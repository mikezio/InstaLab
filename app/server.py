"""
Lightweight web control panel + scheduler for the InstaLab private API pipeline.

Features:
- One-off runs via /api/run
- Interval schedules persisted in Postgres and executed via APScheduler
- Simple control UI at /control (static HTML/JS)
"""

import json
import os
import signal
import shlex
import shutil
import subprocess
import sys
import time
import threading
import urllib.request
from urllib.parse import quote
import ssl
import secrets
import re
import hashlib
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, Response, jsonify, request, send_from_directory, redirect
import requests

from tracker_db import (
    backfill_relationship_events,
    update_run_duration,
    write_run_metadata,
    _init_db as _init_run_db,
)
from unfollow_bot import AuthRequiredError, create_account_guided, ensure_auth_state, unfollow_users, init_login
from db import get_db, get_columns, ddl, is_postgres
from login_store import (
    clear_session_settings,
    clear_challenge_code,
    delete_login,
    disable_login,
    get_login,
    init_login_table,
    list_logins,
    set_challenge_code,
    set_last_error,
    set_last_login,
    set_login_password,
    set_new_password,
    set_session_settings,
    set_totp_seed,
    upsert_login,
)
from recon_store import (
    create_recon_job,
    create_recon_query,
    delete_recon_job,
    finalize_recon_job,
    get_recon_job,
    init_recon_tables,
    list_recon_artifacts,
    list_recon_findings,
    list_recon_jobs,
    mark_recon_job_running,
)
from recon_worker import ReconExecutionError, run_recon_scan
from validation import ValidationError, sanitize_sql_limit
from proxy_utils import load_proxy_from_env
from browser_tracker import browser_storage_path_for_login

# Ensure consistent HOME for session/cache files
os.environ.setdefault("HOME", "/home/stremio")
os.environ.setdefault("TZ", "America/New_York")
try:
    time.tzset()
except (AttributeError, OSError) as e:
    # tzset() may not be available on all platforms
    print(f"Warning: Could not set timezone: {e}", file=sys.stderr)
LOCAL_TZ = ZoneInfo("America/New_York")

if not is_postgres():
    raise RuntimeError("InstaLab is Postgres-only. Set INSTALAB_DB_TYPE=postgres.")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = BASE_DIR / "instalab_runs.db"
# Environment variables are already loaded by db module
ENV_PATH = Path(os.getenv("INSTALAB_ENV", "/srv/secrets/instalab.env"))
COOKIE_DIR = Path(os.getenv("INSTALAB_COOKIE_DIR", "/data/instalab/cookies"))
PRIVATE_SETTINGS_DIR = Path(os.getenv("INSTALAB_PRIVATE_SETTINGS_DIR", "/data/instalab/private"))
# UI redirect configuration: Set INSTALAB_UI_BASE_URL when UI is on custom port or behind proxy
# Leave empty for default behavior (constructs URL from request host + INSTALAB_UI_PORT)
UI_BASE_URL = os.getenv("INSTALAB_UI_BASE_URL", "")

JOB_TMP_DIR = BASE_DIR / "job_runs"


class WorkerRunError(RuntimeError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def _wrap_with_ddtrace(cmd, env, *, service=None):
    ddtrace_run = shutil.which("ddtrace-run")
    if not ddtrace_run:
        return cmd, env
    next_env = dict(env or {})
    if service:
        next_env["DD_SERVICE"] = service
    next_env.setdefault("DD_TRACE_ENABLED", "true")
    next_env.setdefault("DD_DYNAMIC_INSTRUMENTATION_ENABLED", "true")
    return [ddtrace_run, *cmd], next_env


def _ensure_job_tmp_dir():
    try:
        JOB_TMP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            # Ensure group-writable dir for job artifacts.
            JOB_TMP_DIR.chmod(0o2770)
        except PermissionError:
            pass
        if not os.access(JOB_TMP_DIR, os.W_OK | os.X_OK):
            raise PermissionError(f"{JOB_TMP_DIR} not writable")
    except Exception as exc:
        print(f"[init] job_runs not writable: {exc}", file=sys.stderr)


_ensure_job_tmp_dir()


def _cleanup_old_job_dirs():
    """Clean up job directories older than 24 hours."""
    try:
        if not JOB_TMP_DIR.exists():
            return
        now = time.time()
        max_age_seconds = 24 * 3600  # 24 hours
        for item in JOB_TMP_DIR.iterdir():
            if not item.is_dir():
                continue
            try:
                # Check if directory is older than max_age
                mtime = item.stat().st_mtime
                if now - mtime > max_age_seconds:
                    shutil.rmtree(item, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass


def _cleanup_recon_artifacts():
    try:
        retention_days = int(_get_config_value("recon_artifact_retention_days", 30) or 30)
    except Exception:
        retention_days = 30
    if retention_days <= 0:
        return

    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=retention_days)
    cutoff_iso = cutoff.astimezone(ZoneInfo("UTC")).isoformat()
    active_recon_ids = {
        jid
        for jid, meta in list(RECON_META.items())
        if meta.get("state") in {"queued", "running"}
    }

    conn = _get_db()
    try:
        rows = conn.execute(
            """
            SELECT id
            FROM recon_jobs
            WHERE created_at < ?
              AND status IN ('success', 'error', 'cancelled')
            """,
            (cutoff_iso,),
        ).fetchall()
        for row in rows:
            job_id = str(row[0])
            if job_id in active_recon_ids:
                continue
            job_dir = JOB_TMP_DIR / f"recon_{job_id}"
            shutil.rmtree(job_dir, ignore_errors=True)
            conn.execute("DELETE FROM recon_artifacts WHERE recon_job_id = ?", (job_id,))
            conn.execute("UPDATE recon_jobs SET raw_output_path = NULL WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()


def _ensure_cookie_dir():
    global COOKIE_DIR
    try:
        COOKIE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            COOKIE_DIR.chmod(0o2770)
        except PermissionError:
            pass
    except (PermissionError, OSError) as exc:
        # If we can't create the configured directory (e.g., /data/instalab/cookies),
        # fall back to using a local directory in the app folder
        print(f"[init] cookie dir not writable: {exc}", file=sys.stderr)
        print(f"[init] Falling back to local cookie directory", file=sys.stderr)
        COOKIE_DIR = BASE_DIR / "cookies"
        try:
            COOKIE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[init] Failed to create fallback cookie dir: {e}", file=sys.stderr)


_ensure_cookie_dir()


PRIVATE_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]+$")


def _safe_private_name(value: str) -> str:
    value = (value or "").strip()
    if PRIVATE_USERNAME_RE.match(value):
        return value
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value) or "login"


def _private_settings_path(login_username: str) -> Path:
    return PRIVATE_SETTINGS_DIR / f"{_safe_private_name(login_username)}.json"


def _ensure_private_settings_dir():
    try:
        PRIVATE_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            PRIVATE_SETTINGS_DIR.chmod(0o2770)
        except PermissionError:
            pass
    except (PermissionError, OSError) as exc:
        print(f"[init] private settings dir not writable: {exc}", file=sys.stderr)


_ensure_private_settings_dir()


def _remove_private_settings(login_username: str) -> bool:
    path = _private_settings_path(login_username)
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except Exception:
        return False


def _private_session_exists(login_username: str) -> bool:
    entry = get_login(login_username, include_secrets=False)
    if entry and entry.get("session_settings"):
        return True
    return _private_settings_path(login_username).exists()


RUN_LOGIN_MODE_ALLOWED = {"auto", "session_only", "password", "anonymous"}
RUN_LOGIN_MODE_ALIASES = {
    "session": "session_only",
    "session-only": "session_only",
    "password-only": "password",
    "anon": "anonymous",
    "public": "anonymous",
    "no_login": "anonymous",
    "no-login": "anonymous",
}

SCRAPER_BACKEND_ALLOWED = {"private", "browser"}
SCRAPER_BACKEND_ALIASES = {
    "instagrapi": "private",
    "ingrapi": "private",
    "private_api": "private",
    "private-api": "private",
    "osintgram": "private",
    "guided_browser": "browser",
    "playwright": "browser",
}

RUN_BACKEND_TUNING_PROFILES = {
    # Safer cadence for instagrapi/private API to reduce throttling risk.
    "private": {
        "run_http_timeout_seconds": 60.0,
        "run_request_timeout": 60.0,
        "run_private_request_sleep_seconds": 0.8,
        "run_item_delay_min": 0.6,
        "run_item_delay_max": 1.4,
        "run_initial_fetch_delay_seconds": 6.0,
        "run_pause_every_min": 120,
        "run_pause_every_max": 180,
        "run_pause_seconds_min": 20.0,
        "run_pause_seconds_max": 45.0,
        "run_rate_limit_cooldown_seconds": 3600,
    },
    # Browser collector can run faster while keeping basic jitter.
    "browser": {
        "run_http_timeout_seconds": 60.0,
        "run_request_timeout": 60.0,
        "run_private_request_sleep_seconds": 0.0,
        "run_item_delay_min": 0.25,
        "run_item_delay_max": 0.75,
        "run_initial_fetch_delay_seconds": 1.0,
        "run_pause_every_min": 0,
        "run_pause_every_max": 0,
        "run_pause_seconds_min": 0.0,
        "run_pause_seconds_max": 0.0,
        "run_rate_limit_cooldown_seconds": 1800,
    },
}


def _normalize_run_login_mode(value: str | None) -> str:
    mode = str(value or "auto").strip().lower()
    mode = RUN_LOGIN_MODE_ALIASES.get(mode, mode)
    if mode not in RUN_LOGIN_MODE_ALLOWED:
        return "auto"
    return mode


def _is_anonymous_run_login_mode(value: str | None) -> bool:
    return _normalize_run_login_mode(value) == "anonymous"


def _session_stale_threshold() -> int:
    try:
        from private_api_tracker import SESSION_STALE_THRESHOLD

        value = int(SESSION_STALE_THRESHOLD)
    except Exception:
        value = 3
    return max(1, value)


def _normalize_scraper_backend(value: str | None) -> str:
    backend = str(value or "browser").strip().lower()
    backend = SCRAPER_BACKEND_ALIASES.get(backend, backend)
    if backend not in SCRAPER_BACKEND_ALLOWED:
        return "browser"
    return backend


def _is_private_backend_name(value: str | None) -> bool:
    return _normalize_scraper_backend(value) == "private"


def _backend_tuning_profile(value: str | None) -> dict:
    backend = _normalize_scraper_backend(value)
    profile = RUN_BACKEND_TUNING_PROFILES.get(backend, {})
    return dict(profile)

CONFIG_DEFAULTS = {
    "run_stall_seconds": int(os.getenv("RUN_STALL_SECONDS", "1200")),
    "run_max_seconds": int(os.getenv("RUN_MAX_SECONDS", "10800")),
    # Legacy: run_request_timeout used to be treated as a request timeout, but instagrapi uses
    # Client.request_timeout as a per-request sleep. We keep the legacy key, but the pipeline now
    # uses run_http_timeout_seconds (real HTTP timeout) + run_private_request_sleep_seconds (sleep).
    "run_http_timeout_seconds": float(os.getenv("RUN_HTTP_TIMEOUT_SECONDS", os.getenv("RUN_REQUEST_TIMEOUT", "30"))),
    "run_private_request_sleep_seconds": float(os.getenv("RUN_PRIVATE_REQUEST_SLEEP_SECONDS", "0")),
    "run_request_timeout": float(os.getenv("RUN_REQUEST_TIMEOUT", "600")),
    "run_item_delay_min": float(os.getenv("RUN_ITEM_DELAY_MIN", "0")),
    "run_item_delay_max": float(os.getenv("RUN_ITEM_DELAY_MAX", "0")),
    "run_fetch_order": str(os.getenv("RUN_FETCH_ORDER", "followers_first")).strip().lower(),
    "run_initial_fetch_delay_seconds": float(os.getenv("RUN_INITIAL_FETCH_DELAY_SECONDS", "8")),
    "run_pause_every_min": int(os.getenv("RUN_PAUSE_EVERY_MIN", "0")),
    "run_pause_every_max": int(os.getenv("RUN_PAUSE_EVERY_MAX", "0")),
    "run_pause_seconds_min": float(os.getenv("RUN_PAUSE_SECONDS_MIN", "0")),
    "run_pause_seconds_max": float(os.getenv("RUN_PAUSE_SECONDS_MAX", "0")),
    "run_pre_login_flow": os.getenv("RUN_PRE_LOGIN_FLOW", "false").lower() in {"1", "true", "yes", "on"},
    "run_post_login_flow": os.getenv("RUN_POST_LOGIN_FLOW", "false").lower() in {"1", "true", "yes", "on"},
    "run_rate_limit_cooldown_seconds": int(os.getenv("RUN_RATE_LIMIT_COOLDOWN_SECONDS", "900")),
    "run_trace_enabled": os.getenv("RUN_TRACE_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
    "run_profile_only": os.getenv("RUN_PROFILE_ONLY", "false").lower() in {"1", "true", "yes", "on"},
    "run_login_mode": _normalize_run_login_mode(os.getenv("RUN_LOGIN_MODE", "auto")),
    "run_scraper_backend": _normalize_scraper_backend(os.getenv("INSTALAB_SCRAPER_BACKEND", "browser")),
    "private_device_settings_json": os.getenv("INSTALAB_PRIVATE_DEVICE_SETTINGS_JSON", ""),
    "private_user_agent": os.getenv("INSTALAB_PRIVATE_USER_AGENT", ""),
    "proxy_enabled": os.getenv("INSTALAB_PROXY_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
    "proxy_provider": os.getenv("INSTALAB_PROXY_PROVIDER", "decodo"),
    "proxy_access_mode": os.getenv("INSTALAB_PROXY_ACCESS_MODE", "native"),
    "proxy_host": os.getenv("INSTALAB_PROXY_HOST", "gate.decodo.com"),
    "proxy_port": int(os.getenv("INSTALAB_PROXY_PORT", "7000")),
    "proxy_username": os.getenv("INSTALAB_PROXY_USERNAME", ""),
    "proxy_username_pool": os.getenv("INSTALAB_PROXY_USERNAME_POOL", ""),
    "proxy_password": os.getenv("INSTALAB_PROXY_PASSWORD", ""),
    "unfollow_max_per_run": 25,
    "unfollow_delay_min": 25,
    "unfollow_delay_max": 45,
    "unfollow_dry_run_default": False,
    "ui_timezone": os.getenv("UI_TIMEZONE", "America/New_York"),
    "schedule_default_frequency": "once_daily",
    "schedule_default_time1": "09:00",
    "schedule_default_time2": "21:00",
    "schedule_default_weekday": 1,
    "monitor_enabled": True,
    "monitor_interval_minutes": int(os.getenv("MONITOR_INTERVAL_MINUTES", "120")),
    "monitor_threshold_delta": int(os.getenv("MONITOR_THRESHOLD_DELTA", "4")),
    "monitor_min_gap_minutes": int(os.getenv("MONITOR_MIN_GAP_MINUTES", "180")),
    "monitor_login_username": os.getenv("MONITOR_LOGIN_USERNAME", ""),
    "recon_enabled": os.getenv("RECON_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
    "recon_max_concurrency": int(os.getenv("RECON_MAX_CONCURRENCY", "2")),
    "recon_queue_limit": int(os.getenv("RECON_QUEUE_LIMIT", "20")),
    "recon_timeout_seconds": int(os.getenv("RECON_TIMEOUT_SECONDS", "240")),
    "recon_artifact_retention_days": int(os.getenv("RECON_ARTIFACT_RETENTION_DAYS", "30")),
    "recon_blackbird_ai_enabled": os.getenv("RECON_BLACKBIRD_AI_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
    "recon_blackbird_no_nsfw": os.getenv("RECON_BLACKBIRD_NO_NSFW", "true").lower() in {"1", "true", "yes", "on"},
    "recon_blackbird_cmd": os.getenv("RECON_BLACKBIRD_CMD", "python /app/scripts/blackbird_proxy.py"),
    "recon_blackbird_results_dir": os.getenv("RECON_BLACKBIRD_RESULTS_DIR", "/tmp/instalab-blackbird/results"),
    "recon_phoneinfoga_enabled": os.getenv("RECON_PHONEINFOGA_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
    "recon_phoneinfoga_cmd": os.getenv("RECON_PHONEINFOGA_CMD", "/usr/local/bin/phoneinfoga"),
    "whatsapp_enabled": os.getenv("INSTALAB_WHATSAPP_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
    "whatsapp_command_enabled": os.getenv("INSTALAB_WHATSAPP_COMMAND_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
    "whatsapp_notify_enabled": os.getenv("INSTALAB_WHATSAPP_NOTIFY_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
    "whatsapp_verify_token": os.getenv("INSTALAB_WHATSAPP_VERIFY_TOKEN", ""),
    "whatsapp_access_token": os.getenv("INSTALAB_WHATSAPP_ACCESS_TOKEN", ""),
    "whatsapp_phone_number_id": os.getenv("INSTALAB_WHATSAPP_PHONE_NUMBER_ID", ""),
    "whatsapp_api_version": os.getenv("INSTALAB_WHATSAPP_API_VERSION", "v22.0"),
    "whatsapp_allowlist": os.getenv("INSTALAB_WHATSAPP_ALLOWLIST", ""),
    "whatsapp_notify_to": os.getenv("INSTALAB_WHATSAPP_NOTIFY_TO", ""),
}
CONFIG_SCHEMA = {
    "run_stall_seconds": {"type": "int", "min": 60, "max": 21600},
    "run_max_seconds": {"type": "int", "min": 600, "max": 43200},
    "run_http_timeout_seconds": {"type": "float", "min": 5, "max": 3600},
    "run_private_request_sleep_seconds": {"type": "float", "min": 0.0, "max": 2.0},
    "run_request_timeout": {"type": "float", "min": 10, "max": 3600},
    "run_item_delay_min": {"type": "float", "min": 0.0, "max": 10.0},
    "run_item_delay_max": {"type": "float", "min": 0.0, "max": 10.0},
    "run_fetch_order": {"type": "str", "allowed": {"followers_first", "following_first"}},
    "run_initial_fetch_delay_seconds": {"type": "float", "min": 0.0, "max": 120.0},
    "run_pause_every_min": {"type": "int", "min": 0, "max": 1000},
    "run_pause_every_max": {"type": "int", "min": 0, "max": 1000},
    "run_pause_seconds_min": {"type": "float", "min": 0.0, "max": 300.0},
    "run_pause_seconds_max": {"type": "float", "min": 0.0, "max": 300.0},
    "run_pre_login_flow": {"type": "bool"},
    "run_post_login_flow": {"type": "bool"},
    "run_rate_limit_cooldown_seconds": {"type": "int", "min": 60, "max": 86400},
    "run_trace_enabled": {"type": "bool"},
    "run_profile_only": {"type": "bool"},
    "run_login_mode": {"type": "str", "allowed": RUN_LOGIN_MODE_ALLOWED},
    "run_scraper_backend": {"type": "str", "allowed": SCRAPER_BACKEND_ALLOWED},
    "private_device_settings_json": {"type": "str"},
    "private_user_agent": {"type": "str"},
    "proxy_enabled": {"type": "bool"},
    "proxy_provider": {"type": "str"},
    "proxy_access_mode": {"type": "str", "allowed": {"native"}},
    "proxy_host": {"type": "str"},
    "proxy_port": {"type": "int", "min": 1, "max": 65535},
    "proxy_username": {"type": "str"},
    "proxy_username_pool": {"type": "str"},
    "proxy_password": {"type": "str"},
    "unfollow_max_per_run": {"type": "int", "min": 1, "max": 500},
    "unfollow_delay_min": {"type": "int", "min": 1, "max": 600},
    "unfollow_delay_max": {"type": "int", "min": 1, "max": 900},
    "unfollow_dry_run_default": {"type": "bool"},
    "ui_timezone": {"type": "str"},
    "schedule_default_frequency": {
        "type": "str",
        "allowed": {"once_daily", "twice_daily", "every_other_day", "weekly"},
    },
    "schedule_default_time1": {"type": "time"},
    "schedule_default_time2": {"type": "time"},
    "schedule_default_weekday": {"type": "int", "min": 0, "max": 6},
    "monitor_enabled": {"type": "bool"},
    "monitor_interval_minutes": {"type": "int", "min": 30, "max": 720},
    "monitor_threshold_delta": {"type": "int", "min": 1, "max": 200},
    "monitor_min_gap_minutes": {"type": "int", "min": 60, "max": 1440},
    "monitor_login_username": {"type": "str"},
    "recon_enabled": {"type": "bool"},
    "recon_max_concurrency": {"type": "int", "min": 1, "max": 8},
    "recon_queue_limit": {"type": "int", "min": 1, "max": 500},
    "recon_timeout_seconds": {"type": "int", "min": 15, "max": 1800},
    "recon_artifact_retention_days": {"type": "int", "min": 0, "max": 3650},
    "recon_blackbird_ai_enabled": {"type": "bool"},
    "recon_blackbird_no_nsfw": {"type": "bool"},
    "recon_blackbird_cmd": {"type": "str"},
    "recon_blackbird_results_dir": {"type": "str"},
    "recon_phoneinfoga_enabled": {"type": "bool"},
    "recon_phoneinfoga_cmd": {"type": "str"},
    "whatsapp_enabled": {"type": "bool"},
    "whatsapp_command_enabled": {"type": "bool"},
    "whatsapp_notify_enabled": {"type": "bool"},
    "whatsapp_verify_token": {"type": "str"},
    "whatsapp_access_token": {"type": "str"},
    "whatsapp_phone_number_id": {"type": "str"},
    "whatsapp_api_version": {"type": "str"},
    "whatsapp_allowlist": {"type": "str"},
    "whatsapp_notify_to": {"type": "str"},
}
SENSITIVE_CONFIG_KEYS = {"proxy_password", "whatsapp_access_token", "whatsapp_verify_token"}
CONFIG_CACHE = {"data": {}, "ts": 0.0}
CONFIG_CACHE_TTL = 5.0

# Map login usernames to env prefixes (base accounts)
LOGIN_FILE_PATH = os.getenv("INSTALAB_LOGINS_FILE", "/srv/secrets/instalab-logins.json")
LOGIN_CACHE = {"profiles": [], "lookup": {}, "ts": 0.0}
LOGIN_CACHE_TTL = 5.0

# Cache backend availability check (since imports are expensive in health checks)
_BACKEND_HEALTH_CACHE = {"backend": None, "status": None, "ts": 0.0}
_BACKEND_HEALTH_CACHE_TTL = 60.0  # Cache for 1 minute

BASE_LOGIN_PROFILES = [
    {
        "login_username": os.getenv("MZIO_LOGIN_USERNAME", "mzio"),
        "prefix": "MZIO",
        "cookie_file": os.getenv("MZIO_COOKIE_FILE", "cookies_mzio.txt"),
        "db_path": os.getenv("MZIO_DB_PATH", str(DB_PATH_DEFAULT)),
        "source": "env",
    },
    {
        "login_username": os.getenv("DJ_LOGIN_USERNAME", "mystiquethewolfdog"),
        "prefix": "DJ",
        "cookie_file": os.getenv("DJ_COOKIE_FILE", "cookies_mystique.txt"),
        "db_path": os.getenv("DJ_DB_PATH", str(DB_PATH_DEFAULT)),
        "source": "env",
    },
]

BLOCKED_LOGIN_USERNAMES = {"mzio"}
_extra_blocked = os.getenv("INSTALAB_BLOCKED_LOGINS", "")
for _name in _extra_blocked.split(","):
    _name = _name.strip().lower()
    if _name:
        BLOCKED_LOGIN_USERNAMES.add(_name)


def _is_blocked_login(login_username: str | None) -> bool:
    if not login_username:
        return False
    return login_username.strip().lower() in BLOCKED_LOGIN_USERNAMES


def _read_login_file():
    path = Path(LOGIN_FILE_PATH)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to read login file {path}: {e}", file=sys.stderr)
        return []
    if isinstance(data, dict):
        entries = data.get("logins") or []
    elif isinstance(data, list):
        entries = data
    else:
        entries = []
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        username = (entry.get("login_username") or entry.get("username") or "").strip()
        password = (entry.get("login_password") or entry.get("password") or "").strip()
        disabled = bool(entry.get("disabled"))
        if not username:
            continue
        out.append(
            {
                "login_username": username,
                "login_password": password or None,
                "disabled": disabled,
                "cookie_file": (entry.get("cookie_file") or "").strip() or None,
                "db_path": (entry.get("db_path") or "").strip() or None,
                "source": "file",
            }
        )
    return out


def _write_login_file(logins):
    path = Path(LOGIN_FILE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"logins": logins}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    try:
        os.chmod(path, 0o660)
    except (OSError, PermissionError) as e:
        print(f"Warning: Could not set permissions on {path}: {e}", file=sys.stderr)


def _seed_logins_from_env_and_file():
    """Seed login_accounts table from env + legacy logins file if empty."""
    try:
        existing = {entry.get("login_username") for entry in list_logins(include_secrets=False)}
    except Exception:
        existing = set()
    if not existing:
        for base in BASE_LOGIN_PROFILES:
            username = (base.get("login_username") or "").strip()
            if not username:
                continue
            prefix = base.get("prefix") or ""
            password = os.getenv(f"{prefix}_LOGIN_PASSWORD") if prefix else None
            try:
                upsert_login(
                    login_username=username,
                    login_password=password,
                    cookie_file=base.get("cookie_file"),
                    source="env",
                )
            except Exception as exc:
                print(f"Warning: Could not seed env login {username}: {exc}", file=sys.stderr)
        for entry in _read_login_file():
            username = (entry.get("login_username") or "").strip()
            if not username:
                continue
            try:
                upsert_login(
                    login_username=username,
                    login_password=entry.get("login_password"),
                    cookie_file=entry.get("cookie_file"),
                    disabled=bool(entry.get("disabled")),
                    source="file",
                )
            except Exception as exc:
                print(f"Warning: Could not seed file login {username}: {exc}", file=sys.stderr)
    else:
        # Ensure env/file entries are present if missing
        for base in BASE_LOGIN_PROFILES:
            username = (base.get("login_username") or "").strip()
            if not username or username in existing:
                continue
            prefix = base.get("prefix") or ""
            password = os.getenv(f"{prefix}_LOGIN_PASSWORD") if prefix else None
            try:
                upsert_login(
                    login_username=username,
                    login_password=password,
                    cookie_file=base.get("cookie_file"),
                    source="env",
                )
            except Exception as exc:
                print(f"Warning: Could not add env login {username}: {exc}", file=sys.stderr)
        for entry in _read_login_file():
            username = (entry.get("login_username") or "").strip()
            if not username or username in existing:
                continue
            try:
                upsert_login(
                    login_username=username,
                    login_password=entry.get("login_password"),
                    cookie_file=entry.get("cookie_file"),
                    disabled=bool(entry.get("disabled")),
                    source="file",
                )
            except Exception as exc:
                print(f"Warning: Could not add file login {username}: {exc}", file=sys.stderr)


def _load_login_profiles(force: bool = False):
    if not force and (time.time() - LOGIN_CACHE.get("ts", 0.0)) < LOGIN_CACHE_TTL:
        return
    _seed_logins_from_env_and_file()
    profiles = []
    seen = set()
    for entry in list_logins(include_secrets=False):
        username = (entry.get("login_username") or "").strip()
        if not username or username in seen:
            continue
        if _is_blocked_login(username):
            try:
                disable_login(username, True)
            except Exception:
                pass
            continue
        if entry.get("disabled"):
            continue
        profiles.append(
            {
                "login_username": username,
                "prefix": None,
                "cookie_file": entry.get("cookie_file") or f"cookies_{username}.txt",
                "db_path": str(DB_PATH_DEFAULT),
                "source": entry.get("source") or "db",
            }
        )
        seen.add(username)
    LOGIN_CACHE["profiles"] = profiles
    LOGIN_CACHE["lookup"] = {p["login_username"]: p for p in profiles}
    LOGIN_CACHE["ts"] = time.time()


def _get_login_profiles(force: bool = False):
    _load_login_profiles(force=force)
    return LOGIN_CACHE["profiles"]


def _get_login_lookup(force: bool = False):
    _load_login_profiles(force=force)
    return LOGIN_CACHE["lookup"]


def _clear_login_cache():
    LOGIN_CACHE["ts"] = 0.0


def _get_monitor_login():
    pref = _get_config_value("monitor_login_username", None)
    lookup = _get_login_lookup()
    if pref and pref in lookup:
        return pref
    profiles = _get_login_profiles()
    if profiles:
        return profiles[0]["login_username"]
    return None


def _resolve_cookie_file(cookie_file):
    if not cookie_file:
        return None
    path = Path(str(cookie_file))
    if not path.is_absolute():
        path = COOKIE_DIR / path
    return str(path)




def _get_db():
    conn = get_db()
    # Add schema migrations if needed
    try:
        cols = get_columns(conn, "runs")
        if "duration_seconds" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN duration_seconds INTEGER")
        if "followers_fetch_seconds" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN followers_fetch_seconds INTEGER")
        if "followees_fetch_seconds" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN followees_fetch_seconds INTEGER")
        if "followers_rate" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN followers_rate REAL")
        if "followees_rate" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN followees_rate REAL")
        if "confidence_score" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN confidence_score INTEGER")
        if "confidence_flag" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN confidence_flag TEXT")
        conn.commit()
    except Exception as e:
        # Schema migration errors are logged but non-fatal
        print(f"Warning: Schema migration failed: {e}", file=sys.stderr)
        try:
            conn.rollback()
        except Exception:
            pass
    return conn


def _list_tables(conn) -> set[str]:
    try:
        cur = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
        return {row[0] for row in cur.fetchall()}
    except Exception as e:
        print(f"Warning: Could not list tables: {e}", file=sys.stderr)
        return set()


def _merge_health_status(checks: dict) -> str:
    statuses = [v.get("status") for v in checks.values()]
    if any(s == "fail" for s in statuses):
        return "fail"
    if any(s == "degraded" for s in statuses):
        return "degraded"
    return "ok"


def _health_check_db():
    payload = {
        "status": "ok",
        "details": {
            "backend": "postgres" if is_postgres() else "unsupported",
        },
    }
    if not is_postgres():
        payload["status"] = "fail"
        payload["error"] = "unsupported DB backend (Postgres required)"
        return payload
    conn = None
    try:
        conn = _get_db()
        conn.execute("SELECT 1")
        tables = _list_tables(conn)
        required = {
            "runs",
            "run_followers",
            "run_followees",
            "followers_history",
            "followees_history",
            "relationship_events",
            "schedules",
            "count_checks",
            "unfollow_actions",
            "config",
            "login_accounts",
        }
        missing = sorted(required - tables)
        payload["details"]["tables_present"] = sorted(tables)
        if missing:
            payload["status"] = "degraded"
            payload["details"]["missing_tables"] = missing
    except Exception as exc:  # noqa: BLE001
        payload["status"] = "fail"
        payload["error"] = str(exc)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    return payload


def _health_check_files():
    def _path_state(path: Path, *, expect_dir=False, writable=False):
        exists = path.exists()
        readable = os.access(path, os.R_OK) if exists else False
        writable_ok = os.access(path, os.W_OK | os.X_OK) if (exists and expect_dir and writable) else (
            os.access(path, os.W_OK) if (exists and writable) else False
        )
        return {
            "path": str(path),
            "exists": exists,
            "readable": readable,
            "writable": writable_ok if writable else None,
        }

    checks = {
        "env": _path_state(ENV_PATH, writable=False),
        "logins_file": _path_state(Path(LOGIN_FILE_PATH), writable=True),
        "cookie_dir": _path_state(COOKIE_DIR, expect_dir=True, writable=True),
        "private_settings_dir": _path_state(PRIVATE_SETTINGS_DIR, expect_dir=True, writable=True),
        "job_tmp_dir": _path_state(JOB_TMP_DIR, expect_dir=True, writable=True),
        "data_dir": _path_state(Path("/data/instalab"), expect_dir=True, writable=True),
        "encryption_key": {"present": bool(os.getenv("INSTALAB_ENCRYPTION_KEY") or os.getenv("INSTALAB_FERNET_KEY"))},
    }
    status = "ok"
    if not checks["env"]["exists"] or not checks["env"]["readable"]:
        status = "degraded"
    if not checks["job_tmp_dir"]["exists"] or not checks["job_tmp_dir"]["writable"]:
        status = "fail"
    if not checks["cookie_dir"]["exists"] or not checks["cookie_dir"]["writable"]:
        status = "degraded"
    if not checks["private_settings_dir"]["exists"] or not checks["private_settings_dir"]["writable"]:
        status = "degraded"
    if not checks["encryption_key"]["present"]:
        status = "degraded"
    return {"status": status, "details": checks}


def _health_check_scheduler():
    running = bool(getattr(scheduler, "running", False))
    state = getattr(scheduler, "state", None)
    jobs = scheduler.get_jobs() if running else []
    status = "ok" if running else "fail"
    return {
        "status": status,
        "details": {
            "running": running,
            "state": state,
            "job_count": len(jobs),
            "jobs": [j.id for j in jobs],
        },
    }


def _health_check_monitor():
    enabled = _parse_bool(_get_config_value("monitor_enabled", True))
    interval = int(_get_config_value("monitor_interval_minutes", 120) or 120)
    job = scheduler.get_job("monitor_counts") if enabled else None
    last_check = None
    last_target = None
    age_minutes = None
    conn = None
    try:
        conn = _get_db()
        cur = conn.execute(
            "SELECT target_username, timestamp FROM count_checks ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            last_target = row[0]
            last_check = row[1]
            try:
                ts = datetime.fromisoformat(last_check)
                age_minutes = int((datetime.now(LOCAL_TZ) - ts).total_seconds() / 60)
            except Exception:
                age_minutes = None
    except Exception:
        pass
    finally:
        if conn:
            conn.close()
    status = "ok"
    if enabled and not job:
        status = "degraded"
    if enabled and age_minutes is not None and age_minutes > max(interval * 2, interval + 30):
        status = "degraded"
    return {
        "status": status,
        "details": {
            "enabled": enabled,
            "interval_minutes": interval,
            "job_scheduled": bool(job),
            "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
            "last_check_target": last_target,
            "last_check_timestamp": last_check,
            "last_check_age_minutes": age_minutes,
        },
    }


def _health_check_sessions():
    profiles = _get_login_profiles()
    missing_auth = []
    details = []
    for p in profiles:
        username = p.get("login_username")
        private_settings = _private_settings_path(username)
        private_session_exists = private_settings.exists()
        private_session_mtime = None
        if private_session_exists:
            try:
                private_session_mtime = private_settings.stat().st_mtime
            except Exception:
                private_session_mtime = None
        prefix = p.get("prefix")
        has_password = bool(p.get("login_password") or (prefix and os.getenv(f"{prefix}_LOGIN_PASSWORD")))
        ready = has_password or private_session_exists
        if not ready:
            missing_auth.append(username)
        details.append(
            {
                "login_username": username,
                "source": p.get("source", "env"),
                "private_session_exists": private_session_exists,
                "private_session_mtime": private_session_mtime,
                "has_password": has_password,
                "ready": ready,
            }
        )
    status = "ok"
    if not profiles:
        status = "degraded"
    if missing_auth:
        status = "degraded"
    return {
        "status": status,
        "details": {
            "logins_total": len(profiles),
            "missing_auth": missing_auth,
            "logins": details,
            "blocked_logins": sorted(BLOCKED_LOGIN_USERNAMES),
        },
    }


def _health_check_scraper():
    backend = _normalize_scraper_backend(
        _get_config_value(
            "run_scraper_backend",
            os.getenv("INSTALAB_SCRAPER_BACKEND") or os.getenv("SCRAPER_BACKEND") or "browser",
        )
    )
    
    # Check cache first
    now = time.time()
    if (_BACKEND_HEALTH_CACHE["backend"] == backend and 
        _BACKEND_HEALTH_CACHE["ts"] > 0 and 
        now - _BACKEND_HEALTH_CACHE["ts"] < _BACKEND_HEALTH_CACHE_TTL):
        return _BACKEND_HEALTH_CACHE["status"]
    
    details = {"backend": backend}
    status = "ok"
    if _is_private_backend_name(backend):
        try:
            import instagrapi  # noqa: F401
            details["private_api"] = "ok"
        except Exception as exc:  # noqa: BLE001
            status = "fail"
            details["private_api"] = f"error: {exc}"
    elif backend == "browser":
        try:
            import playwright  # noqa: F401
            details["browser"] = "ok"
        except Exception as exc:  # noqa: BLE001
            status = "fail"
            details["browser"] = f"error: {exc}"
    else:
        status = "fail"
        details["backend"] = f"unsupported backend: {backend}"
    
    result = {"status": status, "details": details}
    # Update cache
    _BACKEND_HEALTH_CACHE["backend"] = backend
    _BACKEND_HEALTH_CACHE["status"] = result
    _BACKEND_HEALTH_CACHE["ts"] = now
    return result


def _is_private_backend() -> bool:
    backend = _normalize_scraper_backend(
        _get_config_value(
            "run_scraper_backend",
            os.getenv("INSTALAB_SCRAPER_BACKEND") or os.getenv("SCRAPER_BACKEND") or "browser",
        )
    )
    return _is_private_backend_name(backend)


def _health_check_runs():
    run_max = int(_get_config_value("run_max_seconds", 10800) or 10800)
    active = []
    queued = []
    stalled = []
    now = datetime.now(LOCAL_TZ)
    for login, job in list(ACTIVE_JOBS.items()):
        started_at = job.get("started_at")
        elapsed = None
        try:
            started = datetime.fromisoformat(started_at)
            elapsed = int((now - started).total_seconds())
        except Exception:
            elapsed = None
        item = {
            "login_username": login,
            "target_username": job.get("target_username"),
            "started_at": started_at,
            "elapsed_seconds": elapsed,
        }
        active.append(item)
        if elapsed is not None and elapsed > run_max:
            stalled.append(item)

    queued = _list_run_jobs_by_status("queued", limit=500)

    status = "ok"
    if stalled:
        status = "degraded"
    return {
        "status": status,
        "details": {
            "active_jobs": active,
            "queued_jobs": queued,
            "stalled_jobs": stalled,
        },
    }


def _health_check_unfollow():
    job = dict(UNFOLLOW_JOB)
    state = job.get("state")
    status = "ok"
    if state == "error":
        status = "degraded"
    return {"status": status, "details": job}


def _health_check_recon():
    cfg = _get_config()
    enabled = _parse_bool(cfg.get("recon_enabled", True))
    blackbird_cmd = str(cfg.get("recon_blackbird_cmd") or "").strip()
    phoneinfoga_cmd = str(cfg.get("recon_phoneinfoga_cmd") or "").strip()
    blackbird_exec = shlex.split(blackbird_cmd)[0] if blackbird_cmd else ""
    phoneinfoga_exec = shlex.split(phoneinfoga_cmd)[0] if phoneinfoga_cmd else ""
    counts = _recon_state_counts()
    max_concurrency = int(cfg.get("recon_max_concurrency", 2) or 2)
    queue_limit = int(cfg.get("recon_queue_limit", 20) or 20)
    details = {
        "enabled": enabled,
        "running_jobs": counts["running"],
        "queued_jobs": counts["queued"],
        "pending_jobs": counts["pending"],
        "max_concurrency": max_concurrency,
        "queue_limit": queue_limit,
        "blackbird_executable": bool(shutil.which(blackbird_exec)) if blackbird_exec else False,
        "phoneinfoga_executable": bool(shutil.which(phoneinfoga_exec)) if phoneinfoga_exec else False,
    }
    status = "ok"
    if not enabled or not details["blackbird_executable"]:
        status = "degraded"
    return {"status": status, "details": details}


def _blackbird_ai_key_path(cfg: dict) -> Path:
    results_dir = str(cfg.get("recon_blackbird_results_dir") or "/tmp/instalab-blackbird/results").strip()
    runtime_dir = Path(results_dir).resolve().parent
    return runtime_dir / ".ai_key.json"


def _init_config_table():
    conn = _get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _parse_bool(val):
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if val is None:
        return False
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _validate_time(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("time must be HH:MM")
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("time must be HH:MM")
    h, m = parts
    if len(h) != 2 or len(m) != 2:
        raise ValueError("time must be HH:MM")
    hh = int(h)
    mm = int(m)
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        raise ValueError("time must be HH:MM")
    return f"{hh:02d}:{mm:02d}"


def _coerce_config_value(key, value, strict=False):
    schema = CONFIG_SCHEMA.get(key)
    if not schema:
        return value
    ctype = schema.get("type")
    try:
        if ctype == "int":
            iv = int(value)
            if "min" in schema and iv < schema["min"]:
                raise ValueError(f"{key} must be >= {schema['min']}")
            if "max" in schema and iv > schema["max"]:
                raise ValueError(f"{key} must be <= {schema['max']}")
            return iv
        if ctype == "float":
            fv = float(value)
            if "min" in schema and fv < schema["min"]:
                raise ValueError(f"{key} must be >= {schema['min']}")
            if "max" in schema and fv > schema["max"]:
                raise ValueError(f"{key} must be <= {schema['max']}")
            return fv
        if ctype == "bool":
            return _parse_bool(value)
        if ctype == "time":
            return _validate_time(value)
        if ctype == "str":
            if key == "run_login_mode":
                sv = _normalize_run_login_mode(value)
            elif key == "run_scraper_backend":
                sv = _normalize_scraper_backend(value)
            else:
                sv = str(value).strip()
            allowed = schema.get("allowed")
            if allowed and sv not in allowed:
                raise ValueError(f"{key} must be one of {', '.join(sorted(allowed))}")
            return sv
    except Exception:
        if strict:
            raise
    return CONFIG_DEFAULTS.get(key)


def _serialize_config_value(key, value) -> str:
    schema = CONFIG_SCHEMA.get(key)
    if schema and schema.get("type") == "bool":
        return "true" if bool(value) else "false"
    return str(value)


def _load_config_rows(conn):
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    return {row[0]: row[1] for row in rows}


def _get_config(force=False):
    now = time.time()
    if not force and (now - CONFIG_CACHE["ts"]) < CONFIG_CACHE_TTL:
        return CONFIG_CACHE["data"]
    conn = _get_db()
    try:
        raw = _load_config_rows(conn)
    finally:
        conn.close()
    merged = dict(CONFIG_DEFAULTS)
    for key, val in raw.items():
        if key in CONFIG_DEFAULTS:
            merged[key] = _coerce_config_value(key, val, strict=False)
    # Back-compat: old installs only have run_request_timeout stored. Treat it as the HTTP timeout
    # unless the new key is explicitly present.
    if "run_request_timeout" in raw and "run_http_timeout_seconds" not in raw:
        try:
            merged["run_http_timeout_seconds"] = _coerce_config_value(
                "run_http_timeout_seconds",
                raw.get("run_request_timeout"),
                strict=False,
            )
        except Exception:
            # Best-effort legacy conversion; on any error keep the default
            # value for run_http_timeout_seconds from CONFIG_DEFAULTS.
            pass
    CONFIG_CACHE["data"] = merged
    CONFIG_CACHE["ts"] = now
    return merged


def _mask_config_for_api(cfg: dict) -> dict:
    safe = dict(cfg)
    for key in SENSITIVE_CONFIG_KEYS:
        if key in safe:
            safe.pop(key, None)
            safe[f"{key}_set"] = bool(cfg.get(key))
    return safe


def _get_config_value(key, fallback=None):
    cfg = _get_config()
    if key in cfg:
        return cfg[key]
    if fallback is not None:
        return fallback
    return CONFIG_DEFAULTS.get(key)


def _set_config_values(updates: dict):
    cleaned = {}
    for key, value in updates.items():
        if key not in CONFIG_SCHEMA:
            raise ValueError(f"Unknown config key: {key}")
        cleaned[key] = _coerce_config_value(key, value, strict=True)
    if not cleaned:
        return {}
    conn = _get_db()
    try:
        now = datetime.now(LOCAL_TZ).isoformat()
        for key, value in cleaned.items():
            conn.execute(
                """
                INSERT INTO config (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, _serialize_config_value(key, value), now),
            )
        conn.commit()
    finally:
        conn.close()
    CONFIG_CACHE["ts"] = 0.0
    return cleaned


def _normalize_whatsapp_number(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    keep_plus = raw.startswith("+")
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return ""
    return f"+{digits}" if keep_plus else digits


def _whatsapp_allowlist_set(cfg: dict | None = None) -> set[str]:
    conf = cfg or _get_config()
    raw = str(conf.get("whatsapp_allowlist") or "").strip()
    if not raw:
        return set()
    values = set()
    for item in raw.split(","):
        normalized = _normalize_whatsapp_number(item)
        if normalized:
            values.add(normalized)
    return values


def _whatsapp_is_configured(cfg: dict | None = None) -> bool:
    conf = cfg or _get_config()
    if not _parse_bool(conf.get("whatsapp_enabled", False)):
        return False
    return bool(
        str(conf.get("whatsapp_access_token") or "").strip()
        and str(conf.get("whatsapp_phone_number_id") or "").strip()
    )


def _whatsapp_sender_allowed(sender: str, cfg: dict | None = None) -> bool:
    normalized = _normalize_whatsapp_number(sender)
    allowed = _whatsapp_allowlist_set(cfg)
    if not allowed:
        return True
    return normalized in allowed


def _whatsapp_graph_url(cfg: dict | None = None) -> str:
    conf = cfg or _get_config()
    api_version = str(conf.get("whatsapp_api_version") or "v22.0").strip() or "v22.0"
    phone_number_id = str(conf.get("whatsapp_phone_number_id") or "").strip()
    return f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"


def _whatsapp_send_text(to_number: str, body: str, cfg: dict | None = None) -> tuple[bool, str]:
    conf = cfg or _get_config()
    if not _whatsapp_is_configured(conf):
        return False, "whatsapp integration not configured"
    to_number = _normalize_whatsapp_number(to_number)
    if not to_number:
        return False, "destination number missing"
    access_token = str(conf.get("whatsapp_access_token") or "").strip()
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number.lstrip("+"),
        "type": "text",
        "text": {"preview_url": False, "body": str(body or "").strip()[:4096]},
    }
    try:
        resp = requests.post(
            _whatsapp_graph_url(conf),
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        if resp.status_code >= 400:
            return False, f"graph api {resp.status_code}: {resp.text[:300]}"
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _whatsapp_command_help() -> str:
    return (
        "InstaLab commands:\n"
        "status\n"
        "run <login_username> <target_username>\n"
        "cancel <login_username> <target_username>\n"
        "help"
    )


def _whatsapp_handle_command(sender: str, text: str) -> str:
    cfg = _get_config()
    if not _parse_bool(cfg.get("whatsapp_command_enabled", True)):
        return "WhatsApp commands are disabled."
    if not _whatsapp_sender_allowed(sender, cfg):
        return "Sender not authorized for InstaLab commands."
    command = str(text or "").strip()
    if not command:
        return _whatsapp_command_help()
    tokens = command.split()
    verb = tokens[0].lower()
    if verb in {"help", "/help", "?"}:
        return _whatsapp_command_help()
    if verb in {"status", "/status"}:
        active = len(ACTIVE_JOBS)
        queued = len(_list_run_jobs_by_status("queued", limit=500))
        return f"InstaLab status: active={active}, queued={queued}."
    if verb in {"run", "/run"}:
        if len(tokens) < 3:
            return "Usage: run <login_username> <target_username>"
        login_username = tokens[1].strip()
        target_username = tokens[2].strip().lstrip("@")
        if not login_username or not target_username:
            return "Usage: run <login_username> <target_username>"
        if _is_blocked_login(login_username):
            return f"Blocked login: {login_username}"
        if login_username not in _get_login_lookup():
            return f"Unknown login: {login_username}"
        if _get_run_lock(login_username).locked():
            return "Run already in progress for this login."
        if _is_target_busy(target_username):
            return "Another run is already in progress for this target."
        job_id = _queue_run(login_username, target_username, source="whatsapp")
        return f"Queued run {job_id} for @{target_username} via {login_username}."
    if verb in {"cancel", "/cancel"}:
        if len(tokens) < 3:
            return "Usage: cancel <login_username> <target_username>"
        login_username = tokens[1].strip()
        target_username = tokens[2].strip().lstrip("@")
        if not login_username or not target_username:
            return "Usage: cancel <login_username> <target_username>"
        CANCEL_REQUESTS.add((login_username, target_username))
        return f"Cancel requested for @{target_username} via {login_username}."
    return "Unknown command.\n" + _whatsapp_command_help()


def _whatsapp_notify_run_event(event: str, login_username: str, target_username: str, detail: str) -> None:
    cfg = _get_config()
    if not _parse_bool(cfg.get("whatsapp_notify_enabled", True)):
        return
    to_number = _normalize_whatsapp_number(cfg.get("whatsapp_notify_to"))
    if not to_number:
        return
    body = f"InstaLab {event}\nlogin: {login_username}\ntarget: @{target_username}\n{detail}"
    ok, err = _whatsapp_send_text(to_number, body, cfg)
    if not ok:
        print(f"[whatsapp] notify failed: {err}", file=sys.stderr)


def _build_proxy_server(host: str, port: int) -> str:
    return f"http://{host}:{int(port)}"


def _build_proxy_url(host: str, port: int, username: str | None = None, password: str | None = None) -> str:
    if username and password:
        user_enc = quote(str(username), safe="")
        pass_enc = quote(str(password), safe="")
        return f"http://{user_enc}:{pass_enc}@{host}:{int(port)}"
    return f"http://{host}:{int(port)}"


def _parse_proxy_username_pool(raw: str | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in str(raw or "").split(","):
        candidate = item.strip()
        if not candidate:
            continue
        dedupe_key = candidate.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(candidate)
    return out


def _select_proxy_username_from_pool(
    username_pool: list[str], *, login_username: str | None = None, session_id: str | None = None
) -> str:
    if not username_pool:
        return ""
    login = str(login_username or "").strip().lower()
    if login:
        # Spread active logins across pool entries deterministically before hashing fallback.
        known_logins = sorted(
            {
                str(profile.get("login_username") or "").strip().lower()
                for profile in _get_login_profiles()
                if str(profile.get("login_username") or "").strip()
            }
        )
        if login in known_logins:
            return username_pool[known_logins.index(login) % len(username_pool)]
    selector = str(session_id or login or "default")
    idx = int(hashlib.sha256(selector.encode("utf-8")).hexdigest()[:8], 16) % len(username_pool)
    return username_pool[idx]


 
def _get_proxy_config(session_id: str | None = None, login_username: str | None = None):
    enabled = _parse_bool(_get_config_value("proxy_enabled", False))
    provider = str(_get_config_value("proxy_provider", "decodo") or "decodo").strip().lower()
    access_mode = str(_get_config_value("proxy_access_mode", "native") or "native").strip().lower()
    if access_mode != "native":
        access_mode = "native"
    host = str(_get_config_value("proxy_host", "") or "").strip().rstrip("/")
    if host.startswith("http://"):
        host = host[7:]
    elif host.startswith("https://"):
        host = host[8:]
    port = int(_get_config_value("proxy_port", 7000) or 7000)
    username = str(_get_config_value("proxy_username", "") or "").strip()
    username_pool_raw = str(_get_config_value("proxy_username_pool", "") or "").strip()
    username_pool = _parse_proxy_username_pool(username_pool_raw)
    if username_pool:
        username = _select_proxy_username_from_pool(
            username_pool, login_username=login_username, session_id=session_id
        )
    password = str(_get_config_value("proxy_password", "") or "").strip()
    # Decodo sticky sessions are controlled via username suffix.
    # Example: user-<zone>-country-us-session-<token>
    if provider == "decodo" and username and session_id:
        sid = re.sub(r"[^A-Za-z0-9_-]", "", str(session_id)).strip()
        if sid:
            if "{session}" in username:
                username = username.replace("{session}", sid)
            elif "-session-" not in username:
                username = f"{username}-session-{sid}"
    if not enabled:
        return {"enabled": False}
    if not host or not port:
        return {"enabled": False}
    return {
        "enabled": True,
        "provider": provider,
        "access_mode": access_mode,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "server": _build_proxy_server(host, port),
    }


def _generate_proxy_session_id(*, login_username: str | None = None, length: int = 16) -> str:
    target_len = max(8, int(length))
    if login_username:
        # Keep proxy identity stable per login account across runs.
        normalized = str(login_username).strip().lower()
        # Include proxy username so geo-target changes (country/state/city) rotate sticky session.
        proxy_user = str(_get_config_value("proxy_username", "") or "").strip().lower()
        proxy_pool = str(_get_config_value("proxy_username_pool", "") or "").strip().lower()
        seed = f"{normalized}|{proxy_user}|{proxy_pool}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return digest[:target_len]
    return secrets.token_hex(max(4, target_len // 2))


def _apply_proxy_env(env: dict, *, session_id: str | None = None, login_username: str | None = None):
    proxy = _get_proxy_config(session_id=session_id, login_username=login_username)
    if not proxy.get("enabled"):
        env["INSTALAB_PROXY_ENABLED"] = "false"
        for key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        ):
            env.pop(key, None)
        return
    env["INSTALAB_PROXY_ENABLED"] = "true"
    env["INSTALAB_PROXY_PROVIDER"] = proxy.get("provider", "decodo")
    env["INSTALAB_PROXY_ACCESS_MODE"] = proxy.get("access_mode", "native")
    env["INSTALAB_PROXY_HOST"] = proxy.get("host", "")
    env["INSTALAB_PROXY_PORT"] = str(proxy.get("port", 7000))
    if proxy.get("username"):
        env["INSTALAB_PROXY_USERNAME"] = proxy.get("username")
    if proxy.get("password"):
        env["INSTALAB_PROXY_PASSWORD"] = proxy.get("password")
    host = proxy.get("host") or ""
    port = proxy.get("port")
    if host and port:
        user = proxy.get("username") or ""
        pwd = proxy.get("password") or ""
        proxy_url = _build_proxy_url(host, int(port), user, pwd)
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url


def _proxy_test_url(provider: str) -> str:
    return "https://ip.decodo.com/json"

def _init_unfollow_table():
    conn = _get_db()
    try:
        conn.execute(
            ddl(
                """
                CREATE TABLE IF NOT EXISTS unfollow_actions (
                    id SERIAL PRIMARY KEY,
                    login_username TEXT NOT NULL,
                    target_username TEXT NOT NULL,
                    username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_unfollow_login_target ON unfollow_actions (login_username, target_username)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_unfollow_created ON unfollow_actions (created_at)"
        )
        conn.commit()
    finally:
        conn.close()


def _init_run_tables():
    conn = _get_db()
    try:
        _init_run_db(conn)
        conn.execute(
            ddl(
                """
                CREATE TABLE IF NOT EXISTS run_jobs (
                    job_id TEXT PRIMARY KEY,
                    login_username TEXT NOT NULL,
                    target_username TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    submitted_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    state_reason TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    cooldown_seconds INTEGER,
                    result_json TEXT
                )
                """
            )
        )
        conn.execute(
            ddl(
                """
                CREATE TABLE IF NOT EXISTS run_job_events (
                    id SERIAL PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    details_json TEXT
                )
                """
            )
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_run_jobs_status_submitted ON run_jobs(status, submitted_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_run_jobs_login_submitted ON run_jobs(login_username, submitted_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_run_job_events_job_time ON run_job_events(job_id, event_at)")
        conn.execute(
            """
            UPDATE run_jobs
            SET status = 'error',
                finished_at = ?,
                state_reason = ?,
                error_message = ?
            WHERE status = 'running'
            """,
            (
                datetime.now(LOCAL_TZ).isoformat(),
                "server_restart",
                "job interrupted by server restart",
            ),
        )
        backfill_relationship_events(conn)
        conn.commit()
    finally:
        conn.close()


def _init_recon_tables():
    conn = _get_db()
    try:
        init_recon_tables(conn)
        conn.commit()
    finally:
        conn.close()


def _init_login_tables():
    init_login_table()




def _record_unfollow_action(login_username, target_username, username, action, status, detail=None):
    conn = _get_db()
    try:
        conn.execute(
            """
            INSERT INTO unfollow_actions (login_username, target_username, username, action, status, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                login_username,
                target_username,
                username,
                action,
                status,
                detail,
                datetime.now(LOCAL_TZ).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _parse_snapshot_ts(ts):
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=LOCAL_TZ)
    except Exception:
        try:
            return datetime.fromisoformat(ts)
        except Exception:
            return None


def _get_unfollowed_since(conn, login_username, target_username, since_dt):
    if not since_dt:
        return set()
    rows = conn.execute(
        """
        SELECT username, created_at
        FROM unfollow_actions
        WHERE login_username = ? AND target_username = ? AND action = 'unfollow' AND status = 'success'
        """,
        (login_username, target_username),
    ).fetchall()
    seen = set()
    for row in rows:
        try:
            created = datetime.fromisoformat(row["created_at"])
        except Exception:
            continue
        if created >= since_dt:
            seen.add(row["username"])
    return seen


def _avg_duration_seconds(conn, login_username, target_username, limit=5):
    try:
        rows = conn.execute(
            """
            SELECT duration_seconds
            FROM runs
            WHERE login_username = ? AND target_username = ? AND duration_seconds IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (login_username, target_username, limit),
        ).fetchall()
        vals = [r[0] for r in rows if r[0] is not None]
    except Exception:
        return None
    if not vals:
        return None
    return int(sum(vals) / len(vals))


def _last_totals(conn, target_username):
    try:
        row = conn.execute(
            """
            SELECT followers_count, followees_count
            FROM runs
            WHERE target_username = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_username,),
        ).fetchone()
        if not row:
            return None, None
        return row[0], row[1]
    except Exception:
        return None, None


def _update_run_quality(conn, run_id, login_username, target_username, window=5):
    row = conn.execute(
        "SELECT followers_count, followees_count FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if not row:
        return
    current_followers = row["followers_count"]
    current_followees = row["followees_count"]
    hist = conn.execute(
        """
        SELECT followers_count, followees_count
        FROM runs
        WHERE target_username = ? AND id != ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (target_username, run_id, window),
    ).fetchall()
    if len(hist) < 2:
        conn.execute(
            "UPDATE runs SET confidence_score = ?, confidence_flag = ? WHERE id = ?",
            (100, "insufficient_history", run_id),
        )
        conn.commit()
        return
    avg_f = sum(r["followers_count"] for r in hist) / len(hist)
    avg_fe = sum(r["followees_count"] for r in hist) / len(hist)
    diff_f = abs(current_followers - avg_f) / max(avg_f, 1)
    diff_fe = abs(current_followees - avg_fe) / max(avg_fe, 1)
    severity = max(diff_f, diff_fe)
    score = max(0, int(100 - (severity * 200)))
    flag = "low_confidence" if severity >= 0.25 else "ok"
    conn.execute(
        "UPDATE runs SET confidence_score = ?, confidence_flag = ? WHERE id = ?",
        (score, flag, run_id),
    )
    conn.commit()


def _unfollow_success_rate(conn, login_username, target_username, limit=200):
    rows = conn.execute(
        """
        SELECT status
        FROM unfollow_actions
        WHERE login_username = ? AND target_username = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (login_username, target_username, limit),
    ).fetchall()
    if not rows:
        return None
    total = 0
    success = 0
    for row in rows:
        status = row[0]
        if status in ("success", "skipped", "error"):
            total += 1
            if status == "success":
                success += 1
    return (success / total) if total else None


def _suggest_unfollow_batch(total, success_rate=None):
    if total <= 0:
        return 0
    if total <= 50:
        base = 30
    elif total <= 200:
        base = 20
    elif total <= 500:
        base = 15
    else:
        base = 10
    if success_rate is not None:
        if success_rate < 0.7:
            base = max(5, base - 5)
        elif success_rate > 0.9:
            base = base + 5
    return min(base, total)


def _log_unfollow(msg: str):
    ts = datetime.now(LOCAL_TZ).strftime("%H:%M:%S")
    UNFOLLOW_LOG.insert(0, f"[{ts}] {msg}")
    del UNFOLLOW_LOG[200:]


def _unfollow_snapshot():
    job = dict(UNFOLLOW_JOB)
    started_at = job.get("started_at")
    finished_at = job.get("finished_at")
    elapsed = None
    if started_at:
        try:
            start = datetime.fromisoformat(started_at)
            if job.get("state") in ("running", "cancelling"):
                end = datetime.now(LOCAL_TZ)
            elif finished_at:
                end = datetime.fromisoformat(finished_at)
            else:
                end = datetime.now(LOCAL_TZ)
            elapsed = int((end - start).total_seconds())
        except Exception:
            elapsed = None
    job["elapsed_seconds"] = elapsed
    return job


def _log_account_create(msg: str):
    ts = datetime.now(LOCAL_TZ).strftime("%H:%M:%S")
    ACCOUNT_CREATE_LOG.insert(0, f"[{ts}] {msg}")
    del ACCOUNT_CREATE_LOG[200:]


def _account_create_snapshot():
    job = dict(ACCOUNT_CREATE_JOB)
    started_at = job.get("started_at")
    finished_at = job.get("finished_at")
    elapsed = None
    if started_at:
        try:
            start = datetime.fromisoformat(started_at)
            if job.get("state") in ("running", "cancelling"):
                end = datetime.now(LOCAL_TZ)
            elif finished_at:
                end = datetime.fromisoformat(finished_at)
            else:
                end = datetime.now(LOCAL_TZ)
            elapsed = int((end - start).total_seconds())
        except Exception:
            elapsed = None
    job["elapsed_seconds"] = elapsed
    return job


def _extract_retry_after_seconds(message: str | None) -> int | None:
    text = str(message or "")
    if not text:
        return None
    match = re.search(r"retry[- ]after[^0-9]*(\d+)", text, re.IGNORECASE)
    if not match:
        return None
    try:
        value = int(match.group(1))
    except Exception:
        return None
    if value <= 0:
        return None
    return value


def _account_create_retry_policy(error_code: str, error_message: str) -> tuple[str, list[int]]:
    code = str(error_code or "").strip().lower()
    message = str(error_message or "").strip().lower()
    if code in {"ssl_or_proxy_error"}:
        return ("network", [3, 8, 15, 30])
    if code in {"rate_limited", "feedback_required"}:
        return ("throttle", [20, 45, 90, 180])
    if code in {"private_api_error"}:
        if "429" in message or "too many requests" in message or "thrott" in message:
            return ("throttle", [20, 45, 90, 180])
        if "ssl" in message or "proxy" in message or "connection" in message or "timeout" in message:
            return ("network", [3, 8, 15, 30])
    return ("fatal", [])


def _account_create_post_setup(
    *,
    login_username: str,
    auto_set_runner: bool,
    warmup_target_username: str | None,
    queue_warmup_run: bool,
    schedule_interval: str | None,
) -> dict:
    result: dict[str, object] = {
        "runner_set": False,
        "warmup_job_id": None,
        "schedule_id": None,
        "warnings": [],
    }
    warnings: list[str] = []
    target = str(warmup_target_username or "").strip().lstrip("@")
    cron_expr = str(schedule_interval or "").strip()

    if auto_set_runner:
        try:
            _set_config_values({"monitor_login_username": login_username})
            result["runner_set"] = True
            _log_account_create(f"set @{login_username} as monitor runner login")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"failed to set monitor runner login: {exc}")

    if target and queue_warmup_run:
        try:
            if _is_blocked_login(login_username):
                raise RuntimeError("login is blocked")
            if _get_run_lock(login_username).locked():
                raise RuntimeError("another run is already in progress for this login")
            if _is_target_busy(target):
                raise RuntimeError("another run is already in progress for this target")
            if UNFOLLOW_LOCK.locked() and (UNFOLLOW_JOB.get("login_username") or "") == login_username:
                raise RuntimeError("unfollow job is running for this login")
            cooldown_seconds = _get_run_cooldown_remaining(login_username)
            if cooldown_seconds > 0:
                raise RuntimeError(f"login in cooldown ({cooldown_seconds}s)")
            job_id = _queue_run(login_username, target, source="account_factory")
            result["warmup_job_id"] = job_id
            _log_account_create(f"queued warmup run job_id={job_id} for @{target}")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"failed to queue warmup run: {exc}")
    elif queue_warmup_run and not target:
        warnings.append("warmup run requested but no target username provided")

    if cron_expr and target:
        try:
            CronTrigger.from_crontab(cron_expr)
            schedule_id = _persist_schedule(login_username, target, cron_expr)
            _schedule_job(schedule_id, login_username, target, cron_expr)
            result["schedule_id"] = schedule_id
            _log_account_create(f"created schedule id={schedule_id} for @{target} ({cron_expr})")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"failed to create schedule: {exc}")
    elif cron_expr and not target:
        warnings.append("schedule interval provided but no target username provided")

    result["warnings"] = warnings
    return result


def _account_create_worker(
    *,
    created_placeholder: bool,
    strategy: str,
    email: str,
    full_name: str,
    login_username: str,
    login_password: str,
    phone_number: str,
    birth_year: int | None,
    birth_month: int | None,
    birth_day: int | None,
    max_wait_seconds: int,
    auto_set_runner: bool,
    warmup_target_username: str | None,
    queue_warmup_run: bool,
    schedule_interval: str | None,
):
    strategy = (strategy or "private_api").strip().lower()
    if strategy not in {"private_api", "guided_browser"}:
        strategy = "private_api"
    ACCOUNT_CREATE_JOB.update(
        {
            "state": "running",
            "started_at": datetime.now(LOCAL_TZ).isoformat(),
            "finished_at": None,
            "strategy": strategy,
            "email": email,
            "full_name": full_name,
            "login_username": login_username,
            "max_wait_seconds": int(max_wait_seconds),
            "message": None,
            "last_url": None,
            "saved_login": False,
            "auto_set_runner": bool(auto_set_runner),
            "warmup_target_username": str(warmup_target_username or "").strip().lstrip("@") or None,
            "queue_warmup_run": bool(queue_warmup_run),
            "warmup_job_id": None,
            "schedule_interval": str(schedule_interval or "").strip() or None,
            "schedule_id": None,
            "warnings": [],
        }
    )
    _log_account_create(f"starting account creation using strategy={strategy}")

    def _cancel_check():
        return ACCOUNT_CREATE_CANCEL.is_set()

    def _sleep_cancelable(seconds: int) -> bool:
        deadline = time.time() + max(0, int(seconds))
        while time.time() < deadline:
            if ACCOUNT_CREATE_CANCEL.is_set():
                return False
            time.sleep(1)
        return True

    try:
        proxy = _get_proxy_config(
            session_id=_generate_proxy_session_id(login_username=login_username),
            login_username=login_username,
        )
        if strategy == "private_api":
            from private_api_tracker import PrivateAPIError, signup_account_private_api

            _log_account_create("sending signup via instagrapi private API")
            result = None
            max_attempts = 5
            for attempt in range(1, max_attempts + 1):
                if ACCOUNT_CREATE_CANCEL.is_set():
                    ACCOUNT_CREATE_JOB["state"] = "cancelled"
                    ACCOUNT_CREATE_JOB["message"] = "cancel requested"
                    _log_account_create("cancelled")
                    return
                # First attempt uses stable per-login sticky session; retries rotate session id.
                if attempt == 1:
                    session_id = _generate_proxy_session_id(login_username=login_username)
                else:
                    session_id = _generate_proxy_session_id()
                proxy_url = None
                proxy = _get_proxy_config(session_id=session_id, login_username=login_username)
                if proxy.get("enabled") and proxy.get("host") and proxy.get("port"):
                    proxy_url = _build_proxy_url(
                        proxy.get("host"),
                        int(proxy.get("port")),
                        proxy.get("username") or "",
                        proxy.get("password") or "",
                    )
                if not proxy_url:
                    raise RuntimeError("proxy unavailable for private_api signup")
                _log_account_create(
                    f"signup attempt {attempt}/3 via proxy session {session_id[:8]}"
                )
                try:
                    result = signup_account_private_api(
                        login_username=login_username,
                        login_password=login_password,
                        email=email,
                        full_name=full_name,
                        phone_number=phone_number,
                        year=birth_year,
                        month=birth_month,
                        day=birth_day,
                        poll_seconds=max_wait_seconds,
                        poll_interval=5.0,
                        proxy_url=proxy_url,
                        request_sleep_seconds=0.8,
                    )
                    break
                except PrivateAPIError as exc:
                    group, schedule = _account_create_retry_policy(exc.code, str(exc))
                    retry_after = _extract_retry_after_seconds(str(exc))
                    if group != "fatal" and attempt < max_attempts:
                        schedule_index = min(attempt - 1, len(schedule) - 1) if schedule else 0
                        wait_seconds = int(schedule[schedule_index]) if schedule else 10
                        if retry_after:
                            wait_seconds = max(wait_seconds, int(retry_after))
                        _log_account_create(
                            f"{group} error ({exc.code}) on attempt {attempt}; retrying in {wait_seconds}s with rotated proxy session"
                        )
                        if not _sleep_cancelable(wait_seconds):
                            ACCOUNT_CREATE_JOB["state"] = "cancelled"
                            ACCOUNT_CREATE_JOB["message"] = "cancel requested"
                            _log_account_create("cancelled")
                            return
                        continue
                    raise
            if not result:
                raise RuntimeError("signup did not return a result")
            _log_account_create(f"instagrapi signup completed for @{result.get('username') or login_username}")
        else:
            _log_account_create("opening browser and bootstrapping signup form")
            result = create_account_guided(
                email=email,
                full_name=full_name,
                username=login_username,
                password=login_password,
                max_wait_seconds=max_wait_seconds,
                cancel_check=_cancel_check,
                log=_log_account_create,
                proxy_server=proxy.get("server") if proxy.get("enabled") else None,
                proxy_username=proxy.get("username"),
                proxy_password=proxy.get("password"),
            )
            ACCOUNT_CREATE_JOB["last_url"] = result.get("last_url")
            cancelled = bool(result.get("cancelled")) or ACCOUNT_CREATE_CANCEL.is_set()
            if cancelled:
                ACCOUNT_CREATE_JOB["state"] = "cancelled"
                ACCOUNT_CREATE_JOB["message"] = "cancel requested"
                _log_account_create("cancelled")
                return
            if not result.get("completed"):
                ACCOUNT_CREATE_JOB["state"] = "error"
                ACCOUNT_CREATE_JOB["message"] = "signup not completed before timeout"
                _log_account_create("timeout: account creation was not detected")
                return

        upsert_login(
            login_username=login_username,
            login_password=login_password or None,
            totp_seed=None,
            cookie_file=f"cookies_{login_username}.txt",
            disabled=False,
            source="db",
        )
        disable_login(login_username, False)
        _clear_login_cache()
        post_setup = _account_create_post_setup(
            login_username=login_username,
            auto_set_runner=bool(auto_set_runner),
            warmup_target_username=warmup_target_username,
            queue_warmup_run=bool(queue_warmup_run),
            schedule_interval=schedule_interval,
        )
        warnings = list(post_setup.get("warnings") or [])
        ACCOUNT_CREATE_JOB["auto_set_runner"] = bool(auto_set_runner)
        ACCOUNT_CREATE_JOB["warmup_target_username"] = str(warmup_target_username or "").strip().lstrip("@") or None
        ACCOUNT_CREATE_JOB["queue_warmup_run"] = bool(queue_warmup_run)
        ACCOUNT_CREATE_JOB["warmup_job_id"] = post_setup.get("warmup_job_id")
        ACCOUNT_CREATE_JOB["schedule_interval"] = str(schedule_interval or "").strip() or None
        ACCOUNT_CREATE_JOB["schedule_id"] = post_setup.get("schedule_id")
        ACCOUNT_CREATE_JOB["warnings"] = warnings
        ACCOUNT_CREATE_JOB["saved_login"] = True
        ACCOUNT_CREATE_JOB["state"] = "done"
        ACCOUNT_CREATE_JOB["message"] = (
            "account created and added to Login Vault"
            if not warnings
            else f"account created with warnings ({len(warnings)})"
        )
        _log_account_create(f"created @{login_username} and saved to Login Vault")
        for warning in warnings:
            _log_account_create(f"warning: {warning}")
    except Exception as exc:  # noqa: BLE001
        ACCOUNT_CREATE_JOB["state"] = "error"
        ACCOUNT_CREATE_JOB["message"] = str(exc)
        _log_account_create(f"error: {exc}")
    finally:
        if created_placeholder and not ACCOUNT_CREATE_JOB.get("saved_login"):
            try:
                delete_login(login_username)
                _clear_login_cache()
                _log_account_create(f"removed placeholder login @{login_username} after failed/cancelled signup")
            except Exception:
                pass
        ACCOUNT_CREATE_JOB["finished_at"] = datetime.now(LOCAL_TZ).isoformat()
        ACCOUNT_CREATE_CANCEL.clear()
        if ACCOUNT_CREATE_LOCK.locked():
            ACCOUNT_CREATE_LOCK.release()


def _resolve_unfollow_login(login_username: str | None):
    login_username = (login_username or "").strip()
    if login_username:
        if _is_blocked_login(login_username):
            raise ValueError("login_username is blocked")
        if login_username not in _get_login_lookup():
            raise ValueError("unknown login_username")
        return login_username
    job_login = UNFOLLOW_JOB.get("login_username")
    if job_login and not _is_blocked_login(job_login):
        return job_login
    profiles = _get_login_profiles()
    if profiles:
        return profiles[0]["login_username"]
    return None


def _unfollow_worker(usernames, login_username, target_username, dry_run, max_actions, delay_min, delay_max):
    UNFOLLOW_JOB.update(
        {
            "state": "running",
            "started_at": datetime.now(LOCAL_TZ).isoformat(),
            "finished_at": None,
            "login_username": login_username,
            "target_username": target_username,
            "total": len(usernames),
            "processed": 0,
            "errors": 0,
            "skipped": 0,
            "last_user": None,
            "message": None,
            "dry_run": bool(dry_run),
        }
    )

    if dry_run:
        _log_unfollow("info: dry run requested (no actions executed)")
        UNFOLLOW_JOB["state"] = "done"
        UNFOLLOW_JOB["message"] = "dry_run"
        UNFOLLOW_JOB["finished_at"] = datetime.now(LOCAL_TZ).isoformat()
        if UNFOLLOW_LOCK.locked():
            UNFOLLOW_LOCK.release()
        return

    def _record(username, status, detail=None):
        _record_unfollow_action(login_username, target_username, username, "unfollow", status, detail)

    def _progress(payload):
        if not isinstance(payload, dict):
            return
        UNFOLLOW_JOB["processed"] = payload.get("processed", UNFOLLOW_JOB.get("processed", 0))
        UNFOLLOW_JOB["errors"] = payload.get("errors", UNFOLLOW_JOB.get("errors", 0))
        UNFOLLOW_JOB["skipped"] = payload.get("skipped", UNFOLLOW_JOB.get("skipped", 0))
        UNFOLLOW_JOB["last_user"] = payload.get("last_user") or UNFOLLOW_JOB.get("last_user")

    def _cancel_check():
        return UNFOLLOW_CANCEL.is_set()

    try:
        session_id = _generate_proxy_session_id(login_username=login_username)
        proxy = _get_proxy_config(session_id=session_id, login_username=login_username)
        result = unfollow_users(
            usernames,
            UNFOLLOW_STORAGE,
            headless=True,
            delay_min=delay_min,
            delay_max=delay_max,
            max_actions=max_actions,
            cancel_check=_cancel_check,
            log=_log_unfollow,
            progress=_progress,
            record=_record,
            proxy_server=proxy.get("server") if proxy.get("enabled") else None,
            proxy_username=proxy.get("username"),
            proxy_password=proxy.get("password"),
        )
        cancelled = bool(result.get("cancelled")) or UNFOLLOW_CANCEL.is_set()
        fatal_error = result.get("fatal_error")
        if fatal_error:
            UNFOLLOW_JOB["state"] = "error"
            UNFOLLOW_JOB["message"] = str(fatal_error)
        elif cancelled:
            UNFOLLOW_JOB["state"] = "cancelled"
            UNFOLLOW_JOB["message"] = "cancel requested"
        else:
            UNFOLLOW_JOB["state"] = "done"
        UNFOLLOW_JOB["errors"] = result.get("errors", UNFOLLOW_JOB["errors"])
        UNFOLLOW_JOB["skipped"] = result.get("skipped", UNFOLLOW_JOB["skipped"])
        UNFOLLOW_JOB["last_user"] = result.get("last_user")
        _log_unfollow(
            f"info: done actions={result.get('actions')} skipped={result.get('skipped')} errors={result.get('errors')}"
        )
    except AuthRequiredError:
        UNFOLLOW_JOB["state"] = "error"
        UNFOLLOW_JOB["message"] = "auth_required"
        _log_unfollow("auth required: run init login")
    except Exception as exc:  # noqa: BLE001
        UNFOLLOW_JOB["state"] = "error"
        UNFOLLOW_JOB["message"] = str(exc)
        _log_unfollow(f"error: {exc}")
    finally:
        UNFOLLOW_JOB["finished_at"] = datetime.now(LOCAL_TZ).isoformat()
        UNFOLLOW_CANCEL.clear()
        if UNFOLLOW_LOCK.locked():
            UNFOLLOW_LOCK.release()


def _init_schedule_table():
    conn = _get_db()
    try:
        conn.execute(
            ddl(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id SERIAL PRIMARY KEY,
                    login_username TEXT NOT NULL,
                    target_username TEXT NOT NULL,
                    interval_minutes INTEGER,
                    interval TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
        )
        # backward compat: add interval column if only interval_minutes existed
        cols = get_columns(conn, "schedules")
        global SCHEMA_HAS_INTERVAL_MINUTES
        SCHEMA_HAS_INTERVAL_MINUTES = "interval_minutes" in cols
        if "interval" not in cols and SCHEMA_HAS_INTERVAL_MINUTES:
            conn.execute("ALTER TABLE schedules ADD COLUMN interval TEXT")
        # If old rows exist with interval_minutes but null interval, backfill to daily at 09:00
        conn.execute(
            "UPDATE schedules SET interval = COALESCE(interval, '0 9 * * *') WHERE interval IS NULL"
        )
        conn.commit()
    finally:
        conn.close()


def _init_monitor_table():
    conn = _get_db()
    try:
        conn.execute(
            ddl(
                """
                CREATE TABLE IF NOT EXISTS count_checks (
                    id SERIAL PRIMARY KEY,
                    target_username TEXT NOT NULL,
                    login_username TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    followers_count INTEGER,
                    followees_count INTEGER,
                    run_requested INTEGER NOT NULL DEFAULT 0,
                    triggered_run_id TEXT,
                    trigger_reason TEXT
                )
                """
            )
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_count_checks_target ON count_checks(target_username)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_count_checks_time ON count_checks(timestamp)")
        conn.commit()
    finally:
        conn.close()




def _load_schedules_from_db():
    conn = _get_db()
    try:
        cur = conn.execute(
            "SELECT id, login_username, target_username, interval as interval_expr FROM schedules"
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _get_targets(conn):
    cur = conn.execute("SELECT DISTINCT target_username FROM runs ORDER BY target_username")
    return [row[0] for row in cur.fetchall()]


def _get_latest_run(conn, target_username):
    cur = conn.execute(
        """
        SELECT id, target_username, login_username, timestamp, created_at
        FROM runs
        WHERE target_username = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (target_username,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _get_non_followbacks_for(conn, target_username):
    run = _get_latest_run(conn, target_username)
    if not run:
        return None, []
    run_id = run["id"]
    followers = {r[0] for r in conn.execute("SELECT username FROM run_followers WHERE run_id = ?", (run_id,))}
    followees = {r[0] for r in conn.execute("SELECT username FROM run_followees WHERE run_id = ?", (run_id,))}
    non_followbacks = sorted(followees - followers)
    return run, non_followbacks


def _persist_schedule(login_username, target_username, cron_expr):
    conn = _get_db()
    try:
        created_at = datetime.now(LOCAL_TZ).isoformat()
        if SCHEMA_HAS_INTERVAL_MINUTES:
            sql = """
                INSERT INTO schedules (login_username, target_username, interval_minutes, interval, created_at)
                VALUES (?, ?, 0, ?, ?)
            """
            params = (login_username, target_username, cron_expr, created_at)
        else:
            sql = """
                INSERT INTO schedules (login_username, target_username, interval, created_at)
                VALUES (?, ?, ?, ?)
            """
            params = (login_username, target_username, cron_expr, created_at)
        cur = conn.execute(sql + " RETURNING id", params)
        schedule_id = cur.fetchone()[0]
        conn.commit()
        return int(schedule_id)
    finally:
        conn.close()


def _delete_schedule(schedule_id):
    conn = _get_db()
    try:
        conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        conn.commit()
    finally:
        conn.close()


def _schedule_job(schedule_id, login_username, target_username, cron_expr):
    def _scheduled_wrapper():
        try:
            started = datetime.now(LOCAL_TZ).isoformat()
            res = guarded_run(login_username, target_username, source=f"schedule:{schedule_id}")
            finished = datetime.now(LOCAL_TZ).isoformat()
            try:
                elapsed = int((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds())
                if isinstance(res, dict) and res.get("run_id"):
                    update_run_duration(DB_PATH_DEFAULT, res["run_id"], elapsed)
                    conn = _get_db()
                    try:
                        _update_run_quality(conn, res["run_id"], login_username, target_username)
                    finally:
                        conn.close()
            except Exception:
                pass
        except RuntimeError:
            # Skip if busy; next tick will run
            return
    scheduler.add_job(
        _scheduled_wrapper,
        CronTrigger.from_crontab(cron_expr),
        id=str(schedule_id),
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def _restore_schedules():
    for row in _load_schedules_from_db():
        if not row.get("interval_expr"):
            continue
        _schedule_job(row["id"], row["login_username"], row["target_username"], row["interval_expr"])


def _record_count_check(
    conn,
    *,
    target_username,
    login_username,
    timestamp,
    followers_count,
    followees_count,
    run_requested=0,
    triggered_run_id=None,
    trigger_reason=None,
):
    conn.execute(
        """
        INSERT INTO count_checks (
            target_username,
            login_username,
            timestamp,
            followers_count,
            followees_count,
            run_requested,
            triggered_run_id,
            trigger_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_username,
            login_username,
            timestamp,
            followers_count,
            followees_count,
            int(bool(run_requested)),
            triggered_run_id,
            trigger_reason,
        ),
    )


def _run_count_check(login_username, target_username):
    scraper_backend = _normalize_scraper_backend(_get_config_value("run_scraper_backend", "browser"))
    login_mode = _normalize_run_login_mode(_get_config_value("run_login_mode", "auto"))
    require_private_auth = not _is_anonymous_run_login_mode(login_mode) and _is_private_backend_name(scraper_backend)
    creds = _get_credentials(login_username, require_auth=require_private_auth)
    job_dir = JOB_TMP_DIR / f"count_{uuid4().hex}"
    job_dir.mkdir(parents=True, exist_ok=True)
    result_path = job_dir / "result.json"
    out_path = job_dir / "worker.out"
    err_path = job_dir / "worker.err"

    env = os.environ.copy()
    env["INSTALAB_SCRAPER_BACKEND"] = scraper_backend
    env["SCRAPER_BACKEND"] = scraper_backend
    env["RUN_LOGIN_MODE"] = login_mode
    if creds.get("login_password"):
        env["RUN_LOGIN_PASSWORD"] = creds["login_password"]
    else:
        env.pop("RUN_LOGIN_PASSWORD", None)
    if creds.get("totp_seed"):
        env["RUN_TOTP_SEED"] = str(creds["totp_seed"]).strip()
    else:
        env.pop("RUN_TOTP_SEED", None)
    device_settings_json = str(_get_config_value("private_device_settings_json", "") or "").strip()
    if device_settings_json:
        env["RUN_DEVICE_SETTINGS_JSON"] = device_settings_json
    else:
        env.pop("RUN_DEVICE_SETTINGS_JSON", None)
    user_agent = str(_get_config_value("private_user_agent", "") or "").strip()
    if user_agent:
        env["RUN_USER_AGENT"] = user_agent
    else:
        env.pop("RUN_USER_AGENT", None)
    session_id = _generate_proxy_session_id(login_username=creds["login_username"])
    _apply_proxy_env(env, session_id=session_id, login_username=creds["login_username"])
    env["RUN_PROXY_SESSION_ID"] = session_id
    env["RUN_PRE_LOGIN_FLOW"] = "true" if _parse_bool(_get_config_value("run_pre_login_flow", False)) else "false"
    env["RUN_POST_LOGIN_FLOW"] = "true" if _parse_bool(_get_config_value("run_post_login_flow", False)) else "false"
    http_timeout_seconds = float(
        _get_config_value("run_http_timeout_seconds", _get_config_value("run_request_timeout", 120))
    )
    request_sleep_seconds = float(_get_config_value("run_private_request_sleep_seconds", 0))

    cmd = [
        sys.executable,
        str(BASE_DIR / "count_worker.py"),
        "--login",
        creds["login_username"],
        "--target",
        target_username,
        "--cookie-file",
        str(creds.get("cookie_file") or ""),
        "--result",
        str(result_path),
        "--http-timeout",
        str(http_timeout_seconds),
        "--request-sleep",
        str(request_sleep_seconds),
    ]
    cmd, env = _wrap_with_ddtrace(cmd, env, service="instalab-worker")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )
    out_fh, err_fh, out_thread, err_thread = _stream_worker_output(
        proc, out_path, err_path, f"count:{login_username}"
    )
    try:
        proc.wait(timeout=180)
    except Exception:
        _terminate_proc(proc)
        # Clean up temp directory on failure
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass
        return None
    finally:
        try:
            out_thread.join(timeout=2)
            err_thread.join(timeout=2)
        except Exception:
            pass
        try:
            out_fh.close()
            err_fh.close()
        except Exception:
            pass

    payload = _read_json_file(result_path) or {}
    
    # Clean up temp directory after reading results
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception:
        pass
    
    if payload.get("status") != "success":
        return None
    return payload.get("result") or None


def _monitor_check_target(target_username, *, login_username, threshold, min_gap_minutes):
    if not login_username:
        return
    if _get_run_lock(login_username).locked():
        return
    if _is_target_busy(target_username):
        return
    if UNFOLLOW_LOCK.locked():
        unfollow_login = UNFOLLOW_JOB.get("login_username")
        if unfollow_login and login_username == unfollow_login:
            return

    result = _run_count_check(login_username, target_username)
    if not result:
        return

    ts = result.get("timestamp")
    followers_count = result.get("followers_count")
    followees_count = result.get("followees_count")

    conn = _get_db()
    try:
        last_row = conn.execute(
            """
            SELECT followers_count, followees_count, timestamp
            FROM count_checks
            WHERE target_username = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_username,),
        ).fetchone()

        run_requested = 0
        triggered_run_id = None
        trigger_reason = None

        if last_row:
            last_followers = last_row[0]
            last_followees = last_row[1]
            delta_f = abs((followers_count or 0) - (last_followers or 0))
            delta_fe = abs((followees_count or 0) - (last_followees or 0))
            delta = max(delta_f, delta_fe)
            if delta >= threshold:
                last_run = _get_latest_run(conn, target_username)
                can_trigger = True
                if last_run and last_run.get("timestamp"):
                    last_run_dt = _parse_run_ts(last_run.get("timestamp"))
                    if last_run_dt:
                        gap = (datetime.now(LOCAL_TZ) - last_run_dt).total_seconds()
                        if gap < (min_gap_minutes * 60):
                            can_trigger = False
                            trigger_reason = "min_gap"
                if can_trigger and not _is_target_busy(target_username):
                    triggered_run_id = _queue_run(
                        login_username,
                        target_username,
                        source="monitor",
                        rebuild=True,
                    )
                    run_requested = 1
                    trigger_reason = f"delta>={threshold}"

        _record_count_check(
            conn,
            target_username=target_username,
            login_username=login_username,
            timestamp=ts,
            followers_count=followers_count,
            followees_count=followees_count,
            run_requested=run_requested,
            triggered_run_id=triggered_run_id,
            trigger_reason=trigger_reason,
        )
        conn.commit()
    finally:
        conn.close()


def _monitor_check_all():
    enabled = _parse_bool(_get_config_value("monitor_enabled", True))
    if not enabled:
        return
    login_username = _get_monitor_login()
    if not login_username:
        return
    try:
        threshold = int(_get_config_value("monitor_threshold_delta", 4))
    except Exception:
        threshold = 4
    try:
        min_gap_minutes = int(_get_config_value("monitor_min_gap_minutes", 180))
    except Exception:
        min_gap_minutes = 180

    conn = _get_db()
    try:
        targets = _get_targets(conn)
    finally:
        conn.close()
    if not targets:
        return
    for target in targets:
        _monitor_check_target(
            target,
            login_username=login_username,
            threshold=threshold,
            min_gap_minutes=min_gap_minutes,
        )


def _schedule_monitor_job():
    try:
        scheduler.remove_job("monitor_counts")
    except Exception:
        pass
    enabled = _parse_bool(_get_config_value("monitor_enabled", True))
    if not enabled:
        return
    try:
        interval = int(_get_config_value("monitor_interval_minutes", 120))
    except Exception:
        interval = 120
    if interval < 30:
        interval = 30
    scheduler.add_job(
        _monitor_check_all,
        "interval",
        minutes=interval,
        id="monitor_counts",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )


def _schedule_recon_maintenance_job():
    try:
        scheduler.remove_job("recon_maintenance")
    except Exception:
        pass
    scheduler.add_job(
        _cleanup_recon_artifacts,
        trigger=CronTrigger(hour=3, minute=20),
        id="recon_maintenance",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )


def _delete_run(run_id):
    conn = _get_db()
    try:
        cur = conn.execute(
            """
            SELECT id, target_username, login_username, timestamp, followers_count, followees_count,
                   non_followbacks_count, followers_added, followers_removed, followees_added,
                   followees_removed, prev_run_id, created_at, duration_seconds,
                   followers_fetch_seconds, followees_fetch_seconds, followers_rate, followees_rate,
                   confidence_score, confidence_flag
            FROM runs WHERE id = ?
            """,
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        run = dict(row)
        followers = [r[0] for r in conn.execute("SELECT username FROM run_followers WHERE run_id = ?", (run_id,))]
        followees = [r[0] for r in conn.execute("SELECT username FROM run_followees WHERE run_id = ?", (run_id,))]
        conn.execute("DELETE FROM run_followers WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM run_followees WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        DELETED_RUNS[run_id] = {
            "run": run,
            "followers": followers,
            "followees": followees,
            "deleted_at": datetime.now(LOCAL_TZ),
        }
        return run["target_username"]
    finally:
        conn.close()


def _restore_run(run_id):
    payload = DELETED_RUNS.get(run_id)
    if not payload:
        return False, "not available"
    if (datetime.now(LOCAL_TZ) - payload["deleted_at"]).total_seconds() > 600:
        del DELETED_RUNS[run_id]
        return False, "undo window expired"
    run = payload["run"]
    conn = _get_db()
    try:
        conn.execute(
            """
            INSERT INTO runs (
                id, target_username, login_username, timestamp, followers_count,
                followees_count, non_followbacks_count, followers_added, followers_removed,
                followees_added, followees_removed, prev_run_id, created_at,
                duration_seconds, followers_fetch_seconds, followees_fetch_seconds,
                followers_rate, followees_rate, confidence_score, confidence_flag
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["id"],
                run["target_username"],
                run["login_username"],
                run["timestamp"],
                run["followers_count"],
                run["followees_count"],
                run["non_followbacks_count"],
                run["followers_added"],
                run["followers_removed"],
                run["followees_added"],
                run["followees_removed"],
                run["prev_run_id"],
                run["created_at"],
                run.get("duration_seconds"),
                run.get("followers_fetch_seconds"),
                run.get("followees_fetch_seconds"),
                run.get("followers_rate"),
                run.get("followees_rate"),
                run.get("confidence_score"),
                run.get("confidence_flag"),
            ),
        )
        conn.executemany(
            "INSERT INTO run_followers (run_id, username) VALUES (?, ?)",
            [(run_id, u) for u in payload["followers"]],
        )
        conn.executemany(
            "INSERT INTO run_followees (run_id, username) VALUES (?, ?)",
            [(run_id, u) for u in payload["followees"]],
        )
        conn.commit()
    finally:
        conn.close()
    del DELETED_RUNS[run_id]
    return True, "restored"


def _get_credentials(login_username, *, require_auth: bool = True):
    if _is_blocked_login(login_username):
        raise ValueError(f"Login is blocked: {login_username}")
    profile = _get_login_lookup().get(login_username)
    if not profile:
        raise ValueError(f"Unknown login_username: {login_username}")
    entry = get_login(login_username, include_secrets=True) or {}
    password = entry.get("login_password") or ""
    if not password:
        prefix = profile.get("prefix")
        if prefix:
            password = os.getenv(f"{prefix}_LOGIN_PASSWORD", "")
    if require_auth and not password and not _private_session_exists(login_username):
        raise ValueError(f"Missing password or private session for login: {login_username}")
    return {
        "login_username": login_username,
        "login_password": password,
        "totp_seed": entry.get("totp_seed"),
        "cookie_file": None,
        "db_path": profile.get("db_path", str(DB_PATH_DEFAULT)),
    }


def _sanitize_login_username(value: str) -> str:
    username = (value or "").strip()
    if not username:
        return ""
    if not re.match(r"^[A-Za-z0-9._]+$", username):
        return ""
    return username


def _sanitize_target_username(value: str) -> str:
    username = (value or "").strip().lstrip("@")
    if not username:
        return ""
    if not re.match(r"^[A-Za-z0-9._]+$", username):
        return ""
    return username


def _read_json_file(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _tail_file(path: Path, lines: int = 12):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read().splitlines()
        return "\n".join(content[-lines:]) if content else ""
    except Exception:
        return ""


def _terminate_proc(proc: subprocess.Popen):
    try:
        proc.terminate()
        proc.wait(timeout=5)
        return
    except Exception:
        pass


def _stream_worker_output(proc: subprocess.Popen, out_path: Path, err_path: Path, prefix: str):
    """Stream worker stdout/stderr to files and container logs for Datadog collection."""
    out_fh = open(out_path, "w", encoding="utf-8")
    err_fh = open(err_path, "w", encoding="utf-8")

    def _reader(stream, fh, tag):
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                fh.write(line)
                fh.flush()
                print(f"[{prefix} {tag}] {line.rstrip()}", flush=True)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    out_thread = threading.Thread(target=_reader, args=(proc.stdout, out_fh, "out"), daemon=True)
    err_thread = threading.Thread(target=_reader, args=(proc.stderr, err_fh, "err"), daemon=True)
    out_thread.start()
    err_thread.start()
    return out_fh, err_fh, out_thread, err_thread
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass


def run_snapshot(login_username, target_username, job_id=None, two_factor_code=None, challenge_code=None):
    _ensure_job_tmp_dir()
    scraper_backend = _normalize_scraper_backend(_get_config_value("run_scraper_backend", "browser"))
    login_mode = _normalize_run_login_mode(_get_config_value("run_login_mode", "auto"))
    require_private_auth = not _is_anonymous_run_login_mode(login_mode) and _is_private_backend_name(scraper_backend)
    creds = _get_credentials(login_username, require_auth=require_private_auth)
    job = ACTIVE_JOBS.get(login_username)
    run_id = job_id or uuid4().hex
    job_dir = JOB_TMP_DIR / f"job_{run_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    progress_path = job_dir / "progress.json"
    result_path = job_dir / "result.json"
    out_path = job_dir / "worker.out"
    err_path = job_dir / "worker.err"

    env = os.environ.copy()
    env["INSTALAB_SCRAPER_BACKEND"] = scraper_backend
    env["SCRAPER_BACKEND"] = scraper_backend
    env["RUN_LOGIN_MODE"] = login_mode
    if creds.get("login_password"):
        env["RUN_LOGIN_PASSWORD"] = creds["login_password"]
    else:
        env.pop("RUN_LOGIN_PASSWORD", None)
    session_id = _generate_proxy_session_id(login_username=login_username)
    _apply_proxy_env(env, session_id=session_id, login_username=login_username)
    env["RUN_PROXY_SESSION_ID"] = session_id
    if two_factor_code or challenge_code:
        try:
            clear_challenge_code(login_username)
        except Exception:
            pass
    if two_factor_code:
        env["RUN_2FA_CODE"] = str(two_factor_code).strip()
    else:
        env.pop("RUN_2FA_CODE", None)
    if challenge_code:
        env["RUN_CHALLENGE_CODE"] = str(challenge_code).strip()
    else:
        env.pop("RUN_CHALLENGE_CODE", None)
    if creds.get("totp_seed"):
        env["RUN_TOTP_SEED"] = str(creds["totp_seed"]).strip()
    else:
        env.pop("RUN_TOTP_SEED", None)
    device_settings_json = str(_get_config_value("private_device_settings_json", "") or "").strip()
    if device_settings_json:
        env["RUN_DEVICE_SETTINGS_JSON"] = device_settings_json
    else:
        env.pop("RUN_DEVICE_SETTINGS_JSON", None)
    user_agent = str(_get_config_value("private_user_agent", "") or "").strip()
    if user_agent:
        env["RUN_USER_AGENT"] = user_agent
    else:
        env.pop("RUN_USER_AGENT", None)

    http_timeout_seconds = float(
        _get_config_value("run_http_timeout_seconds", _get_config_value("run_request_timeout", 600))
    )
    request_sleep_seconds = float(_get_config_value("run_private_request_sleep_seconds", 0))
    item_delay_min = float(_get_config_value("run_item_delay_min", 0.25))
    item_delay_max = float(_get_config_value("run_item_delay_max", 0.75))
    fetch_order = str(_get_config_value("run_fetch_order", "followers_first") or "followers_first").strip().lower()
    initial_fetch_delay_seconds = float(_get_config_value("run_initial_fetch_delay_seconds", 8.0))
    pause_every_min = int(_get_config_value("run_pause_every_min", 0) or 0)
    pause_every_max = int(_get_config_value("run_pause_every_max", 0) or 0)
    pause_seconds_min = float(_get_config_value("run_pause_seconds_min", 0) or 0)
    pause_seconds_max = float(_get_config_value("run_pause_seconds_max", 0) or 0)
    trace_enabled = _parse_bool(_get_config_value("run_trace_enabled", False))
    profile_only = _parse_bool(_get_config_value("run_profile_only", False))
    pre_login_flow = _parse_bool(_get_config_value("run_pre_login_flow", False))
    post_login_flow = _parse_bool(_get_config_value("run_post_login_flow", False))
    stall_seconds = int(_get_config_value("run_stall_seconds", 1200))
    max_seconds = int(_get_config_value("run_max_seconds", 10800))
    env["RUN_ITEM_DELAY_MIN"] = str(item_delay_min)
    env["RUN_ITEM_DELAY_MAX"] = str(item_delay_max)
    env["RUN_FETCH_ORDER"] = fetch_order
    env["RUN_INITIAL_FETCH_DELAY_SECONDS"] = str(initial_fetch_delay_seconds)
    env["RUN_PAUSE_EVERY_MIN"] = str(pause_every_min)
    env["RUN_PAUSE_EVERY_MAX"] = str(pause_every_max)
    env["RUN_PAUSE_SECONDS_MIN"] = str(pause_seconds_min)
    env["RUN_PAUSE_SECONDS_MAX"] = str(pause_seconds_max)
    env["RUN_HTTP_TIMEOUT_SECONDS"] = str(http_timeout_seconds)
    env["RUN_PRIVATE_REQUEST_SLEEP_SECONDS"] = str(request_sleep_seconds)
    env["RUN_TRACE_ENABLED"] = "true" if trace_enabled else "false"
    env["RUN_PRE_LOGIN_FLOW"] = "true" if pre_login_flow else "false"
    env["RUN_POST_LOGIN_FLOW"] = "true" if post_login_flow else "false"
    if trace_enabled:
        env["RUN_TRACE_PATH"] = str(job_dir / "trace.jsonl")
    env["RUN_PROFILE_ONLY"] = "true" if profile_only else "false"
    cmd = [
        sys.executable,
        str(BASE_DIR / "snapshot_worker.py"),
        "--login",
        creds["login_username"],
        "--target",
        target_username,
        "--db-path",
        str(creds["db_path"]),
        "--cookie-file",
        str(creds["cookie_file"] or ""),
        "--progress",
        str(progress_path),
        "--result",
        str(result_path),
        "--http-timeout",
        str(http_timeout_seconds),
        "--request-sleep",
        str(request_sleep_seconds),
    ]
    cmd, env = _wrap_with_ddtrace(cmd, env, service="instalab-worker")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )
    out_fh, err_fh, out_thread, err_thread = _stream_worker_output(
        proc, out_path, err_path, f"run:{login_username}"
    )

    if job:
        job["worker_pid"] = proc.pid

    start_ts = time.time()
    last_progress_ts = start_ts
    last_mtime = 0

    while True:
        if proc.poll() is not None:
            break

        try:
            mtime = progress_path.stat().st_mtime
        except FileNotFoundError:
            mtime = 0
        if mtime and mtime != last_mtime:
            last_mtime = mtime
            payload = _read_json_file(progress_path) or {}
            phase = payload.get("phase") or "running"
            count = payload.get("count")
            last_progress_ts = time.time()
            if job:
                if payload.get("followers_total") is not None:
                    try:
                        job["followers_total"] = int(payload.get("followers_total"))
                    except Exception:
                        pass
                if payload.get("following_total") is not None:
                    try:
                        job["following_total"] = int(payload.get("following_total"))
                    except Exception:
                        pass
                if phase != "totals":
                    job["phase"] = phase
                if phase == "followers":
                    job["followers_progress"] = int(count or 0)
                elif phase == "following":
                    job["following_progress"] = int(count or 0)

        if (login_username, target_username) in CANCEL_REQUESTS or (job and job.get("cancelled")):
            _terminate_proc(proc)
            raise RuntimeError("cancelled")

        if time.time() - last_progress_ts > stall_seconds:
            if job:
                job["stalled"] = True
                job["stall_reason"] = f"no progress for {int(time.time() - last_progress_ts)}s"
                job["cancelled"] = True
            _terminate_proc(proc)
            raise RuntimeError("stalled (no progress)")

        if time.time() - start_ts > max_seconds:
            if job:
                job["stalled"] = True
                job["stall_reason"] = f"exceeded hard limit ({int(time.time() - start_ts)}s)"
                job["cancelled"] = True
            _terminate_proc(proc)
            raise RuntimeError("stalled (time limit)")

        time.sleep(1.0)

    try:
        out_thread.join(timeout=2)
        err_thread.join(timeout=2)
    except Exception:
        pass
    try:
        out_fh.close()
        err_fh.close()
    except Exception:
        pass

    result_payload = _read_json_file(result_path)
    if not result_payload:
        tail = _tail_file(err_path)
        error_msg = f"worker exited without result"
        if tail:
            error_msg = f"{error_msg}: {tail}"
        error_msg = f"{error_msg} (job_dir: {job_dir})"
        raise RuntimeError(error_msg)
    if result_payload.get("status") != "success":
        tail = _tail_file(err_path)
        error_msg = result_payload.get("error", "worker failed")
        if tail:
            error_msg = f"{error_msg}: {tail}"
        error_msg = f"{error_msg} (job_dir: {job_dir})"
        raise WorkerRunError(error_msg, code=result_payload.get("error_code"))

    result = result_payload.get("result") or {}
    
    return result


def guarded_run(login_username, target_username, source="api", job_id=None, two_factor_code=None, challenge_code=None):
    """Serialise runs per login across API + scheduler."""
    _acquire_run_slot(source, login_username, target_username, job_id=job_id)
    try:
        if (login_username, target_username) in CANCEL_REQUESTS:
            job = ACTIVE_JOBS.get(login_username)
            if job:
                job["cancelled"] = True
            raise RuntimeError("cancelled")
        return run_snapshot(
            login_username,
            target_username,
            job_id=job_id,
            two_factor_code=two_factor_code,
            challenge_code=challenge_code,
        )
    finally:
        CANCEL_REQUESTS.discard((login_username, target_username))
        _release_run_slot(login_username)


# Background scheduler and executor for async runs
executor = ThreadPoolExecutor(max_workers=3)
scheduler = BackgroundScheduler()
scheduler.configure(timezone=LOCAL_TZ)
scheduler.start()

# Schedule periodic cleanup of old job directories (runs every 6 hours)
scheduler.add_job(
    _cleanup_old_job_dirs,
    trigger="interval",
    hours=6,
    id="cleanup_old_job_dirs",
    name="Cleanup old job directories",
)

# Track in-flight manual runs
RUN_FUTURES = {}
RUN_META = {}
RUN_INPUTS = {}
RUN_LOCKS = {}
RUN_LOCKS_GUARD = threading.Lock()
RUN_COOLDOWN_UNTIL = {}
RUN_COOLDOWN_GUARD = threading.Lock()
ACTIVE_JOBS = {}
LAST_JOB_BY_LOGIN = {}
RECON_FUTURES = {}
RECON_META = {}
RECON_PROCESSES = {}
RECON_CANCEL_EVENTS = {}
RECON_LOCK = threading.Lock()
DELETED_RUNS = {}
CANCEL_REQUESTS = set()
SCHEMA_HAS_INTERVAL_MINUTES = False
UNFOLLOW_LOCK = threading.Lock()
UNFOLLOW_CANCEL = threading.Event()
UNFOLLOW_JOB = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "login_username": None,
    "target_username": None,
    "total": 0,
    "excluded": 0,
    "batch_size": None,
    "processed": 0,
    "errors": 0,
    "skipped": 0,
    "last_user": None,
    "message": None,
    "dry_run": bool(CONFIG_DEFAULTS.get("unfollow_dry_run_default", False)),
}
UNFOLLOW_LOG = []
UNFOLLOW_STORAGE = str(BASE_DIR / ".playwright" / "instagram_storage.json")
ACCOUNT_CREATE_LOCK = threading.Lock()
ACCOUNT_CREATE_CANCEL = threading.Event()
ACCOUNT_CREATE_JOB = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "strategy": "private_api",
    "email": None,
    "full_name": None,
    "login_username": None,
    "max_wait_seconds": 300,
    "message": None,
    "last_url": None,
    "saved_login": False,
    "auto_set_runner": True,
    "warmup_target_username": None,
    "queue_warmup_run": False,
    "warmup_job_id": None,
    "schedule_interval": None,
    "schedule_id": None,
    "warnings": [],
}
ACCOUNT_CREATE_LOG = []
RUN_WATCHDOG_STOP = threading.Event()
RUN_DISPATCH_STOP = threading.Event()
MANUAL_ACTIONS_LOCK = threading.Lock()
MANUAL_ACTIONS: dict[str, dict] = {}
RUN_STATE_ALLOWED = {
    "queued",
    "running",
    "done",
    "error",
    "cancelled",
    "cooling_down",
    "challenge_required",
    "manual_required",
    "blocked",
}
RUN_STATE_TERMINAL = {"done", "error", "cancelled", "cooling_down", "challenge_required", "manual_required", "blocked"}


def _run_state_set(job_id: str, state: str, *, reason: str | None = None, **extra) -> None:
    target_state = str(state or "").strip().lower()
    if target_state not in RUN_STATE_ALLOWED:
        target_state = "error"
    meta = RUN_META.setdefault(job_id, {})
    history = meta.setdefault("state_history", [])
    if not isinstance(history, list):
        history = []
        meta["state_history"] = history
    now_iso = datetime.now(LOCAL_TZ).isoformat()
    if meta.get("state") != target_state:
        history.append({"state": target_state, "at": now_iso, "reason": reason})
    meta["state"] = target_state
    if reason:
        meta["state_reason"] = str(reason)
    for key, value in extra.items():
        if value is None:
            continue
        meta[key] = value


def _record_run_job_event(job_id: str, event: str, details: dict | None = None) -> None:
    conn = _get_db()
    try:
        conn.execute(
            """
            INSERT INTO run_job_events (job_id, event_at, event, details_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                job_id,
                datetime.now(LOCAL_TZ).isoformat(),
                str(event or "").strip() or "event",
                json.dumps(details or {}, separators=(",", ":")),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _load_run_job_record(job_id: str) -> dict | None:
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM run_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        raw_result = data.get("result_json")
        if raw_result:
            try:
                data["result"] = json.loads(raw_result)
            except Exception:
                data["result"] = None
        else:
            data["result"] = None
        return data
    finally:
        conn.close()


def _create_run_job_record(
    *,
    job_id: str,
    login_username: str,
    target_username: str,
    source: str,
) -> dict:
    now_iso = datetime.now(LOCAL_TZ).isoformat()
    conn = _get_db()
    try:
        conn.execute(
            """
            INSERT INTO run_jobs (
                job_id,
                login_username,
                target_username,
                source,
                status,
                submitted_at
            ) VALUES (?, ?, ?, ?, 'queued', ?)
            """,
            (job_id, login_username, target_username, source, now_iso),
        )
        conn.commit()
    finally:
        conn.close()
    _record_run_job_event(job_id, "queued", {"source": source})
    return {
        "job_id": job_id,
        "login_username": login_username,
        "target_username": target_username,
        "source": source,
        "status": "queued",
        "submitted_at": now_iso,
    }


def _update_run_job_record(
    job_id: str,
    *,
    status: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    cancel_requested: bool | None = None,
    state_reason: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    cooldown_seconds: int | None = None,
    result: dict | None = None,
) -> None:
    updates = []
    params: list = []
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if started_at is not None:
        updates.append("started_at = ?")
        params.append(started_at)
    if finished_at is not None:
        updates.append("finished_at = ?")
        params.append(finished_at)
    if cancel_requested is not None:
        updates.append("cancel_requested = ?")
        params.append(1 if cancel_requested else 0)
    if state_reason is not None:
        updates.append("state_reason = ?")
        params.append(state_reason)
    if error_code is not None:
        updates.append("error_code = ?")
        params.append(error_code)
    if error_message is not None:
        updates.append("error_message = ?")
        params.append(error_message)
    if cooldown_seconds is not None:
        updates.append("cooldown_seconds = ?")
        params.append(int(cooldown_seconds))
    if result is not None:
        updates.append("result_json = ?")
        params.append(json.dumps(result, separators=(",", ":")))
    if not updates:
        return
    params.append(job_id)
    conn = _get_db()
    try:
        conn.execute(f"UPDATE run_jobs SET {', '.join(updates)} WHERE job_id = ?", tuple(params))
        conn.commit()
    finally:
        conn.close()


def _claim_next_run_jobs(limit: int = 25) -> list[dict]:
    conn = _get_db()
    try:
        rows = conn.execute(
            """
            SELECT job_id, login_username, target_username, source, submitted_at
            FROM run_jobs
            WHERE status = 'queued'
            ORDER BY submitted_at ASC, job_id ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _mark_run_job_running(job_id: str) -> bool:
    now_iso = datetime.now(LOCAL_TZ).isoformat()
    conn = _get_db()
    try:
        cur = conn.execute(
            """
            UPDATE run_jobs
            SET status = 'running',
                started_at = ?,
                state_reason = NULL,
                error_code = NULL,
                error_message = NULL,
                cooldown_seconds = NULL
            WHERE job_id = ? AND status = 'queued'
            """,
            (now_iso, job_id),
        )
        conn.commit()
        claimed = int(getattr(cur, "rowcount", 0) or 0) > 0
    finally:
        conn.close()
    if claimed:
        _record_run_job_event(job_id, "running", {"started_at": now_iso})
    return claimed


def _list_run_jobs_by_status(*statuses: str, limit: int = 200) -> list[dict]:
    wanted = [str(s or "").strip().lower() for s in statuses if str(s or "").strip()]
    if not wanted:
        return []
    placeholders = ",".join(["?"] * len(wanted))
    conn = _get_db()
    try:
        rows = conn.execute(
            f"""
            SELECT job_id, login_username, target_username, source, status, submitted_at, started_at, finished_at, state_reason, cooldown_seconds
            FROM run_jobs
            WHERE status IN ({placeholders})
            ORDER BY submitted_at ASC, job_id ASC
            LIMIT ?
            """,
            (*wanted, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _latest_run_job_for_login(login_username: str) -> dict | None:
    conn = _get_db()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM run_jobs
            WHERE login_username = ?
            ORDER BY submitted_at DESC, job_id DESC
            LIMIT 1
            """,
            (login_username,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _run_record_meta(record: dict | None) -> dict | None:
    if not record:
        return None
    meta = {
        "login_username": record.get("login_username"),
        "target_username": record.get("target_username"),
        "submitted_at": record.get("submitted_at"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "source": record.get("source"),
        "state": record.get("status"),
    }
    if record.get("state_reason"):
        meta["state_reason"] = record.get("state_reason")
    if record.get("error_code"):
        meta["error_code"] = record.get("error_code")
    if record.get("error_message"):
        meta["error_message"] = record.get("error_message")
    if record.get("cooldown_seconds"):
        meta["cooldown_seconds"] = record.get("cooldown_seconds")
    return meta

def _manual_action_key(login_username: str, target_username: str, action_type: str) -> str:
    return f"{str(login_username or '').strip().lower()}::{str(target_username or '').strip().lower()}::{str(action_type or '').strip().lower()}"


def _enqueue_manual_action(
    *,
    login_username: str,
    target_username: str,
    job_id: str,
    action_type: str,
    reason: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    key = _manual_action_key(login_username, target_username, action_type)
    with MANUAL_ACTIONS_LOCK:
        existing = MANUAL_ACTIONS.get(key) or {}
        action_id = existing.get("action_id") or uuid4().hex
        now_iso = datetime.now(LOCAL_TZ).isoformat()
        payload = {
            "action_id": action_id,
            "key": key,
            "state": "open",
            "created_at": existing.get("created_at") or now_iso,
            "updated_at": now_iso,
            "login_username": login_username,
            "target_username": target_username,
            "job_id": job_id,
            "action_type": action_type,
            "reason": reason,
            "error_code": error_code,
            "error_message": error_message,
            "recommended_step": (
                "Submit challenge/2FA code and retry run"
                if action_type in {"challenge_required", "two_factor_required"}
                else "Review account state in Instagram app and retry when stable"
            ),
        }
        MANUAL_ACTIONS[key] = payload
    return payload


def _resolve_manual_actions_for(login_username: str, target_username: str, *, note: str | None = None) -> None:
    login_key = str(login_username or "").strip().lower()
    target_key = str(target_username or "").strip().lower()
    now_iso = datetime.now(LOCAL_TZ).isoformat()
    with MANUAL_ACTIONS_LOCK:
        for key, action in MANUAL_ACTIONS.items():
            if action.get("state") != "open":
                continue
            if str(action.get("login_username") or "").strip().lower() != login_key:
                continue
            if str(action.get("target_username") or "").strip().lower() != target_key:
                continue
            action["state"] = "resolved"
            action["resolved_at"] = now_iso
            if note:
                action["resolution_note"] = note


def _open_manual_actions() -> list[dict]:
    with MANUAL_ACTIONS_LOCK:
        rows = [dict(v) for v in MANUAL_ACTIONS.values() if str(v.get("state")) == "open"]
    rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return rows


def _classify_terminal_run_state(error_code: str | None, error_message: str | None, cooldown_seconds: int) -> str:
    code = str(error_code or "").strip().lower()
    message = str(error_message or "").strip().lower()
    if cooldown_seconds > 0 or code in {"rate_limited", "feedback_required"}:
        return "cooling_down"
    if code == "challenge_required":
        return "challenge_required"
    if code == "two_factor_required":
        return "manual_required"
    if code in {"unsupported_version", "login_required", "bad_password", "invalid_username"}:
        return "blocked"
    if "challenge" in message or "two-factor" in message or "2fa" in message:
        return "manual_required"
    return "error"


def _collector_storage_path(login_username: str) -> str:
    username = str(login_username or "").strip()
    if not username:
        return UNFOLLOW_STORAGE
    try:
        return str(browser_storage_path_for_login(username))
    except Exception:
        return UNFOLLOW_STORAGE


def _get_run_lock(login_username: str) -> threading.Lock:
    key = login_username or "_default"
    with RUN_LOCKS_GUARD:
        lock = RUN_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            RUN_LOCKS[key] = lock
    return lock


def _clear_run_cooldown(login_username: str) -> None:
    if not login_username:
        return
    with RUN_COOLDOWN_GUARD:
        RUN_COOLDOWN_UNTIL.pop(login_username, None)


def _set_run_cooldown(login_username: str, error_code: str | None, error_message: str | None) -> int:
    if not login_username:
        return 0
    base = int(_get_config_value("run_rate_limit_cooldown_seconds", 900) or 900)
    retry_after = _extract_retry_after_seconds(error_message)
    wait_seconds = max(base, int(retry_after or 0))
    until_ts = time.time() + wait_seconds
    with RUN_COOLDOWN_GUARD:
        RUN_COOLDOWN_UNTIL[login_username] = {
            "until_ts": until_ts,
            "error_code": str(error_code or ""),
            "error_message": str(error_message or ""),
        }
    return wait_seconds


def _get_run_cooldown_remaining(login_username: str) -> int:
    if not login_username:
        return 0
    with RUN_COOLDOWN_GUARD:
        payload = RUN_COOLDOWN_UNTIL.get(login_username)
        if not payload:
            return 0
        until_ts = float(payload.get("until_ts") or 0)
        remaining = int(round(until_ts - time.time()))
        if remaining <= 0:
            RUN_COOLDOWN_UNTIL.pop(login_username, None)
            return 0
        return remaining


def _watchdog_loop():
    while not RUN_WATCHDOG_STOP.is_set():
        time.sleep(6)
        now = datetime.now(LOCAL_TZ)
        conn = _get_db()
        try:
            for login, job in list(ACTIVE_JOBS.items()):
                started_at = job.get("started_at")
                if not started_at:
                    continue
                try:
                    started = datetime.fromisoformat(started_at)
                    elapsed = int((now - started).total_seconds())
                except Exception:
                    elapsed = 0
                avg = _avg_duration_seconds(conn, job.get("login_username"), job.get("target_username"))
                if avg:
                    limit = max(int(avg * 3), avg + 300)
                else:
                    limit = 2 * 60 * 60
                if elapsed > limit:
                    job["stalled"] = True
                    job["stall_reason"] = f"exceeded expected duration ({elapsed}s)"
                    job["cancelled"] = True
                    CANCEL_REQUESTS.add((job.get("login_username"), job.get("target_username")))
        finally:
            conn.close()


def _acquire_run_slot(source: str, login_username: str, target_username: str, job_id: str | None = None):
    """Prevent overlapping runs per login (manual or scheduled)."""
    lock = _get_run_lock(login_username)
    if not lock.acquire(blocking=False):
        raise RuntimeError("Another run is already in progress for this login")
    ACTIVE_JOBS[login_username] = {
        "source": source,
        "login_username": login_username,
        "target_username": target_username,
        "started_at": datetime.now(LOCAL_TZ).isoformat(),
        "cancelled": False,
        "job_id": job_id,
        "phase": "starting",
        "followers_progress": 0,
        "following_progress": 0,
        "followers_total": None,
        "following_total": None,
    }
    try:
        conn = _get_db()
        try:
            f_total, fe_total = _last_totals(conn, target_username)
        finally:
            conn.close()
        if f_total is not None:
            ACTIVE_JOBS[login_username]["followers_total"] = f_total
        if fe_total is not None:
            ACTIVE_JOBS[login_username]["following_total"] = fe_total
    except Exception:
        pass


def _release_run_slot(login_username: str):
    ACTIVE_JOBS.pop(login_username, None)
    lock = _get_run_lock(login_username)
    if lock.locked():
        lock.release()


def _recon_state_counts() -> dict:
    running = 0
    queued = 0
    for jid, meta in list(RECON_META.items()):
        fut = RECON_FUTURES.get(jid)
        if fut and not fut.done():
            if meta.get("state") == "running":
                running += 1
            elif meta.get("state") == "queued":
                queued += 1
    return {"running": running, "queued": queued, "pending": running + queued}


def _recon_running_count() -> int:
    return int(_recon_state_counts()["running"])


def _recon_queue_snapshot() -> list[dict]:
    rows = []
    now = time.time()
    for jid, meta in list(RECON_META.items()):
        fut = RECON_FUTURES.get(jid)
        if not fut or fut.done():
            continue
        state = str(meta.get("state") or "queued")
        if state not in {"queued", "running"}:
            continue
        created_raw = str(meta.get("created_at") or "")
        started_raw = str(meta.get("started_at") or "")
        elapsed = None
        if started_raw:
            try:
                started_dt = datetime.fromisoformat(started_raw)
                elapsed = max(0, int((datetime.now(LOCAL_TZ) - started_dt).total_seconds()))
            except Exception:
                elapsed = None
        rows.append(
            {
                "job_id": jid,
                "state": state,
                "mode": meta.get("mode"),
                "query_value": meta.get("query_value"),
                "created_at": created_raw,
                "started_at": started_raw or None,
                "timeout_seconds": int(meta.get("timeout_seconds") or 0),
                "elapsed_seconds": elapsed,
                "_sort_key": created_raw or str(now),
            }
        )
    rows.sort(key=lambda item: item.get("_sort_key") or "")
    queue_index = 0
    for item in rows:
        if item["state"] == "queued":
            queue_index += 1
            item["queue_position"] = queue_index
        else:
            item["queue_position"] = 0
        timeout_seconds = int(item.get("timeout_seconds") or 0)
        elapsed = item.get("elapsed_seconds")
        if item["state"] == "running" and timeout_seconds > 0 and elapsed is not None:
            item["timeout_remaining_seconds"] = max(0, timeout_seconds - int(elapsed))
        else:
            item["timeout_remaining_seconds"] = None
        item.pop("_sort_key", None)
    return rows


def _queue_recon_scan(mode: str, query_value: str, *, requested_by: str | None = None, options: dict | None = None):
    cfg = _get_config()
    if not _parse_bool(cfg.get("recon_enabled", True)):
        raise RuntimeError("recon module disabled")
    max_concurrency = int(cfg.get("recon_max_concurrency", 2) or 2)
    queue_limit = int(cfg.get("recon_queue_limit", 20) or 20)
    counts = _recon_state_counts()
    if counts["pending"] >= queue_limit:
        raise RuntimeError("recon queue at capacity")

    options = options or {}
    job_id = uuid4().hex
    source_tool = "phoneinfoga" if str(mode).strip().lower() == "phone" else "blackbird"
    timeout_seconds = int(options.get("timeout_seconds") or cfg.get("recon_timeout_seconds") or 240)

    conn = _get_db()
    try:
        recon_query_id = create_recon_query(
            conn,
            mode=str(mode).strip().lower(),
            query_value=(query_value or "").strip(),
            requested_by=requested_by,
            source_tool=source_tool,
        )
        create_recon_job(
            conn,
            job_id=job_id,
            recon_query_id=recon_query_id,
            status="queued",
            command_fingerprint=None,
        )
        conn.commit()
    finally:
        conn.close()

    RECON_META[job_id] = {
        "job_id": job_id,
        "state": "queued",
        "mode": mode,
        "query_value": query_value,
        "requested_by": requested_by,
        "created_at": datetime.now(LOCAL_TZ).isoformat(),
        "started_at": None,
        "timeout_seconds": timeout_seconds,
        "error": None,
    }
    cancel_event = threading.Event()
    RECON_CANCEL_EVENTS[job_id] = cancel_event
    RECON_PROCESSES[job_id] = None

    def _runner():
        started = time.time()
        while True:
            if cancel_event.is_set():
                raise ReconExecutionError("cancelled by user")
            if _recon_running_count() < max_concurrency:
                break
            time.sleep(0.2)

        RECON_META[job_id]["state"] = "running"
        RECON_META[job_id]["started_at"] = datetime.now(LOCAL_TZ).isoformat()
        conn = _get_db()
        try:
            mark_recon_job_running(conn, job_id=job_id)
            conn.commit()
        finally:
            conn.close()

        job_dir = JOB_TMP_DIR / f"recon_{job_id}"
        job_dir.mkdir(parents=True, exist_ok=True)
        try:
            payload = run_recon_scan(
                mode=mode,
                query_value=query_value,
                options={
                    **options,
                    "timeout_seconds": timeout_seconds,
                    "_cancel_event": cancel_event,
                    "_on_process_start": lambda proc: RECON_PROCESSES.__setitem__(job_id, proc),
                },
                job_dir=job_dir,
                cfg=cfg,
            )
            duration = int(payload.get("duration_seconds") or round(time.time() - started))
            conn = _get_db()
            try:
                finalize_recon_job(
                    conn,
                    job_id=job_id,
                    status="success",
                    duration_seconds=duration,
                    raw_output_path=payload.get("raw_output_path"),
                    error_message=None,
                    findings=payload.get("findings") or [],
                    artifacts=payload.get("artifacts") or [],
                )
                conn.commit()
            finally:
                conn.close()
            RECON_META[job_id]["state"] = "done"
            RECON_META[job_id]["finished_at"] = datetime.now(LOCAL_TZ).isoformat()
            RECON_META[job_id]["result"] = payload
            return payload
        except ReconExecutionError as exc:
            message = str(exc)
            cancelled = "cancelled by user" in message.lower()
            conn = _get_db()
            try:
                finalize_recon_job(
                    conn,
                    job_id=job_id,
                    status="cancelled" if cancelled else "error",
                    duration_seconds=int(round(time.time() - started)),
                    raw_output_path=None,
                    error_message=message,
                    findings=[],
                    artifacts=[],
                )
                conn.commit()
            finally:
                conn.close()
            RECON_META[job_id]["state"] = "cancelled" if cancelled else "error"
            RECON_META[job_id]["error"] = message
            RECON_META[job_id]["finished_at"] = datetime.now(LOCAL_TZ).isoformat()
            return {"status": "cancelled" if cancelled else "error", "error": message}
        except (ValidationError, RuntimeError, subprocess.TimeoutExpired) as exc:
            conn = _get_db()
            try:
                finalize_recon_job(
                    conn,
                    job_id=job_id,
                    status="error",
                    duration_seconds=int(round(time.time() - started)),
                    raw_output_path=None,
                    error_message=str(exc),
                    findings=[],
                    artifacts=[],
                )
                conn.commit()
            finally:
                conn.close()
            RECON_META[job_id]["state"] = "error"
            RECON_META[job_id]["error"] = str(exc)
            RECON_META[job_id]["finished_at"] = datetime.now(LOCAL_TZ).isoformat()
            return {"status": "error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            conn = _get_db()
            try:
                finalize_recon_job(
                    conn,
                    job_id=job_id,
                    status="error",
                    duration_seconds=int(round(time.time() - started)),
                    raw_output_path=None,
                    error_message=str(exc),
                    findings=[],
                    artifacts=[],
                )
                conn.commit()
            finally:
                conn.close()
            RECON_META[job_id]["state"] = "error"
            RECON_META[job_id]["error"] = str(exc)
            RECON_META[job_id]["finished_at"] = datetime.now(LOCAL_TZ).isoformat()
            return {"status": "error", "error": str(exc)}
        finally:
            RECON_PROCESSES.pop(job_id, None)
            RECON_CANCEL_EVENTS.pop(job_id, None)

    RECON_FUTURES[job_id] = executor.submit(_runner)
    return job_id


_init_schedule_table()
_init_config_table()
_init_unfollow_table()
_init_run_tables()
_init_recon_tables()
_init_login_tables()
_init_monitor_table()
_restore_schedules()
_schedule_monitor_job()
_schedule_recon_maintenance_job()
threading.Thread(target=_watchdog_loop, daemon=True).start()

app = Flask(__name__)


@app.before_request
def _block_removed_recon_routes():
    if request.path == "/api/recon" or request.path.startswith("/api/recon/"):
        return jsonify({"error": "recon feature has been removed"}), 410


@app.route("/api/logins", methods=["GET"])
def api_logins():
    entries = [e for e in list_logins(include_secrets=False) if not e.get("disabled")]
    try:
        from private_api_tracker import get_auth_state
    except Exception:  # pragma: no cover
        get_auth_state = None
    payload = []
    for entry in entries:
        username = entry.get("login_username")
        path = _private_settings_path(username)
        session_cached = bool(entry.get("session_settings")) or path.exists()
        auth_state = get_auth_state(username) if callable(get_auth_state) else {}
        fail_streak = int(entry.get("session_fail_streak") or 0)
        payload.append(
            {
                "login_username": username,
                "cookie_file": entry.get("cookie_file"),
                "db_path": str(DB_PATH_DEFAULT),
                "source": entry.get("source", "db"),
                "private_session_exists": session_cached,
                "private_session_mtime": (path.stat().st_mtime if path.exists() else None),
                "has_password": bool(entry.get("has_password")),
                "has_totp_seed": bool(entry.get("has_totp_seed")),
                "last_login_at": entry.get("last_login_at"),
                "last_error": entry.get("last_error"),
                "session_fail_streak": fail_streak,
                "session_last_fail_at": entry.get("session_last_fail_at"),
                "session_marked_stale": fail_streak >= _session_stale_threshold(),
                "two_factor_method": auth_state.get("two_factor_method"),
                "auth_last_event": auth_state.get("last_event"),
                "auth_last_event_at": auth_state.get("last_event_at"),
            }
        )
    return jsonify(payload)


@app.route("/api/logins/auth/trace", methods=["GET"])
def api_logins_auth_trace():
    login_username = (request.args.get("login_username") or "").strip()
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    if login_username not in _get_login_lookup():
        return jsonify({"error": "unknown login_username"}), 404
    try:
        limit = int(request.args.get("limit") or 30)
    except Exception:
        limit = 30
    try:
        from private_api_tracker import get_auth_trace

        rows = get_auth_trace(login_username, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to fetch auth trace: {exc}"}), 500
    return jsonify({"login_username": login_username, "trace": rows})


@app.route("/api/logins/auth/preflight", methods=["POST"])
def api_logins_auth_preflight():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    if login_username not in _get_login_lookup():
        return jsonify({"error": "unknown login_username"}), 404
    entry = get_login(login_username, include_secrets=False) or {}
    fail_streak = int(entry.get("session_fail_streak") or 0)
    proxy = _get_proxy_config(
        session_id=_generate_proxy_session_id(login_username=login_username),
        login_username=login_username,
    )
    proxy_url = None
    if proxy.get("enabled") and proxy.get("host") and proxy.get("port"):
        proxy_url = _build_proxy_url(
            proxy.get("host"),
            int(proxy.get("port")),
            proxy.get("username") or "",
            proxy.get("password") or "",
        )
    try:
        from private_api_tracker import get_auth_state, get_auth_trace, get_instagram_clock_skew

        auth_state = get_auth_state(login_username)
        trace = get_auth_trace(login_username, limit=20)
        clock_skew = get_instagram_clock_skew(proxy_url=proxy_url, timeout_seconds=12.0)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to run auth preflight: {exc}"}), 500

    warnings: list[str] = []
    abs_skew = int(clock_skew.get("abs_skew_seconds") or 0) if clock_skew.get("ok") else None
    if abs_skew is not None and abs_skew > 15:
        warnings.append(f"clock skew is high ({abs_skew}s); TOTP can fail when skew exceeds ~15s")
    if fail_streak >= _session_stale_threshold():
        warnings.append("session marked stale after repeated session validation failures; re-init login is recommended")
    if auth_state.get("two_factor_method") == "unknown":
        warnings.append("two-factor method not confirmed yet; run one login to detect TOTP/SMS/email path")
    if auth_state.get("two_factor_method") in {"sms", "email"} and entry.get("has_totp_seed"):
        warnings.append("account reports non-TOTP 2FA method; stored TOTP seed may not be used for this login")

    return jsonify(
        {
            "ok": True,
            "login_username": login_username,
            "private_session_exists": bool(entry.get("session_settings")) or _private_settings_path(login_username).exists(),
            "has_password": bool(entry.get("has_password")),
            "has_totp_seed": bool(entry.get("has_totp_seed")),
            "session_fail_streak": fail_streak,
            "session_marked_stale": fail_streak >= _session_stale_threshold(),
            "session_last_fail_at": entry.get("session_last_fail_at"),
            "two_factor_method": auth_state.get("two_factor_method"),
            "clock_skew": clock_skew,
            "warnings": warnings,
            "trace": trace,
        }
    )


@app.route("/api/logins/create/status", methods=["GET"])
def api_logins_create_status():
    return jsonify({"job": _account_create_snapshot(), "log": ACCOUNT_CREATE_LOG[:40]})


@app.route("/api/logins/create/cancel", methods=["POST"])
def api_logins_create_cancel():
    if not ACCOUNT_CREATE_LOCK.locked():
        return jsonify({"error": "no account creation job running"}), 400
    ACCOUNT_CREATE_CANCEL.set()
    ACCOUNT_CREATE_JOB["state"] = "cancelling"
    ACCOUNT_CREATE_JOB["message"] = "cancel requested"
    return jsonify({"cancelled": True})


@app.route("/api/logins/create/start", methods=["POST"])
def api_logins_create_start():
    if ACCOUNT_CREATE_LOCK.locked():
        return jsonify({"error": "account creation job already running"}), 409
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    full_name = (data.get("full_name") or "").strip()
    login_username = _sanitize_login_username(data.get("login_username") or "")
    login_password = (data.get("login_password") or "").strip()
    phone_number = (data.get("phone_number") or "").strip()
    strategy = (data.get("strategy") or "private_api").strip().lower()
    auto_set_runner = _parse_bool(data.get("auto_set_runner", True))
    queue_warmup_run = _parse_bool(data.get("queue_warmup_run", False))
    warmup_target_username = _sanitize_target_username(data.get("warmup_target_username") or "")
    schedule_interval = str(data.get("schedule_interval") or "").strip()
    try:
        max_wait_seconds = int(data.get("max_wait_seconds") or 300)
    except Exception:
        max_wait_seconds = 300
    max_wait_seconds = max(60, min(max_wait_seconds, 900))
    birth_year = data.get("birth_year")
    birth_month = data.get("birth_month")
    birth_day = data.get("birth_day")
    try:
        birth_year = int(birth_year) if birth_year not in (None, "") else None
    except Exception:
        birth_year = None
    try:
        birth_month = int(birth_month) if birth_month not in (None, "") else None
    except Exception:
        birth_month = None
    try:
        birth_day = int(birth_day) if birth_day not in (None, "") else None
    except Exception:
        birth_day = None

    if not email or "@" not in email:
        return jsonify({"error": "valid email is required"}), 400
    if not full_name:
        return jsonify({"error": "full_name is required"}), 400
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    if len(login_password) < 6:
        return jsonify({"error": "login_password must be at least 6 characters"}), 400
    if strategy not in {"private_api", "guided_browser"}:
        return jsonify({"error": "strategy must be private_api or guided_browser"}), 400
    if strategy == "private_api":
        proxy_check = _get_proxy_config(
            session_id=_generate_proxy_session_id(login_username=login_username),
            login_username=login_username,
        )
        if not proxy_check.get("enabled"):
            return jsonify({"error": "private_api signup requires proxy_enabled=true"}), 400
        if not proxy_check.get("host") or not proxy_check.get("port"):
            return jsonify({"error": "private_api signup requires proxy host/port"}), 400
        if not proxy_check.get("username") or not proxy_check.get("password"):
            return jsonify({"error": "private_api signup requires proxy username/password"}), 400
    if strategy == "guided_browser" and not os.getenv("DISPLAY"):
        return jsonify({"error": "guided_browser strategy requires a display server (DISPLAY not set)"}), 400
    if schedule_interval:
        if not warmup_target_username:
            return jsonify({"error": "warmup_target_username is required when schedule_interval is provided"}), 400
        try:
            CronTrigger.from_crontab(schedule_interval)
        except Exception:
            return jsonify({"error": "schedule_interval must be a valid crontab expression"}), 400
    if queue_warmup_run and not warmup_target_username:
        return jsonify({"error": "warmup_target_username is required when queue_warmup_run=true"}), 400
    if _is_blocked_login(login_username):
        return jsonify({"error": "login_username is blocked"}), 400
    if _get_run_lock(login_username).locked():
        return jsonify({"error": "run already in progress for this login"}), 409
    if UNFOLLOW_LOCK.locked() and (UNFOLLOW_JOB.get("login_username") or "") == login_username:
        return jsonify({"error": "unfollow job running for this login"}), 409
    if get_login(login_username, include_secrets=False):
        return jsonify({"error": "login_username already exists in vault"}), 409

    created_placeholder = False
    try:
        upsert_login(
            login_username=login_username,
            login_password=login_password or None,
            totp_seed=None,
            cookie_file=f"cookies_{login_username}.txt",
            disabled=False,
            source="db",
        )
        _clear_login_cache()
        created_placeholder = True
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to prepare login placeholder: {exc}"}), 500

    if not ACCOUNT_CREATE_LOCK.acquire(blocking=False):
        if created_placeholder:
            try:
                delete_login(login_username)
                _clear_login_cache()
            except Exception:
                pass
        return jsonify({"error": "account creation job already running"}), 409
    ACCOUNT_CREATE_CANCEL.clear()
    executor.submit(
        _account_create_worker,
        created_placeholder=created_placeholder,
        strategy=strategy,
        email=email,
        full_name=full_name,
        login_username=login_username,
        login_password=login_password,
        phone_number=phone_number,
        birth_year=birth_year,
        birth_month=birth_month,
        birth_day=birth_day,
        max_wait_seconds=max_wait_seconds,
        auto_set_runner=auto_set_runner,
        warmup_target_username=warmup_target_username or None,
        queue_warmup_run=queue_warmup_run,
        schedule_interval=schedule_interval or None,
    )
    return jsonify(
        {
            "started": True,
            "strategy": strategy,
            "login_username": login_username,
            "max_wait_seconds": max_wait_seconds,
            "auto_set_runner": auto_set_runner,
            "queue_warmup_run": queue_warmup_run,
            "warmup_target_username": warmup_target_username or None,
            "schedule_interval": schedule_interval or None,
        }
    )


@app.route("/api/logins/add", methods=["POST"])
def api_logins_add():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    login_password = (data.get("login_password") or "").strip()
    totp_seed = (data.get("totp_seed") or "").strip() or None
    private_session_exists = _private_session_exists(login_username) if login_username else False
    if not login_username or (not login_password and not private_session_exists and not totp_seed):
        return jsonify({"error": "login_username and login_password are required (no private session cached)"}), 400
    if _is_blocked_login(login_username):
        return jsonify({"error": "login_username is blocked"}), 400
    try:
        entry = get_login(login_username, include_secrets=False)
        if entry:
            if login_password:
                set_login_password(login_username, login_password)
            if totp_seed:
                set_totp_seed(login_username, totp_seed)
            disable_login(login_username, False)
            _clear_login_cache()
            return jsonify({"updated": login_username})
        upsert_login(
            login_username=login_username,
            login_password=login_password or None,
            totp_seed=totp_seed,
            cookie_file=f"cookies_{login_username}.txt",
            disabled=False,
            source="db",
        )
        _clear_login_cache()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to save login: {exc}"}), 500
    return jsonify({"added": login_username})


@app.route("/api/logins/reset", methods=["POST"])
def api_logins_reset():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    if login_username not in _get_login_lookup():
        return jsonify({"error": "unknown login_username"}), 404
    if _get_run_lock(login_username).locked():
        return jsonify({"error": "run already in progress for this login"}), 409
    if UNFOLLOW_LOCK.locked() and (UNFOLLOW_JOB.get("login_username") or "") == login_username:
        return jsonify({"error": "unfollow job running for this login"}), 409
    try:
        removed = _remove_private_settings(login_username)
        clear_session_settings(login_username)
        _clear_login_cache()
        return jsonify({"ok": True, "login_username": login_username, "removed": removed})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to remove session: {exc}"}), 500


@app.route("/api/logins/delete", methods=["POST"])
def api_logins_delete():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    delete_session = bool(data.get("delete_session", True))
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    if login_username not in _get_login_lookup():
        return jsonify({"error": "unknown login_username"}), 404
    if _get_run_lock(login_username).locked():
        return jsonify({"error": "run already in progress for this login"}), 409
    if UNFOLLOW_LOCK.locked() and (UNFOLLOW_JOB.get("login_username") or "") == login_username:
        return jsonify({"error": "unfollow job running for this login"}), 409
    try:
        delete_login(login_username)
        removed_session = False
        if delete_session:
            removed_session = _remove_private_settings(login_username)
            clear_session_settings(login_username)
        _clear_login_cache()
        return jsonify({"deleted": login_username, "session_removed": removed_session})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to delete login: {exc}"}), 500


@app.route("/api/logins/challenge", methods=["POST"])
def api_logins_challenge():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    code = (data.get("code") or data.get("challenge_code") or "").strip()
    if not login_username or not code:
        return jsonify({"error": "login_username and code are required"}), 400
    if login_username not in _get_login_lookup():
        return jsonify({"error": "unknown login_username"}), 404
    try:
        set_challenge_code(login_username, code)
        return jsonify({"ok": True, "login_username": login_username})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to store challenge code: {exc}"}), 500


@app.route("/api/logins/new-password", methods=["POST"])
def api_logins_new_password():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    new_password = (data.get("new_password") or "").strip()
    if not login_username or not new_password:
        return jsonify({"error": "login_username and new_password are required"}), 400
    if login_username not in _get_login_lookup():
        return jsonify({"error": "unknown login_username"}), 404
    try:
        set_new_password(login_username, new_password)
        return jsonify({"ok": True, "login_username": login_username})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to store new password: {exc}"}), 500


@app.route("/api/logins/password/reset-request", methods=["POST"])
def api_logins_password_reset_request():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    email_or_username = (data.get("email_or_username") or "").strip()
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    if login_username not in _get_login_lookup():
        return jsonify({"error": "unknown login_username"}), 404
    if _get_run_lock(login_username).locked():
        return jsonify({"error": "run already in progress for this login"}), 409
    if UNFOLLOW_LOCK.locked() and (UNFOLLOW_JOB.get("login_username") or "") == login_username:
        return jsonify({"error": "unfollow job running for this login"}), 409
    identifier = email_or_username or login_username
    if not identifier:
        return jsonify({"error": "email_or_username is required"}), 400
    try:
        from private_api_tracker import request_password_reset

        proxy = load_proxy_from_env()
        proxy_url = proxy.get("url") if proxy else None
        http_timeout_seconds = _float_config("private_http_timeout_seconds", 30.0)
        user_agent = str(_get_config_value("private_user_agent", "") or "").strip() or None
        result = request_password_reset(
            email_or_username=identifier,
            proxy_url=proxy_url,
            http_timeout_seconds=http_timeout_seconds,
            user_agent=user_agent,
        )
        return jsonify(
            {
                "ok": True,
                "login_username": login_username,
                "email_or_username": identifier,
                "http_status": result.get("http_status"),
                "payload": result.get("payload"),
                "via_proxy": bool(proxy_url),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to request password reset: {exc}"}), 500


@app.route("/api/logins/totp/seed", methods=["POST"])
def api_logins_totp_seed():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    two_factor_code = (data.get("two_factor_code") or "").strip() or None
    challenge_code = (data.get("challenge_code") or "").strip() or None
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    if login_username not in _get_login_lookup():
        return jsonify({"error": "unknown login_username"}), 404
    creds = _get_credentials(login_username)
    device_settings_json = str(_get_config_value("private_device_settings_json", "") or "").strip() or None
    user_agent = str(_get_config_value("private_user_agent", "") or "").strip() or None
    try:
        from private_api_tracker import generate_totp_seed

        seed = generate_totp_seed(
            login_username=login_username,
            login_password=creds.get("login_password"),
            two_factor_code=two_factor_code,
            challenge_code=challenge_code,
            device_settings_json=device_settings_json,
            user_agent=user_agent,
        )
        return jsonify({"ok": True, "login_username": login_username, "totp_seed": seed})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to generate totp seed: {exc}"}), 500


@app.route("/api/logins/totp/enable", methods=["POST"])
def api_logins_totp_enable():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    seed = (data.get("totp_seed") or "").strip()
    verification_code = (data.get("verification_code") or "").strip() or None
    two_factor_code = (data.get("two_factor_code") or "").strip() or None
    challenge_code = (data.get("challenge_code") or "").strip() or None
    if not login_username or not seed:
        return jsonify({"error": "login_username and totp_seed are required"}), 400
    if login_username not in _get_login_lookup():
        return jsonify({"error": "unknown login_username"}), 404
    creds = _get_credentials(login_username)
    device_settings_json = str(_get_config_value("private_device_settings_json", "") or "").strip() or None
    user_agent = str(_get_config_value("private_user_agent", "") or "").strip() or None
    try:
        from private_api_tracker import enable_totp

        backup_codes = enable_totp(
            login_username=login_username,
            login_password=creds.get("login_password"),
            totp_seed=seed,
            verification_code=verification_code,
            two_factor_code=two_factor_code,
            challenge_code=challenge_code,
            device_settings_json=device_settings_json,
            user_agent=user_agent,
        )
        return jsonify({"ok": True, "login_username": login_username, "backup_codes": backup_codes})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to enable totp: {exc}"}), 500


@app.route("/api/logins/totp/disable", methods=["POST"])
def api_logins_totp_disable():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    two_factor_code = (data.get("two_factor_code") or "").strip() or None
    challenge_code = (data.get("challenge_code") or "").strip() or None
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    if login_username not in _get_login_lookup():
        return jsonify({"error": "unknown login_username"}), 404
    creds = _get_credentials(login_username)
    device_settings_json = str(_get_config_value("private_device_settings_json", "") or "").strip() or None
    user_agent = str(_get_config_value("private_user_agent", "") or "").strip() or None
    try:
        from private_api_tracker import disable_totp

        ok = disable_totp(
            login_username=login_username,
            login_password=creds.get("login_password"),
            two_factor_code=two_factor_code,
            challenge_code=challenge_code,
            device_settings_json=device_settings_json,
            user_agent=user_agent,
        )
        return jsonify({"ok": bool(ok), "login_username": login_username})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to disable totp: {exc}"}), 500


@app.route("/api/logins/totp/code", methods=["POST"])
def api_logins_totp_code():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    entry = get_login(login_username, include_secrets=True) or {}
    seed = entry.get("totp_seed")
    if not seed:
        return jsonify({"error": "totp seed not configured"}), 404
    try:
        from private_api_tracker import generate_totp_code

        code = generate_totp_code(seed)
        return jsonify({"ok": True, "login_username": login_username, "code": code})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"failed to generate totp code: {exc}"}), 500



@app.route("/api/targets", methods=["GET"])
def api_targets():
    conn = _get_db()
    try:
        cur = conn.execute(
            """
            SELECT target_username, MAX(timestamp) as last_run
            FROM runs
            GROUP BY target_username
            ORDER BY target_username
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        return jsonify(rows)
    finally:
        conn.close()


@app.route("/api/runs", methods=["GET"])
def api_runs():
    target = request.args.get("target")
    limit = int(request.args.get("limit", 20))
    if not target:
        return jsonify({"error": "target is required"}), 400
    conn = _get_db()
    try:
        cur = conn.execute(
            """
            SELECT id, timestamp, followers_count, followees_count,
                   followers_added, followers_removed, followees_added, followees_removed,
                   non_followbacks_count, login_username, duration_seconds, confidence_score, confidence_flag,
                   followers_collected_count, followees_collected_count, snapshot_complete, snapshot_note
            FROM runs
            WHERE target_username = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (target, limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
        return jsonify(rows)
    finally:
        conn.close()


@app.route("/api/recon/run", methods=["POST"])
def api_recon_run():
    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or "").strip().lower()
    query_value = (data.get("query_value") or "").strip()
    requested_by = (data.get("requested_by") or "").strip() or None
    options = data.get("options") or {}
    if not mode or not query_value:
        return jsonify({"error": "mode and query_value are required"}), 400
    if not isinstance(options, dict):
        return jsonify({"error": "options must be an object"}), 400
    try:
        job_id = _queue_recon_scan(mode, query_value, requested_by=requested_by, options=options)
        return jsonify({"job_id": job_id, "status": "queued"})
    except (ValidationError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/recon/run/<job_id>", methods=["GET"])
def api_recon_run_status(job_id):
    fut = RECON_FUTURES.get(job_id)
    meta = RECON_META.get(job_id) or {}
    conn = _get_db()
    try:
        job = get_recon_job(conn, job_id)
        findings = list_recon_findings(conn, job_id=job_id) if job else []
        artifacts = list_recon_artifacts(conn, job_id=job_id) if job else []
    finally:
        conn.close()
    if not job:
        return jsonify({"error": "recon job not found"}), 404
    done = bool(fut.done()) if fut else job.get("status") in {"success", "error", "cancelled"}
    return jsonify(
        {
            "done": done,
            "meta": meta,
            "job": job,
            "findings": findings,
            "artifacts": artifacts,
        }
    )


@app.route("/api/recon/run/<job_id>", methods=["DELETE"])
def api_recon_run_delete(job_id):
    fut = RECON_FUTURES.get(job_id)
    if fut and not fut.done():
        return jsonify({"error": "cannot delete a running recon job; cancel first"}), 409

    conn = _get_db()
    deleted = False
    try:
        job = get_recon_job(conn, job_id)
        if not job:
            return jsonify({"error": "recon job not found"}), 404
        if job.get("status") in {"queued", "running"}:
            return jsonify({"error": "cannot delete a queued/running recon job; cancel first"}), 409
        deleted = delete_recon_job(conn, job_id=job_id)
        conn.commit()
    finally:
        conn.close()

    job_dir = JOB_TMP_DIR / f"recon_{job_id}"
    shutil.rmtree(job_dir, ignore_errors=True)
    RECON_FUTURES.pop(job_id, None)
    RECON_META.pop(job_id, None)
    return jsonify({"job_id": job_id, "deleted": bool(deleted)})


@app.route("/api/recon/run/<job_id>/cancel", methods=["POST"])
def api_recon_run_cancel(job_id):
    fut = RECON_FUTURES.get(job_id)
    meta = RECON_META.get(job_id)
    conn = _get_db()
    try:
        job = get_recon_job(conn, job_id)
        if not job:
            return jsonify({"error": "recon job not found"}), 404
        if job.get("status") in {"success", "error", "cancelled"}:
            return jsonify({"error": "recon job already finished"}), 409

        if fut and not fut.done():
            cancelled = fut.cancel()
            if not cancelled:
                cancel_event = RECON_CANCEL_EVENTS.get(job_id)
                if cancel_event:
                    cancel_event.set()
                proc = RECON_PROCESSES.get(job_id)
                if proc and getattr(proc, "poll", lambda: None)() is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                if meta is not None:
                    meta["state"] = "cancelling"
                return jsonify({"job_id": job_id, "cancel_requested": True, "running": True})

        finalize_recon_job(
            conn,
            job_id=job_id,
            status="cancelled",
            duration_seconds=int(job.get("duration_seconds") or 0),
            raw_output_path=None,
            error_message="cancelled by user",
            findings=[],
            artifacts=[],
        )
        conn.commit()
    finally:
        conn.close()
    if meta is not None:
        meta["state"] = "cancelled"
        meta["error"] = "cancelled by user"
        meta["finished_at"] = datetime.now(LOCAL_TZ).isoformat()
    return jsonify({"job_id": job_id, "cancelled": True, "stale_recovered": not bool(fut)})


@app.route("/api/recon/history", methods=["GET"])
def api_recon_history():
    mode = (request.args.get("mode") or "").strip().lower() or None
    q = (request.args.get("q") or "").strip() or None
    status = (request.args.get("status") or "").strip().lower() or None
    limit = sanitize_sql_limit(request.args.get("limit"), default=30, max_limit=200)
    conn = _get_db()
    try:
        rows = list_recon_jobs(conn, mode=mode, q=q, status=status, limit=limit)
        return jsonify(rows)
    finally:
        conn.close()


@app.route("/api/recon/queue", methods=["GET"])
def api_recon_queue():
    cfg = _get_config()
    counts = _recon_state_counts()
    return jsonify(
        {
            "queue": _recon_queue_snapshot(),
            "running": counts["running"],
            "queued": counts["queued"],
            "pending": counts["pending"],
            "max_concurrency": int(cfg.get("recon_max_concurrency", 2) or 2),
            "queue_limit": int(cfg.get("recon_queue_limit", 20) or 20),
        }
    )


@app.route("/api/recon/findings", methods=["GET"])
def api_recon_findings():
    job_id = (request.args.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id is required"}), 400
    conn = _get_db()
    try:
        job = get_recon_job(conn, job_id)
        if not job:
            return jsonify({"error": "recon job not found"}), 404
        findings = list_recon_findings(conn, job_id=job_id)
        artifacts = list_recon_artifacts(conn, job_id=job_id)
        return jsonify({"job": job, "findings": findings, "artifacts": artifacts})
    finally:
        conn.close()


@app.route("/api/recon/export/<job_id>", methods=["GET"])
def api_recon_export(job_id):
    export_format = (request.args.get("format") or "json").strip().lower()
    if export_format not in {"json", "csv"}:
        return jsonify({"error": "format must be json or csv"}), 400
    conn = _get_db()
    try:
        job = get_recon_job(conn, job_id)
        if not job:
            return jsonify({"error": "recon job not found"}), 404
        findings = list_recon_findings(conn, job_id=job_id)
    finally:
        conn.close()

    filename_base = f"recon_{job_id}"
    if export_format == "json":
        payload = {
            "job": job,
            "findings": findings,
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        return Response(
            body,
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename=\"{filename_base}.json\"'},
        )

    lines = ["platform,url,category,tool_status,confidence_tier"]
    for f in findings:
        row = [
            str(f.get("platform") or "").replace(",", " "),
            str(f.get("url") or "").replace(",", " "),
            str(f.get("category") or "").replace(",", " "),
            str(f.get("tool_status") or "").replace(",", " "),
            str(f.get("confidence_tier") or "").replace(",", " "),
        ]
        lines.append(",".join(row))
    return Response(
        "\n".join(lines) + "\n",
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename=\"{filename_base}.csv\"'},
    )


@app.route("/api/recon/report/<job_id>", methods=["GET"])
def api_recon_report_download(job_id):
    conn = _get_db()
    try:
        job = get_recon_job(conn, job_id)
        if not job:
            return jsonify({"error": "recon job not found"}), 404
        artifacts = list_recon_artifacts(conn, job_id=job_id)
    finally:
        conn.close()

    priority = {"pdf_instalab": 0, "pdf": 1, "pdf_blackbird": 2}
    pdf_artifacts = [
        a
        for a in artifacts
        if str(a.get("artifact_type") or "").lower() in priority
    ]
    if not pdf_artifacts:
        return jsonify({"error": "report not found for this recon job"}), 404
    pdf_artifacts.sort(key=lambda a: priority.get(str(a.get("artifact_type") or "").lower(), 99))
    path = Path(str(pdf_artifacts[0].get("path") or ""))
    if not path.exists() or not path.is_file():
        return jsonify({"error": "report file missing on disk"}), 410
    return send_from_directory(path.parent, path.name, as_attachment=True, mimetype="application/pdf")


@app.route("/api/recon/ai/setup", methods=["POST"])
def api_recon_ai_setup():
    cfg = _get_config()
    blackbird_cmd = str(cfg.get("recon_blackbird_cmd") or "").strip()
    cmd = shlex.split(blackbird_cmd)
    if not cmd:
        return jsonify({"error": "recon_blackbird_cmd is empty"}), 400
    cmd.append("--setup-ai")

    runtime_key_path = _blackbird_ai_key_path(cfg)
    runtime_cwd = runtime_key_path.parent
    runtime_cwd.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
            cwd=runtime_cwd,
            input="y\n",
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "blackbird AI setup timed out"}), 504

    if proc.returncode != 0:
        return jsonify(
            {
                "error": "blackbird AI setup failed",
                "returncode": proc.returncode,
                "stderr": (proc.stderr or "")[-800:],
            }
        ), 500

    return jsonify(
        {
            "ok": runtime_key_path.exists(),
            "key_configured": runtime_key_path.exists(),
            "stdout_tail": (proc.stdout or "")[-500:],
        }
    )


@app.route("/api/recon/health", methods=["GET"])
def api_recon_health():
    cfg = _get_config()
    blackbird_cmd = str(cfg.get("recon_blackbird_cmd") or "").strip()
    phoneinfoga_cmd = str(cfg.get("recon_phoneinfoga_cmd") or "").strip()
    blackbird_exec = shlex.split(blackbird_cmd)[0] if blackbird_cmd else ""
    phoneinfoga_exec = shlex.split(phoneinfoga_cmd)[0] if phoneinfoga_cmd else ""

    payload = {
        "status": "ok",
        "enabled": bool(cfg.get("recon_enabled", True)),
        "ai": {
            "enabled": bool(cfg.get("recon_blackbird_ai_enabled", False)),
            "key_configured": _blackbird_ai_key_path(cfg).exists(),
        },
        "queue": {
            "running": _recon_state_counts()["running"],
            "queued": _recon_state_counts()["queued"],
            "max_concurrency": int(cfg.get("recon_max_concurrency", 2) or 2),
            "queue_limit": int(cfg.get("recon_queue_limit", 20) or 20),
        },
        "tools": {
            "blackbird": {
                "command": blackbird_cmd,
                "executable_exists": bool(shutil.which(blackbird_exec)) if blackbird_exec else False,
            },
            "phoneinfoga": {
                "command": phoneinfoga_cmd,
                "executable_exists": bool(shutil.which(phoneinfoga_exec)) if phoneinfoga_exec else False,
            },
        },
    }
    if not payload["enabled"]:
        payload["status"] = "degraded"
    elif not payload["tools"]["blackbird"]["executable_exists"]:
        payload["status"] = "degraded"
    return jsonify(payload)


@app.route("/api/import/osintgraph", methods=["POST"])
def api_import_osintgraph():
    data = request.get_json(silent=True) or {}
    target_username = (data.get("target_username") or "").strip()
    login_username = (data.get("login_username") or "").strip()
    followers = data.get("followers") or []
    followees = data.get("followees") or []
    if not target_username or not login_username:
        return jsonify({"error": "target_username and login_username are required"}), 400
    if not isinstance(followers, list) or not isinstance(followees, list):
        return jsonify({"error": "followers and followees must be lists"}), 400
    try:
        followers = [str(u).strip() for u in followers if str(u).strip()]
        followees = [str(u).strip() for u in followees if str(u).strip()]
    except Exception:
        return jsonify({"error": "invalid follower/followee values"}), 400
    tz = ZoneInfo("America/New_York")
    timestamp = (data.get("timestamp") or "").strip() or datetime.now(tz).strftime("%Y-%m-%d_%H-%M-%S")
    followers_fetch_seconds = data.get("followers_fetch_seconds")
    followees_fetch_seconds = data.get("followees_fetch_seconds")
    followers_rate = None
    followees_rate = None
    try:
        if followers_fetch_seconds:
            followers_rate = round(len(followers) / float(followers_fetch_seconds), 3)
        if followees_fetch_seconds:
            followees_rate = round(len(followees) / float(followees_fetch_seconds), 3)
    except Exception:
        pass
    non_followbacks = sorted(set(followees) - set(followers))
    try:
        changes, run_id = write_run_metadata(
            db_path=str(DB_PATH_DEFAULT),
            login_username=login_username,
            target_username=target_username,
            timestamp=timestamp,
            followers=followers,
            followees=followees,
            non_followbacks_count=len(non_followbacks),
            followers_fetch_seconds=followers_fetch_seconds,
            followees_fetch_seconds=followees_fetch_seconds,
            followers_rate=followers_rate,
            followees_rate=followees_rate,
        )
        return jsonify({"ok": True, "run_id": run_id, "changes": changes})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/last_status", methods=["GET"])
def api_last_status():
    """Return last run per target and overall latest (by id)."""
    conn = _get_db()
    try:
        cur = conn.execute(
            """
            SELECT r.id, r.target_username, r.login_username, r.timestamp,
                   r.followers_count, r.followees_count, r.non_followbacks_count, r.created_at
            FROM runs r
            JOIN (
                SELECT target_username, MAX(id) AS max_id
                FROM runs
                GROUP BY target_username
            ) m ON r.id = m.max_id
            ORDER BY r.id DESC
            """
        )
        per_target = [dict(row) for row in cur.fetchall()]
        cur2 = conn.execute(
            """
            SELECT id, target_username, login_username, timestamp,
                   followers_count, followees_count, non_followbacks_count, created_at
            FROM runs
            ORDER BY id DESC
            LIMIT 1
            """
        )
        overall = cur2.fetchone()
        overall = dict(overall) if overall else None
        return jsonify({"targets": per_target, "overall": overall})
    finally:
        conn.close()


@app.route("/api/targets_summary", methods=["GET"])
def api_targets_summary():
    conn = _get_db()
    try:
        targets = _get_targets(conn)
        if not targets:
            return jsonify([])
        
        cutoff = datetime.now(LOCAL_TZ) - timedelta(days=7)
        cutoff_str = cutoff.strftime("%Y-%m-%d_%H-%M-%S")
        
        # Build single optimized query to get all data at once
        placeholders = ",".join(["?"] * len(targets))
        
        # Get latest 2 runs per target for delta calculation
        # Filter in outer query to retrieve only top 2 rows per target
        latest_query = f"""
        SELECT 
            target_username,
            id, timestamp, followers_count, followees_count, non_followbacks_count,
            followers_added, followers_removed, followees_added, followees_removed, 
            login_username, duration_seconds, confidence_score, confidence_flag,
            rn
        FROM (
            SELECT 
                target_username,
                id, timestamp, followers_count, followees_count, non_followbacks_count,
                followers_added, followers_removed, followees_added, followees_removed, 
                login_username, duration_seconds, confidence_score, confidence_flag,
                ROW_NUMBER() OVER (PARTITION BY target_username ORDER BY timestamp DESC, id DESC) as rn
            FROM runs
            WHERE target_username IN ({placeholders})
        ) ranked
        WHERE rn <= 2
        """
        
        if is_postgres():
            latest_query = latest_query.replace("?", "%s")
        
        cur = conn.execute(latest_query, tuple(targets))
        rows_by_target = {}
        for row in cur.fetchall():
            row_dict = dict(row)
            target = row_dict["target_username"]
            if target not in rows_by_target:
                rows_by_target[target] = []
            rows_by_target[target].append(row_dict)
        
        # Get history data (first 30 runs ordered by timestamp)
        # Filter ranked results to first 30 rows per target for efficiency
        history_query = f"""
        SELECT target_username, followers_count
        FROM (
            SELECT 
                target_username, followers_count,
                ROW_NUMBER() OVER (PARTITION BY target_username ORDER BY timestamp ASC) as rn
            FROM runs
            WHERE target_username IN ({placeholders})
        ) ranked
        WHERE rn <= 30
        ORDER BY target_username, rn
        """
        
        if is_postgres():
            history_query = history_query.replace("?", "%s")
        
        hist_cur = conn.execute(history_query, tuple(targets))
        history_by_target = {}
        for row in hist_cur.fetchall():
            row_dict = dict(row)
            target = row_dict["target_username"]
            if target not in history_by_target:
                history_by_target[target] = []
            history_by_target[target].append(row_dict["followers_count"])
        
        # Get week aggregates
        week_query = f"""
        SELECT 
            target_username,
            COUNT(*) as runs,
            SUM(COALESCE(followers_added, 0)) as followers_added,
            SUM(COALESCE(followers_removed, 0)) as followers_removed,
            SUM(COALESCE(followees_added, 0)) as followees_added,
            SUM(COALESCE(followees_removed, 0)) as followees_removed
        FROM runs
        WHERE target_username IN ({placeholders})
          AND timestamp >= ?
        GROUP BY target_username
        """
        
        if is_postgres():
            week_query = week_query.replace("?", "%s")
        
        week_cur = conn.execute(week_query, tuple(targets) + (cutoff_str,))
        week_by_target = {}
        for row in week_cur.fetchall():
            row_dict = dict(row)
            week_by_target[row_dict["target_username"]] = row_dict
        
        # Build output
        out = []
        for t in targets:
            rows = rows_by_target.get(t, [])
            if not rows:
                continue
            
            latest = rows[0]
            prev = rows[1] if len(rows) > 1 else None
            
            delta_f = latest["followers_count"] - (prev["followers_count"] if prev else 0)
            delta_fe = latest["followees_count"] - (prev["followees_count"] if prev else 0)
            
            history = history_by_target.get(t, [])
            week_data = week_by_target.get(t, {})
            
            out.append(
                {
                    "target_username": t,
                    "latest": latest,
                    "delta_followers": delta_f,
                    "delta_followees": delta_fe,
                    "history_followers": history,
                    "week": {
                        "runs": week_data.get("runs", 0),
                        "followers_added": week_data.get("followers_added", 0),
                        "followers_removed": week_data.get("followers_removed", 0),
                        "followees_added": week_data.get("followees_added", 0),
                        "followees_removed": week_data.get("followees_removed", 0),
                    },
                }
            )
        return jsonify(out)
    finally:
        conn.close()


def _parse_run_ts(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=LOCAL_TZ)
    except Exception:
        return None


def _is_active_flag(val) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    sval = str(val).strip().lower()
    return sval not in {"0", "false", "none", ""}


@app.route("/api/insights/followers", methods=["GET"])
def api_follow_insights():
    target = (request.args.get("target") or "").strip()
    kind = (request.args.get("kind") or "followers").strip().lower()
    try:
        days = int(request.args.get("days", 7))
    except Exception:
        days = 7
    if not target:
        return jsonify({"error": "target is required"}), 400
    if kind not in {"followers", "following", "followees"}:
        return jsonify({"error": "invalid kind"}), 400
    table = "followers_history" if kind == "followers" else "followees_history"
    now = datetime.now(LOCAL_TZ)
    cutoff_dt = now - timedelta(days=days)
    cutoff = cutoff_dt.strftime("%Y-%m-%d_%H-%M-%S")

    conn = _get_db()
    try:
        cur = conn.execute(
            f"""
            SELECT username, first_seen, last_seen, active, unfollowed_at, first_seen_known
            FROM {table}
            WHERE target_username = ?
              AND ((first_seen_known = 1 AND first_seen >= ?) OR (unfollowed_at IS NOT NULL AND unfollowed_at >= ?))
            ORDER BY COALESCE(unfollowed_at, first_seen) DESC
            LIMIT 200
            """,
            (target, cutoff, cutoff),
        )
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"insights unavailable: {exc}"}), 500
    finally:
        conn.close()

    active_items = []
    churned_items = []
    for row in rows:
        username = row["username"] if isinstance(row, dict) else row[0]
        first_seen = row["first_seen"] if isinstance(row, dict) else row[1]
        last_seen = row["last_seen"] if isinstance(row, dict) else row[2]
        active_flag = row["active"] if isinstance(row, dict) else row[3]
        unfollowed_at = row["unfollowed_at"] if isinstance(row, dict) else row[4]
        first_seen_known = row["first_seen_known"] if isinstance(row, dict) else row[5]
        active = _is_active_flag(active_flag)
        known = _is_active_flag(first_seen_known)
        start_dt = _parse_run_ts(first_seen)
        unfollowed_dt = _parse_run_ts(unfollowed_at)

        include_active = active and known and start_dt and start_dt >= cutoff_dt
        include_churned = (not active) and unfollowed_dt and unfollowed_dt >= cutoff_dt
        if not include_active and not include_churned:
            continue

        end_dt = now if active else (unfollowed_dt or now)
        days_followed = None
        if known and start_dt and end_dt:
            days_followed = max(0, (end_dt - start_dt).days)
        item = {
            "username": username,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "active": active,
            "unfollowed_at": unfollowed_at,
            "days_followed": days_followed,
            "first_seen_known": known,
        }
        if include_active:
            active_items.append(item)
        elif include_churned:
            churned_items.append(item)

    return jsonify(
        {
            "target_username": target,
            "kind": "followers" if kind == "followers" else "following",
            "window_days": days,
            "active_count": len(active_items),
            "churned_count": len(churned_items),
            "active": active_items,
            "churned": churned_items,
        }
    )


@app.route("/api/summary", methods=["GET"])
def api_summary():
    conn = _get_db()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM runs")
        total_runs = cur.fetchone()[0] if cur else 0
        total_targets = len(_get_targets(conn))
        cur = conn.execute(
            "SELECT timestamp FROM runs ORDER BY timestamp DESC, id DESC LIMIT 1"
        )
        last_ts = cur.fetchone()[0] if cur else None
        perf = conn.execute(
            """
            SELECT MIN(duration_seconds), MAX(duration_seconds), AVG(duration_seconds)
            FROM runs
            WHERE duration_seconds IS NOT NULL
            """
        ).fetchone()
        fastest = perf[0] if perf else None
        slowest = perf[1] if perf else None
        avg = int(perf[2]) if perf and perf[2] is not None else None
        return jsonify(
            {
                "total_runs": total_runs,
                "total_targets": total_targets,
                "last_run": last_ts,
                "avg_duration_seconds": avg,
                "fastest_duration_seconds": fastest,
                "slowest_duration_seconds": slowest,
            }
        )
    finally:
        conn.close()


@app.route("/api/run/<int:run_id>", methods=["GET"])
def api_run_detail(run_id):
    conn = _get_db()
    try:
        cur = conn.execute(
            """
            SELECT id, target_username, login_username, timestamp,
                   followers_count, followees_count, non_followbacks_count,
                   followers_added, followers_removed, followees_added, followees_removed,
                   duration_seconds, followers_fetch_seconds, followees_fetch_seconds,
                   followers_rate, followees_rate, confidence_score, confidence_flag,
                   followers_collected_count, followees_collected_count, snapshot_complete, snapshot_note
            FROM runs WHERE id = ?
            """,
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        run = dict(row)
        followers = [r[0] for r in conn.execute("SELECT username FROM run_followers WHERE run_id = ? ORDER BY username", (run_id,))]
        followees = [r[0] for r in conn.execute("SELECT username FROM run_followees WHERE run_id = ? ORDER BY username", (run_id,))]
        run["followers"] = followers
        run["followees"] = followees
        run["non_followbacks"] = sorted(set(followees) - set(followers))

        prev_cur = conn.execute(
            """
            SELECT id FROM runs
            WHERE target_username = ?
              AND (timestamp < ? OR (timestamp = ? AND id < ?))
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            (run["target_username"], run["timestamp"], run["timestamp"], run_id),
        )
        prev_row = prev_cur.fetchone()
        if prev_row:
            prev_id = prev_row[0]
            prev_followers = {r[0] for r in conn.execute("SELECT username FROM run_followers WHERE run_id = ?", (prev_id,))}
            prev_followees = {r[0] for r in conn.execute("SELECT username FROM run_followees WHERE run_id = ?", (prev_id,))}
        else:
            prev_followers = set()
            prev_followees = set()

        cur_f = set(followers)
        cur_fe = set(followees)
        run["followers_added_list"] = sorted(cur_f - prev_followers)
        run["followers_removed_list"] = sorted(prev_followers - cur_f)
        run["followees_added_list"] = sorted(cur_fe - prev_followees)
        run["followees_removed_list"] = sorted(prev_followees - cur_fe)
        # Keep headline delta counters aligned with the same computed baseline
        # used by the detailed username lists (important when prior runs were deleted).
        run["followers_added"] = len(run["followers_added_list"])
        run["followers_removed"] = len(run["followers_removed_list"])
        run["followees_added"] = len(run["followees_added_list"])
        run["followees_removed"] = len(run["followees_removed_list"])
        follower_change_names = sorted(set(run["followers_added_list"]) | set(run["followers_removed_list"]))
        followee_change_names = sorted(set(run["followees_added_list"]) | set(run["followees_removed_list"]))
        follower_hist = _load_relationship_history_rows(
            conn,
            table="followers_history",
            target_username=run["target_username"],
            usernames=follower_change_names,
        )
        followee_hist = _load_relationship_history_rows(
            conn,
            table="followees_history",
            target_username=run["target_username"],
            usernames=followee_change_names,
        )
        run["followers_added_details"] = _build_relationship_change_details(
            run["followers_added_list"],
            follower_hist,
            relation_type="followers",
            event_type="added",
            observed_at=run["timestamp"],
            run_id=run_id,
        )
        run["followers_removed_details"] = _build_relationship_change_details(
            run["followers_removed_list"],
            follower_hist,
            relation_type="followers",
            event_type="removed",
            observed_at=run["timestamp"],
            run_id=run_id,
        )
        run["followees_added_details"] = _build_relationship_change_details(
            run["followees_added_list"],
            followee_hist,
            relation_type="following",
            event_type="added",
            observed_at=run["timestamp"],
            run_id=run_id,
        )
        run["followees_removed_details"] = _build_relationship_change_details(
            run["followees_removed_list"],
            followee_hist,
            relation_type="following",
            event_type="removed",
            observed_at=run["timestamp"],
            run_id=run_id,
        )
        run["relationship_events"] = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, target_username, login_username, username,
                       relation_type, event_type, observed_at, run_id, prev_run_id
                FROM relationship_events
                WHERE run_id = ?
                ORDER BY relation_type, event_type, username
                """,
                (run_id,),
            ).fetchall()
        ]
        return jsonify(run)
    finally:
        conn.close()


def _load_relationship_history_rows(conn, *, table, target_username, usernames):
    if not usernames:
        return {}
    names = sorted({u for u in usernames if u})
    if not names:
        return {}
    placeholders = ",".join(["?"] * len(names))
    query = f"""
        SELECT username, first_seen, last_seen, first_seen_run_id, last_seen_run_id,
               first_seen_known, active, unfollowed_at
        FROM {table}
        WHERE target_username = ? AND username IN ({placeholders})
    """
    rows = conn.execute(query, (target_username, *names)).fetchall()
    return {row["username"]: dict(row) for row in rows}


def _build_relationship_change_details(usernames, history_map, *, relation_type, event_type, observed_at, run_id):
    details = []
    for username in usernames:
        row = history_map.get(username) or {}
        details.append(
            {
                "username": username,
                "relation_type": relation_type,
                "event_type": event_type,
                "observed_at": observed_at,
                "run_id": run_id,
                "first_seen": row.get("first_seen"),
                "last_seen": row.get("last_seen"),
                "first_seen_run_id": row.get("first_seen_run_id"),
                "last_seen_run_id": row.get("last_seen_run_id"),
                "first_seen_known": row.get("first_seen_known"),
                "active": row.get("active"),
                "unfollowed_at": row.get("unfollowed_at"),
            }
        )
    return details


@app.route("/api/relationship_events", methods=["GET"])
def api_relationship_events():
    target = (request.args.get("target") or "").strip()
    if not target:
        return jsonify({"error": "target is required"}), 400
    relation_type = (request.args.get("relation_type") or "").strip().lower()
    if relation_type and relation_type not in {"followers", "following"}:
        return jsonify({"error": "relation_type must be followers or following"}), 400
    event_type = (request.args.get("event_type") or "").strip().lower()
    if event_type and event_type not in {"added", "removed"}:
        return jsonify({"error": "event_type must be added or removed"}), 400
    username = (request.args.get("username") or "").strip()
    run_id_raw = (request.args.get("run_id") or "").strip()
    observed_from = (request.args.get("observed_from") or "").strip()
    observed_to = (request.args.get("observed_to") or "").strip()
    run_id = None
    if run_id_raw:
        try:
            run_id = int(run_id_raw)
        except Exception:
            return jsonify({"error": "run_id must be an integer"}), 400
    try:
        limit = max(1, min(int(request.args.get("limit", 200)), 1000))
    except Exception:
        limit = 200
    where = ["target_username = ?"]
    params = [target]
    if relation_type:
        where.append("relation_type = ?")
        params.append(relation_type)
    if event_type:
        where.append("event_type = ?")
        params.append(event_type)
    if username:
        where.append("LOWER(username) LIKE ?")
        params.append(f"%{username.lower()}%")
    if run_id is not None:
        where.append("run_id = ?")
        params.append(run_id)
    if observed_from:
        where.append("observed_at >= ?")
        params.append(observed_from)
    if observed_to:
        where.append("observed_at <= ?")
        params.append(observed_to)
    params.append(limit)
    conn = _get_db()
    try:
        query = f"""
            SELECT id, target_username, login_username, username,
                   relation_type, event_type, observed_at, run_id, prev_run_id
            FROM relationship_events
            WHERE {' AND '.join(where)}
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
        """
        rows = [dict(r) for r in conn.execute(query, tuple(params)).fetchall()]
        return jsonify(rows)
    finally:
        conn.close()


@app.route("/api/relationship_history", methods=["GET"])
def api_relationship_history():
    target = (request.args.get("target") or "").strip()
    if not target:
        return jsonify({"error": "target is required"}), 400
    relation_type = (request.args.get("relation_type") or "").strip().lower()
    table_map = {"followers": "followers_history", "following": "followees_history"}
    table = table_map.get(relation_type)
    if not table:
        return jsonify({"error": "relation_type must be followers or following"}), 400
    username = (request.args.get("username") or "").strip()
    active_filter = (request.args.get("active") or "").strip().lower()
    try:
        limit = max(1, min(int(request.args.get("limit", 500)), 5000))
    except Exception:
        limit = 500
    where = ["target_username = ?"]
    params = [target]
    if username:
        where.append("username = ?")
        params.append(username)
    if active_filter in {"1", "true", "yes", "on"}:
        where.append("active = 1")
    elif active_filter in {"0", "false", "no", "off"}:
        where.append("active = 0")
    params.append(limit)
    conn = _get_db()
    try:
        query = f"""
            SELECT target_username, username, first_seen, last_seen,
                   first_seen_run_id, last_seen_run_id, first_seen_known,
                   active, unfollowed_at
            FROM {table}
            WHERE {' AND '.join(where)}
            ORDER BY last_seen DESC, username ASC
            LIMIT ?
        """
        rows = [dict(r) for r in conn.execute(query, tuple(params)).fetchall()]
        return jsonify(rows)
    finally:
        conn.close()


def _is_target_busy(target_username: str, *, exclude_job_id: str | None = None, include_queued: bool = True) -> bool:
    normalized_target = str(target_username or "").strip().lower()
    if not normalized_target:
        return False
    exclude = str(exclude_job_id or "").strip()
    for job in ACTIVE_JOBS.values():
        if str(job.get("target_username") or "").strip().lower() == normalized_target:
            if exclude and str(job.get("job_id") or "").strip() == exclude:
                continue
            return True
    for jid, meta in RUN_META.items():
        if exclude and str(jid) == exclude:
            continue
        fut = RUN_FUTURES.get(jid)
        if fut and not fut.done() and str(meta.get("target_username") or "").strip().lower() == normalized_target:
            return True
    statuses = ["running"]
    if include_queued:
        statuses.append("queued")
    for row in _list_run_jobs_by_status(*statuses, limit=500):
        if exclude and str(row.get("job_id") or "") == exclude:
            continue
        if str(row.get("target_username") or "").strip().lower() == normalized_target:
            return True
    return False


def _execute_run_job(job_id: str, login_username: str, target_username: str, source: str):
    started = datetime.now(LOCAL_TZ).isoformat()
    meta = RUN_META.setdefault(
        job_id,
        {
            "login_username": login_username,
            "target_username": target_username,
            "submitted_at": started,
            "source": source,
        },
    )
    meta["started_at"] = started
    _run_state_set(job_id, "running")
    inputs = RUN_INPUTS.get(job_id) or {}
    two_factor_code = inputs.get("two_factor_code")
    challenge_code = inputs.get("challenge_code")
    try:
        res = guarded_run(
            login_username,
            target_username,
            source=source,
            job_id=job_id,
            two_factor_code=two_factor_code,
            challenge_code=challenge_code,
        )
        finished = datetime.now(LOCAL_TZ).isoformat()
        try:
            elapsed = int((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds())
            if isinstance(res, dict) and res.get("run_id"):
                update_run_duration(DB_PATH_DEFAULT, res["run_id"], elapsed)
                conn = _get_db()
                try:
                    _update_run_quality(conn, res["run_id"], login_username, target_username)
                finally:
                    conn.close()
        except Exception:
            pass
        job = ACTIVE_JOBS.get(login_username)
        if job and isinstance(res, dict):
            job["followers_total"] = res.get("followers_count")
            job["following_total"] = res.get("followees_count")
            job["phase"] = "done"
        _run_state_set(job_id, "done", finished_at=finished)
        _update_run_job_record(
            job_id,
            status="done",
            finished_at=finished,
            state_reason=None,
            error_code=None,
            error_message=None,
            cooldown_seconds=None,
            result=res if isinstance(res, dict) else {"value": res},
        )
        _record_run_job_event(job_id, "done", {"finished_at": finished})
        _resolve_manual_actions_for(login_username, target_username, note=f"run succeeded ({job_id})")
        _clear_run_cooldown(login_username)
        _whatsapp_notify_run_event(
            "run_done",
            login_username,
            target_username,
            f"job_id: {job_id}",
        )
        return {"status": "success", "started_at": started, "finished_at": finished, "result": res}
    except Exception as exc:  # noqa: BLE001
        finished = datetime.now(LOCAL_TZ).isoformat()
        code = getattr(exc, "code", None)
        cooldown_seconds = 0
        if str(code or "").strip().lower() in {"rate_limited", "feedback_required"}:
            cooldown_seconds = _set_run_cooldown(login_username, code, str(exc))
        terminal_state = _classify_terminal_run_state(code, str(exc), cooldown_seconds)
        _run_state_set(
            job_id,
            terminal_state,
            reason=str(code or exc.__class__.__name__),
            finished_at=finished,
            cooldown_seconds=cooldown_seconds if cooldown_seconds > 0 else None,
        )
        _update_run_job_record(
            job_id,
            status=terminal_state,
            finished_at=finished,
            state_reason=str(code or exc.__class__.__name__),
            error_code=str(code or "") or None,
            error_message=str(exc),
            cooldown_seconds=cooldown_seconds if cooldown_seconds > 0 else None,
        )
        _record_run_job_event(
            job_id,
            terminal_state,
            {
                "finished_at": finished,
                "error": str(exc),
                "error_code": str(code or ""),
                "cooldown_seconds": cooldown_seconds,
            },
        )
        if terminal_state in {"challenge_required", "manual_required", "blocked"}:
            action_type = str(code or terminal_state).strip().lower()
            if action_type not in {"challenge_required", "two_factor_required"}:
                action_type = terminal_state
            _enqueue_manual_action(
                login_username=login_username,
                target_username=target_username,
                job_id=job_id,
                action_type=action_type,
                reason=terminal_state,
                error_code=str(code or ""),
                error_message=str(exc),
            )
        _whatsapp_notify_run_event(
            "run_error",
            login_username,
            target_username,
            (
                f"job_id: {job_id} · error: {exc}"
                + (f" · cooldown: {cooldown_seconds}s" if cooldown_seconds > 0 else "")
            ),
        )
        payload = {"status": "error", "started_at": started, "finished_at": finished, "error": str(exc)}
        if code:
            payload["error_code"] = code
        if cooldown_seconds > 0:
            payload["cooldown_seconds"] = cooldown_seconds
        return payload
    finally:
        RUN_INPUTS.pop(job_id, None)


def _run_dispatcher_loop():
    while not RUN_DISPATCH_STOP.is_set():
        dispatched = False
        try:
            for row in _claim_next_run_jobs(limit=25):
                if RUN_DISPATCH_STOP.is_set():
                    break
                job_id = str(row.get("job_id") or "")
                login_username = str(row.get("login_username") or "").strip()
                target_username = str(row.get("target_username") or "").strip()
                source = str(row.get("source") or "api").strip() or "api"
                if not job_id or not login_username or not target_username:
                    continue
                if _get_run_cooldown_remaining(login_username) > 0:
                    continue
                if UNFOLLOW_LOCK.locked() and str(UNFOLLOW_JOB.get("login_username") or "") == login_username:
                    continue
                if _get_run_lock(login_username).locked():
                    continue
                if _is_target_busy(target_username, exclude_job_id=job_id, include_queued=False):
                    continue
                if not _mark_run_job_running(job_id):
                    continue
                started_at = datetime.now(LOCAL_TZ).isoformat()
                RUN_META[job_id] = {
                    "login_username": login_username,
                    "target_username": target_username,
                    "submitted_at": row.get("submitted_at") or started_at,
                    "started_at": started_at,
                    "state": "running",
                    "source": source,
                }
                _run_state_set(job_id, "running")
                RUN_FUTURES[job_id] = executor.submit(
                    _execute_run_job,
                    job_id,
                    login_username,
                    target_username,
                    source,
                )
                dispatched = True
        except Exception as exc:  # noqa: BLE001
            print(f"[run-dispatcher] loop error: {exc}", file=sys.stderr)
        if dispatched:
            time.sleep(0.25)
        else:
            time.sleep(1.0)


threading.Thread(target=_run_dispatcher_loop, daemon=True).start()


def _queue_run(login_username, target_username, *, source="api", two_factor_code=None, challenge_code=None, rebuild=True):
    job_id = str(uuid4())
    LAST_JOB_BY_LOGIN[login_username] = job_id
    if source == "whatsapp":
        _whatsapp_notify_run_event(
            "run_queued",
            login_username,
            target_username,
            f"job_id: {job_id}",
        )
    _create_run_job_record(
        job_id=job_id,
        login_username=login_username,
        target_username=target_username,
        source=source,
    )
    RUN_META[job_id] = {
        "login_username": login_username,
        "target_username": target_username,
        "submitted_at": datetime.now(LOCAL_TZ).isoformat(),
        "state": "queued",
        "source": source,
    }
    _run_state_set(job_id, "queued")
    if two_factor_code or challenge_code:
        RUN_INPUTS[job_id] = {
            "two_factor_code": two_factor_code,
            "challenge_code": challenge_code,
        }
    return job_id


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True)
    login_username = data.get("login_username")
    target_username = data.get("target_username")
    two_factor_code = data.get("two_factor_code") or data.get("twoFactorCode")
    challenge_code = data.get("challenge_code") or data.get("challengeCode")
    rebuild = data.get("rebuild", True)
    rebuild = bool(rebuild)
    if not login_username or not target_username:
        return jsonify({"error": "login_username and target_username are required"}), 400
    if _is_blocked_login(login_username):
        return jsonify({"error": "login_username is blocked"}), 400
    cooldown_seconds = _get_run_cooldown_remaining(login_username)
    if cooldown_seconds > 0:
        return (
            jsonify(
                {
                    "error": "login is in cooldown after Instagram throttle response",
                    "error_code": "rate_limited",
                    "login_username": login_username,
                    "cooldown_seconds": cooldown_seconds,
                }
            ),
            429,
        )

    # refuse if this login already running, or unfollow is running on same login
    if _get_run_lock(login_username).locked():
        return jsonify({"error": "another run is already in progress for this login"}), 429
    if _is_target_busy(target_username):
        return jsonify({"error": "another run is already in progress for this target"}), 429
    if UNFOLLOW_LOCK.locked():
        unfollow_login = UNFOLLOW_JOB.get("login_username")
        if unfollow_login and login_username == unfollow_login:
            return jsonify({"error": "unfollow job is running for this login"}), 429

    job_id = _queue_run(
        login_username,
        target_username,
        source="api",
        two_factor_code=two_factor_code,
        challenge_code=challenge_code,
        rebuild=rebuild,
    )
    return jsonify({"job_id": job_id})


@app.route("/api/run/<job_id>", methods=["GET"])
def api_run_status(job_id):
    record = _load_run_job_record(job_id)
    if not record:
        return jsonify({"error": "unknown job_id"}), 404
    fut = RUN_FUTURES.get(job_id)
    merged_meta = _run_record_meta(record) or {}
    if RUN_META.get(job_id):
        merged_meta.update(RUN_META.get(job_id) or {})
        merged_meta["state"] = record.get("status")
    terminal = str(record.get("status") or "").strip().lower() in RUN_STATE_TERMINAL
    if fut and fut.done():
        try:
            payload = fut.result()
        except Exception as exc:  # noqa: BLE001
            payload = {"status": "error", "error": str(exc)}
        return jsonify({"done": True, "payload": payload, "meta": merged_meta})
    if terminal:
        payload = {
            "status": "success" if record.get("status") == "done" else "error",
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
        }
        if record.get("result"):
            payload["result"] = record.get("result")
        if record.get("error_message"):
            payload["error"] = record.get("error_message")
        if record.get("error_code"):
            payload["error_code"] = record.get("error_code")
        if record.get("cooldown_seconds"):
            payload["cooldown_seconds"] = record.get("cooldown_seconds")
        return jsonify({"done": True, "payload": payload, "meta": merged_meta})
    return jsonify({"done": False, "meta": merged_meta})


@app.route("/api/jobs/<job_id>/detail", methods=["GET"])
def api_job_detail(job_id):
    job_dir = JOB_TMP_DIR / f"job_{job_id}"
    if not job_dir.exists():
        return jsonify({"error": "job not found"}), 404
    progress_path = job_dir / "progress.json"
    result_path = job_dir / "result.json"
    out_path = job_dir / "worker.out"
    err_path = job_dir / "worker.err"
    trace_path = job_dir / "trace.jsonl"
    return jsonify(
        {
            "job_id": job_id,
            "progress": _read_json_file(progress_path),
            "result": _read_json_file(result_path),
            "worker_out_tail": _tail_file(out_path, lines=30),
            "worker_err_tail": _tail_file(err_path, lines=30),
            "trace_tail": _tail_file(trace_path, lines=40),
        }
    )


@app.route("/api/jobs/latest", methods=["GET"])
def api_job_latest():
    login_username = (request.args.get("login_username") or "").strip()
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    latest = _latest_run_job_for_login(login_username)
    if not latest:
        return jsonify({"error": "no job found for login"}), 404
    job_id = str(latest.get("job_id") or "")
    if not job_id:
        return jsonify({"error": "no job found for login"}), 404
    job_dir = JOB_TMP_DIR / f"job_{job_id}"
    payload = {
        "job_id": job_id,
        "meta": _run_record_meta(latest),
        "status": latest.get("status"),
        "db_result": None,
    }
    raw_result = latest.get("result_json")
    if raw_result:
        try:
            payload["db_result"] = json.loads(raw_result)
        except Exception:
            payload["db_result"] = None
    if not job_dir.exists():
        return jsonify(payload)
    progress_path = job_dir / "progress.json"
    result_path = job_dir / "result.json"
    out_path = job_dir / "worker.out"
    err_path = job_dir / "worker.err"
    trace_path = job_dir / "trace.jsonl"
    payload.update(
        {
            "progress": _read_json_file(progress_path),
            "result": _read_json_file(result_path),
            "worker_out_tail": _tail_file(out_path, lines=60),
            "worker_err_tail": _tail_file(err_path, lines=60),
            "trace_tail": _tail_file(trace_path, lines=80),
        }
    )
    return jsonify(payload)


@app.route("/api/status", methods=["GET"])
def api_status():
    """Current system status across runs."""
    conn = _get_db()
    active_jobs = []
    active_job_ids = set()
    active_logins = set()
    for login, job in list(ACTIVE_JOBS.items()):
        try:
            started = datetime.fromisoformat(job["started_at"])
            elapsed = int((datetime.now(LOCAL_TZ) - started).total_seconds())
        except Exception:
            elapsed = 0
        avg = _avg_duration_seconds(conn, job.get("login_username"), job.get("target_username"))
        eta = max(avg - elapsed, 0) if avg is not None else None
        item = {**job, "elapsed_seconds": elapsed, "avg_duration_seconds": avg, "eta_seconds": eta}
        active_jobs.append(item)
        active_logins.add(job["login_username"])
        if job.get("job_id"):
            active_job_ids.add(job["job_id"])

    queued_rows = _list_run_jobs_by_status("queued", limit=500)
    running_rows = _list_run_jobs_by_status("running", limit=500)
    queued = [{"job_id": row.get("job_id"), "meta": _run_record_meta(row)} for row in queued_rows]

    # If we have DB running rows without ACTIVE_JOBS, surface them as running.
    for row in running_rows:
        job_id = str(row.get("job_id") or "")
        if not job_id or job_id in active_job_ids:
            continue
        meta = _run_record_meta(row) or {}
        started_at = meta.get("started_at") or meta.get("submitted_at")
        try:
            started = datetime.fromisoformat(started_at) if started_at else None
            elapsed = int((datetime.now(LOCAL_TZ) - started).total_seconds()) if started else 0
        except Exception:
            elapsed = 0
        avg = _avg_duration_seconds(conn, meta.get("login_username"), meta.get("target_username"))
        eta = max(avg - elapsed, 0) if avg is not None else None
        active_jobs.append(
            {
                "source": "api",
                "login_username": meta.get("login_username"),
                "target_username": meta.get("target_username"),
                "started_at": started_at,
                "cancelled": False,
                "job_id": job_id,
                "elapsed_seconds": elapsed,
                "avg_duration_seconds": avg,
                "eta_seconds": eta,
            }
        )
        active_logins.add(meta.get("login_username"))

    manual_actions = _open_manual_actions()
    cooldown_entries = []
    now_ts = time.time()
    with RUN_COOLDOWN_GUARD:
        stale_logins = []
        for login, payload in RUN_COOLDOWN_UNTIL.items():
            until_ts = float(payload.get("until_ts") or 0)
            remaining = int(round(until_ts - now_ts))
            if remaining <= 0:
                stale_logins.append(login)
                continue
            cooldown_entries.append(
                {
                    "login_username": login,
                    "cooldown_seconds": remaining,
                    "error_code": payload.get("error_code"),
                    "error_message": payload.get("error_message"),
                }
            )
        for login in stale_logins:
            RUN_COOLDOWN_UNTIL.pop(login, None)
    if active_jobs:
        state = "running"
    elif queued:
        state = "queued"
    elif manual_actions:
        state = "manual_required"
    elif cooldown_entries:
        state = "cooling_down"
    else:
        state = "idle"

    payload = {
        "state": state,
        "active_jobs": active_jobs,
        "active_logins": list(active_logins),
        "queued_jobs": queued,
        "queued_logins": [q["meta"].get("login_username") for q in queued],
        "manual_actions": manual_actions,
        "manual_actions_open": len(manual_actions),
        "cooldowns": cooldown_entries,
        "unfollow": _unfollow_snapshot(),
    }
    # Backward-compatible keys for UI that expects single job/queued list
    if len(active_jobs) == 1:
        payload["job"] = active_jobs[0]
        payload["elapsed_seconds"] = active_jobs[0]["elapsed_seconds"]
    if queued:
        payload["jobs"] = queued
    conn.close()
    return jsonify(payload)


@app.route("/api/manual/actions", methods=["GET"])
def api_manual_actions():
    return jsonify({"actions": _open_manual_actions()})


@app.route("/api/manual/actions/<action_id>/resolve", methods=["POST"])
def api_manual_action_resolve(action_id):
    data = request.get_json(silent=True) or {}
    note = str(data.get("note") or "").strip() or None
    updated = None
    now_iso = datetime.now(LOCAL_TZ).isoformat()
    with MANUAL_ACTIONS_LOCK:
        for key, action in MANUAL_ACTIONS.items():
            if str(action.get("action_id") or "") != str(action_id):
                continue
            action["state"] = "resolved"
            action["resolved_at"] = now_iso
            action["updated_at"] = now_iso
            if note:
                action["resolution_note"] = note
            MANUAL_ACTIONS[key] = action
            updated = dict(action)
            break
    if not updated:
        return jsonify({"error": "manual action not found"}), 404
    return jsonify({"ok": True, "action": updated})


@app.route("/api/health", methods=["GET"])
def api_health():
    checks = {
        "db": _health_check_db(),
        "scheduler": _health_check_scheduler(),
        "files": _health_check_files(),
    }
    status = _merge_health_status(checks)
    return jsonify({"status": status, "checks": checks})


@app.route("/api/health/detail", methods=["GET"])
def api_health_detail():
    checks = {
        "db": _health_check_db(),
        "files": _health_check_files(),
        "scheduler": _health_check_scheduler(),
        "monitor": _health_check_monitor(),
        "sessions": _health_check_sessions(),
        "scraper": _health_check_scraper(),
        "runs": _health_check_runs(),
        "unfollow": _health_check_unfollow(),
    }
    status = _merge_health_status(checks)
    return jsonify({"status": status, "checks": checks})


@app.route("/api/monitor/status", methods=["GET"])
def api_monitor_status():
    conn = _get_db()
    try:
        targets = _get_targets(conn)
        per_target = []
        for target in targets:
            cur = conn.execute(
                """
                SELECT id, target_username, login_username, timestamp,
                       followers_count, followees_count, run_requested,
                       triggered_run_id, trigger_reason
                FROM count_checks
                WHERE target_username = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (target,),
            )
            row = cur.fetchone()
            if row:
                per_target.append(dict(row))
        cur2 = conn.execute(
            """
            SELECT id, target_username, login_username, timestamp,
                   followers_count, followees_count, run_requested,
                   triggered_run_id, trigger_reason
            FROM count_checks
            ORDER BY id DESC
            LIMIT 1
            """
        )
        overall = cur2.fetchone()
        return jsonify({"targets": per_target, "overall": dict(overall) if overall else None})
    finally:
        conn.close()


@app.route("/api/config", methods=["GET"])
def api_config_get():
    cfg = _get_config(force=True)
    return jsonify({"config": _mask_config_for_api(cfg), "defaults": _mask_config_for_api(CONFIG_DEFAULTS)})


@app.route("/api/config", methods=["PUT"])
def api_config_update():
    data = request.get_json(force=True) or {}
    if not isinstance(data, dict) or not data:
        return jsonify({"error": "config payload required"}), 400
    apply_backend_profile = False
    profile_applied_backend = None
    try:
        cleaned = {}
        for key, value in data.items():
            if key == "_apply_backend_profile":
                apply_backend_profile = _parse_bool(value)
                continue
            if key in SENSITIVE_CONFIG_KEYS and (value is None or str(value).strip() == ""):
                continue
            cleaned[key] = value
        if "run_scraper_backend" in cleaned:
            cleaned["run_scraper_backend"] = _normalize_scraper_backend(cleaned.get("run_scraper_backend"))
        if apply_backend_profile:
            profile_applied_backend = str(
                cleaned.get("run_scraper_backend", _get_config_value("run_scraper_backend", "browser"))
            )
            cleaned.update(_backend_tuning_profile(profile_applied_backend))
        # Keep legacy + new timeout keys aligned for older callers.
        if "run_request_timeout" in cleaned and "run_http_timeout_seconds" not in cleaned:
            cleaned["run_http_timeout_seconds"] = cleaned["run_request_timeout"]
        if "run_http_timeout_seconds" in cleaned and "run_request_timeout" not in cleaned:
            cleaned["run_request_timeout"] = cleaned["run_http_timeout_seconds"]
        if _parse_bool(cleaned.get("proxy_enabled", _get_config_value("proxy_enabled", False))):
            host = cleaned.get("proxy_host") or _get_config_value("proxy_host", "")
            port = cleaned.get("proxy_port") or _get_config_value("proxy_port", 0)
            user = cleaned.get("proxy_username") or _get_config_value("proxy_username", "")
            user_pool = cleaned.get("proxy_username_pool") or _get_config_value("proxy_username_pool", "")
            pwd = cleaned.get("proxy_password") or _get_config_value("proxy_password", "")
            has_pool = bool(str(user_pool or "").strip())
            if not host or not port or (not user and not has_pool) or not pwd:
                raise ValueError("Proxy enabled requires host, port, password, and username or username pool")
        updated = _set_config_values(cleaned)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        _schedule_monitor_job()
    except Exception:
        pass
    try:
        _schedule_recon_maintenance_job()
    except Exception:
        pass
    cfg = _get_config(force=True)
    return jsonify(
        {
            "updated": list(updated.keys()),
            "profile_applied_backend": profile_applied_backend,
            "config": _mask_config_for_api(cfg),
        }
    )


@app.route("/api/integrations/whatsapp/webhook", methods=["GET"])
def api_whatsapp_webhook_verify():
    cfg = _get_config()
    verify_token = str(cfg.get("whatsapp_verify_token") or "").strip()
    mode = request.args.get("hub.mode", "")
    token = request.args.get("hub.verify_token", "")
    challenge = request.args.get("hub.challenge", "")
    if mode == "subscribe" and verify_token and token == verify_token:
        return Response(challenge, mimetype="text/plain")
    return jsonify({"error": "verification failed"}), 403


@app.route("/api/integrations/whatsapp/webhook", methods=["POST"])
def api_whatsapp_webhook_receive():
    cfg = _get_config()
    if not _whatsapp_is_configured(cfg):
        return jsonify({"ok": True, "ignored": "integration disabled"}), 200
    payload = request.get_json(silent=True) or {}
    try:
        entries = payload.get("entry") or []
        for entry in entries:
            changes = (entry or {}).get("changes") or []
            for change in changes:
                value = (change or {}).get("value") or {}
                messages = value.get("messages") or []
                for msg in messages:
                    sender = _normalize_whatsapp_number(msg.get("from"))
                    mtype = str(msg.get("type") or "").strip().lower()
                    if mtype != "text":
                        continue
                    text_body = ((msg.get("text") or {}).get("body") or "").strip()
                    if not text_body:
                        continue
                    reply = _whatsapp_handle_command(sender, text_body)
                    _whatsapp_send_text(sender, reply, cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[whatsapp] webhook processing error: {exc}", file=sys.stderr)
    return jsonify({"ok": True})


@app.route("/api/integrations/whatsapp/test", methods=["POST"])
def api_whatsapp_test():
    cfg = _get_config()
    to_number = _normalize_whatsapp_number((request.get_json(silent=True) or {}).get("to") or cfg.get("whatsapp_notify_to"))
    message = str((request.get_json(silent=True) or {}).get("message") or "InstaLab WhatsApp test").strip()
    if not to_number:
        return jsonify({"error": "destination number missing (set whatsapp_notify_to or pass {to})"}), 400
    ok, detail = _whatsapp_send_text(to_number, message, cfg)
    if not ok:
        return jsonify({"ok": False, "error": detail}), 502
    return jsonify({"ok": True, "to": to_number})


@app.route("/api/collector/auth/status", methods=["GET"])
def api_collector_auth_status():
    login_username = str(request.args.get("login_username") or "").strip()
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    storage_path = _collector_storage_path(login_username)
    storage_file = Path(storage_path)
    return jsonify(
        {
            "login_username": login_username,
            "scraper_backend": _normalize_scraper_backend(_get_config_value("run_scraper_backend", "browser")),
            "storage_path": storage_path,
            "storage_exists": storage_file.exists(),
            "auth_ready": ensure_auth_state(storage_path),
            "storage_mtime": storage_file.stat().st_mtime if storage_file.exists() else None,
        }
    )


@app.route("/api/collector/auth/init", methods=["POST"])
def api_collector_auth_init():
    data = request.get_json(force=True) or {}
    login_username = str(data.get("login_username") or "").strip()
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    try:
        max_wait_seconds = int(data.get("max_wait_seconds") or 300)
    except Exception:
        max_wait_seconds = 300
    storage_path = _collector_storage_path(login_username)
    proxy = _get_proxy_config(session_id=_generate_proxy_session_id())
    executor.submit(
        init_login,
        storage_path,
        proxy.get("server") if proxy.get("enabled") else None,
        proxy.get("username"),
        proxy.get("password"),
        max_wait_seconds,
    )
    return jsonify(
        {
            "started": True,
            "note": "interactive collector login opened",
            "login_username": login_username,
            "storage_path": storage_path,
        }
    )


@app.route("/api/unfollow/status", methods=["GET"])
def api_unfollow_status():
    try:
        login_username = _resolve_unfollow_login(request.args.get("login_username"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not login_username:
        return jsonify({"error": "no login_username available"}), 400
    conn = _get_db()
    try:
        run, non_followbacks = _get_non_followbacks_for(conn, login_username)
        already_unfollowed = set()
        eligible = non_followbacks
        if run:
            since_dt = _parse_snapshot_ts(run["timestamp"])
            already_unfollowed = _get_unfollowed_since(conn, run["login_username"], run["target_username"], since_dt)
            eligible = [u for u in non_followbacks if u not in already_unfollowed]
        success_rate = _unfollow_success_rate(conn, run["login_username"], run["target_username"]) if run else None
        suggested_max = _suggest_unfollow_batch(len(eligible), success_rate)
    finally:
        conn.close()
    return jsonify(
        {
            "auth_ready": ensure_auth_state(UNFOLLOW_STORAGE),
            "latest_run": run,
            "non_followbacks_count": len(non_followbacks),
            "eligible_count": len(eligible),
            "already_unfollowed_count": len(already_unfollowed),
            "suggested_max": suggested_max,
            "job": _unfollow_snapshot(),
            "login_username": login_username,
            "log": UNFOLLOW_LOG[:20],
        }
    )


@app.route("/api/unfollow/preview", methods=["GET"])
def api_unfollow_preview():
    try:
        login_username = _resolve_unfollow_login(request.args.get("login_username"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not login_username:
        return jsonify({"error": "no login_username available"}), 400
    conn = _get_db()
    try:
        run, non_followbacks = _get_non_followbacks_for(conn, login_username)
        already_unfollowed = set()
        eligible = non_followbacks
        if run:
            since_dt = _parse_snapshot_ts(run["timestamp"])
            already_unfollowed = _get_unfollowed_since(conn, run["login_username"], run["target_username"], since_dt)
            eligible = [u for u in non_followbacks if u not in already_unfollowed]
        success_rate = _unfollow_success_rate(conn, run["login_username"], run["target_username"]) if run else None
        suggested_max = _suggest_unfollow_batch(len(eligible), success_rate)
    finally:
        conn.close()
    return jsonify(
        {
            "latest_run": run,
            "count": len(eligible),
            "sample": eligible[:20],
            "total_non_followbacks": len(non_followbacks),
            "already_unfollowed_count": len(already_unfollowed),
            "suggested_max": suggested_max,
            "login_username": login_username,
        }
    )


@app.route("/api/unfollow/start", methods=["POST"])
def api_unfollow_start():
    if UNFOLLOW_LOCK.locked():
        return jsonify({"error": "unfollow job already running"}), 409
    data = request.get_json(force=True)
    cfg = _get_config()
    try:
        login_username = _resolve_unfollow_login(data.get("login_username"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not login_username:
        return jsonify({"error": "login_username is required"}), 400
    dry_run = bool(data.get("dry_run", cfg.get("unfollow_dry_run_default", False)))
    max_actions = data.get("max_actions", cfg.get("unfollow_max_per_run", 25))
    delay_min = int(data.get("delay_min", cfg.get("unfollow_delay_min", 25)))
    delay_max = int(data.get("delay_max", cfg.get("unfollow_delay_max", 45)))
    if max_actions is not None:
        try:
            max_actions = int(max_actions)
        except Exception:
            max_actions = None

    conn = _get_db()
    try:
        run, non_followbacks = _get_non_followbacks_for(conn, login_username)
        already_unfollowed = set()
        eligible = non_followbacks
        success_rate = None
        if run:
            since_dt = _parse_snapshot_ts(run["timestamp"])
            already_unfollowed = _get_unfollowed_since(conn, run["login_username"], run["target_username"], since_dt)
            eligible = [u for u in non_followbacks if u not in already_unfollowed]
            success_rate = _unfollow_success_rate(conn, run["login_username"], run["target_username"])
    finally:
        conn.close()
    if not run:
        return jsonify({"error": f"no runs for {login_username} yet"}), 400
    if not eligible:
        return jsonify({"error": "all non-followbacks already unfollowed since last snapshot"}), 400
    if _get_run_lock(run["login_username"]).locked():
        return jsonify({"error": "run already in progress for this login"}), 409

    if not max_actions or max_actions <= 0:
        max_actions = _suggest_unfollow_batch(len(eligible), success_rate)

    if not UNFOLLOW_LOCK.acquire(blocking=False):
        return jsonify({"error": "unfollow job already running"}), 409
    UNFOLLOW_CANCEL.clear()
    UNFOLLOW_JOB["excluded"] = len(non_followbacks) - len(eligible)
    UNFOLLOW_JOB["batch_size"] = max_actions
    executor.submit(
        _unfollow_worker,
        eligible,
        run["login_username"],
        run["target_username"],
        dry_run,
        max_actions,
        delay_min,
        delay_max,
    )
    return jsonify({"started": True, "count": len(non_followbacks)})


@app.route("/api/unfollow/cancel", methods=["POST"])
def api_unfollow_cancel():
    if not UNFOLLOW_LOCK.locked():
        return jsonify({"error": "no unfollow job running"}), 400
    UNFOLLOW_CANCEL.set()
    UNFOLLOW_JOB["state"] = "cancelling"
    UNFOLLOW_JOB["message"] = "cancel requested"
    return jsonify({"cancelled": True})


@app.route("/api/unfollow/init", methods=["POST"])
def api_unfollow_init():
    data = request.get_json(silent=True) or {}
    try:
        max_wait_seconds = int(data.get("max_wait_seconds") or 300)
    except Exception:
        max_wait_seconds = 300
    if UNFOLLOW_LOCK.locked():
        return jsonify({"error": "unfollow job running"}), 409
    # Launch interactive login in background (requires display on server)
    proxy = _get_proxy_config(session_id=_generate_proxy_session_id())
    executor.submit(
        init_login,
        UNFOLLOW_STORAGE,
        proxy.get("server") if proxy.get("enabled") else None,
        proxy.get("username"),
        proxy.get("password"),
        max_wait_seconds,
    )
    return jsonify({"started": True, "note": "interactive login opened"})


@app.route("/api/proxy/test", methods=["POST", "GET"])
def api_proxy_test():
    proxy = _get_proxy_config(session_id=_generate_proxy_session_id())
    if not proxy.get("enabled"):
        return jsonify({"ok": False, "error": "proxy disabled"}), 400
    test_url = _proxy_test_url(proxy.get("provider", "decodo"))
    start = time.monotonic()
    if not proxy.get("host") or not proxy.get("port") or not proxy.get("username") or not proxy.get("password"):
        return jsonify({"ok": False, "error": "proxy credentials incomplete"}), 400

    proxy_url = _build_proxy_url(
        proxy["host"],
        int(proxy["port"]),
        proxy.get("username"),
        proxy.get("password"),
    )
    handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    ctx = ssl._create_unverified_context()
    opener = urllib.request.build_opener(handler, urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(
        test_url,
        headers={
            "User-Agent": "InstaLabProxyCheck/1.0",
            "Accept": "application/json,text/plain",
        },
    )
    try:
        with opener.open(req, timeout=20) as resp:
            body = resp.read(2048).decode("utf-8", errors="ignore").strip()
            latency_ms = int((time.monotonic() - start) * 1000)
            return jsonify(
                {
                    "ok": resp.status == 200,
                    "status": resp.status,
                    "latency_ms": latency_ms,
                    "body": body[:400],
                }
            )
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        return jsonify({"ok": False, "error": str(exc), "latency_ms": latency_ms}), 502


@app.route("/api/run/cancel", methods=["POST"])
def api_run_cancel():
    data = request.get_json(force=True) or {}
    login_username = data.get("login_username")
    target_username = data.get("target_username")
    job_id = data.get("job_id")
    worker_pid = None
    queued_cancelled = False
    record = None
    if job_id:
        record = _load_run_job_record(str(job_id))
    if (not login_username or not target_username) and job_id:
        meta = RUN_META.get(job_id) or _run_record_meta(record) or {}
        login_username = login_username or meta.get("login_username") or (record or {}).get("login_username")
        target_username = target_username or meta.get("target_username") or (record or {}).get("target_username")
        if not login_username or not target_username:
            for login, job in list(ACTIVE_JOBS.items()):
                if job.get("job_id") == job_id:
                    login_username = login_username or job.get("login_username") or login
                    target_username = target_username or job.get("target_username")
                    worker_pid = job.get("worker_pid")
                    break
    if record and str(record.get("status") or "") == "queued":
        _update_run_job_record(
            str(job_id),
            status="cancelled",
            finished_at=datetime.now(LOCAL_TZ).isoformat(),
            cancel_requested=True,
            state_reason="cancel_requested",
            error_message="job cancelled while queued",
        )
        _record_run_job_event(str(job_id), "cancelled", {"reason": "cancel_requested", "phase": "queued"})
        _run_state_set(str(job_id), "cancelled", reason="cancel_requested")
        queued_cancelled = True
    if not login_username or not target_username:
        return jsonify({"error": "login_username and target_username are required"}), 400
    # mark cancel
    CANCEL_REQUESTS.add((login_username, target_username))
    if job_id and record and str(record.get("status") or "") == "running":
        _update_run_job_record(str(job_id), cancel_requested=True, state_reason="cancel_requested")
        _record_run_job_event(str(job_id), "cancel_requested", {"phase": "running"})
    # best-effort: if current active matches, mark cancelled flag
    job = ACTIVE_JOBS.get(login_username)
    if job and job.get("target_username") == target_username:
        job["cancelled"] = True
        worker_pid = job.get("worker_pid") or worker_pid
        if not job_id:
            job_id = job.get("job_id")
    if worker_pid:
        try:
            os.kill(int(worker_pid), signal.SIGTERM)
        except Exception:
            pass
    if job_id and (job_id in RUN_META) and queued_cancelled:
        _run_state_set(job_id, "cancelled", reason="cancel_requested")
    return jsonify({"cancelled": True, "queued_cancelled": queued_cancelled})


@app.route("/api/run/<int:run_id>", methods=["DELETE"])
def api_run_delete(run_id):
    target = _delete_run(run_id)
    if not target:
        return jsonify({"error": "run not found"}), 404
    return jsonify({"deleted": run_id, "target": target, "undo": True})


@app.route("/api/run/undo/<int:run_id>", methods=["POST"])
def api_run_undo(run_id):
    ok, msg = _restore_run(run_id)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"restored": run_id})


@app.route("/api/schedules", methods=["GET"])
def api_schedules():
    jobs = []
    for row in _load_schedules_from_db():
        job = scheduler.get_job(str(row["id"]))
        jobs.append(
            {
                "id": row["id"],
                "login_username": row["login_username"],
                "target_username": row["target_username"],
                "interval": row["interval_expr"],
                "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
            }
        )
    return jsonify(jobs)


@app.route("/api/schedules", methods=["POST"])
def api_schedules_create():
    data = request.get_json(force=True)
    login_username = data.get("login_username")
    target_username = data.get("target_username")
    interval_expr = data.get("interval")  # cron expression, e.g., "0 9,21 * * *"
    if not login_username or not target_username or not interval_expr:
        return jsonify({"error": "login_username, target_username, interval (cron) required"}), 400
    # Validate cron
    try:
        CronTrigger.from_crontab(interval_expr)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"invalid cron expression: {exc}"}), 400

    # Validate credentials exist
    _get_credentials(login_username)

    schedule_id = _persist_schedule(login_username, target_username, interval_expr)
    _schedule_job(schedule_id, login_username, target_username, interval_expr)
    return jsonify({"id": schedule_id})


@app.route("/api/schedules/<int:schedule_id>", methods=["DELETE"])
def api_schedule_delete(schedule_id):
    _delete_schedule(schedule_id)
    job = scheduler.get_job(str(schedule_id))
    if job:
        scheduler.remove_job(str(schedule_id))
    return jsonify({"deleted": schedule_id})


@app.route("/api/schedules/<int:schedule_id>", methods=["PUT"])
def api_schedule_update(schedule_id):
    data = request.get_json(force=True)
    target_username = data.get("target_username")
    interval_expr = data.get("interval")
    if interval_expr is None and target_username is None:
        return jsonify({"error": "nothing to update"}), 400
    if interval_expr is not None:
        try:
            CronTrigger.from_crontab(interval_expr)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"invalid cron expression: {exc}"}), 400

    conn = _get_db()
    try:
        cur = conn.execute("SELECT login_username FROM schedules WHERE id = ?", (schedule_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "schedule not found"}), 404
        login_username = row[0]
        # validate creds still exist
        _get_credentials(login_username)

        if target_username is not None and interval_expr is not None:
            conn.execute(
                "UPDATE schedules SET target_username = ?, interval = ? WHERE id = ?",
                (target_username, interval_expr, schedule_id),
            )
        elif target_username is not None:
            conn.execute(
                "UPDATE schedules SET target_username = ? WHERE id = ?",
                (target_username, schedule_id),
            )
        elif interval_expr is not None:
            conn.execute(
                "UPDATE schedules SET interval = ? WHERE id = ?",
                (interval_expr, schedule_id),
            )
        conn.commit()
    finally:
        conn.close()

    # reschedule
    if scheduler.get_job(str(schedule_id)):
        scheduler.remove_job(str(schedule_id))
    # reload row to get current values
    conn = _get_db()
    try:
        r = conn.execute(
            "SELECT login_username, target_username, interval FROM schedules WHERE id = ?",
            (schedule_id,),
        ).fetchone()
        if r:
            _schedule_job(schedule_id, r[0], r[1], r[2])
    finally:
        conn.close()
    return jsonify({"updated": schedule_id})


def _get_ui_redirect_url():
    """
    Get the UI redirect URL. 
    Uses INSTALAB_UI_BASE_URL if set, otherwise constructs from request host.
    """
    if UI_BASE_URL:
        return UI_BASE_URL
    # Fallback: construct URL from request host (works for local development)
    host = request.host.split(":")[0]
    ui_port = os.getenv("INSTALAB_UI_PORT", "8000")
    return f"http://{host}:{ui_port}/"


@app.route("/control")
def control_page():
    """Redirect /control to Django UI."""
    return redirect(_get_ui_redirect_url(), code=302)


@app.route("/")
def root():
    """Redirect root to Django UI."""
    return redirect(_get_ui_redirect_url(), code=302)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
