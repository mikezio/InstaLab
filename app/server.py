"""
Lightweight web control panel + scheduler for Instaloader.

Features:
- One-off runs via /api/run
- Interval schedules persisted in SQLite and executed via APScheduler
- Rebuild dashboard after runs (optional)
- Simple control UI at /control (static HTML/JS)

Run:
    cd /home/stremio/instaloader_data
    source venv/bin/activate
    python server.py

The server sets HOME to /home/stremio so Instaloader uses the existing session
files in ~/.config/instaloader (to avoid repeated 2FA).
"""

import json
import os
import shutil
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, jsonify, request, send_from_directory, redirect

from instaloader_tracker import update_run_duration, _init_db as _init_instaloader_db
import dashboard as dashboard_builder
from unfollow_bot import AuthRequiredError, ensure_auth_state, unfollow_users, init_login
from db import get_db, get_columns, ddl, is_postgres

# Ensure Instaloader uses the stremio user's home for session files
os.environ.setdefault("HOME", "/home/stremio")
os.environ.setdefault("TZ", "America/New_York")
try:
    time.tzset()
except (AttributeError, OSError) as e:
    # tzset() may not be available on all platforms
    print(f"Warning: Could not set timezone: {e}", file=sys.stderr)
LOCAL_TZ = ZoneInfo("America/New_York")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = BASE_DIR / "instaloader.db"
# Environment variables are already loaded by db module
COOKIE_DIR = Path(os.getenv("INSTALAB_COOKIE_DIR", "/data/instalab/cookies"))

JOB_TMP_DIR = BASE_DIR / "job_runs"


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

CONFIG_DEFAULTS = {
    "run_stall_seconds": int(os.getenv("RUN_STALL_SECONDS", "1200")),
    "run_max_seconds": int(os.getenv("RUN_MAX_SECONDS", "10800")),
    "run_request_timeout": float(os.getenv("RUN_REQUEST_TIMEOUT", "600")),
    "run_item_delay_min": float(os.getenv("RUN_ITEM_DELAY_MIN", "0.25")),
    "run_item_delay_max": float(os.getenv("RUN_ITEM_DELAY_MAX", "0.75")),
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
}
CONFIG_SCHEMA = {
    "run_stall_seconds": {"type": "int", "min": 60, "max": 21600},
    "run_max_seconds": {"type": "int", "min": 600, "max": 43200},
    "run_request_timeout": {"type": "float", "min": 10, "max": 3600},
    "run_item_delay_min": {"type": "float", "min": 0.0, "max": 5.0},
    "run_item_delay_max": {"type": "float", "min": 0.0, "max": 5.0},
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
}
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


def _load_login_profiles(force: bool = False):
    if not force and (time.time() - LOGIN_CACHE.get("ts", 0.0)) < LOGIN_CACHE_TTL:
        return
    profiles = []
    seen = set()
    file_entries = _read_login_file()
    disabled = {
        (entry.get("login_username") or "").strip()
        for entry in file_entries
        if entry.get("disabled")
    }
    for base in BASE_LOGIN_PROFILES:
        username = (base.get("login_username") or "").strip()
        if not username or username in seen or username in disabled or _is_blocked_login(username):
            continue
        profiles.append(dict(base))
        seen.add(username)
    scrubbed_entries = []
    changed = False
    for entry in file_entries:
        username = (entry.get("login_username") or "").strip()
        if not username:
            continue
        if _is_blocked_login(username):
            changed = True
            entry = dict(entry)
            entry["disabled"] = True
            entry["login_password"] = None
            scrubbed_entries.append(entry)
            seen.add(username)
            continue
        if entry.get("disabled"):
            scrubbed_entries.append(entry)
            seen.add(username)
            continue
        if entry.get("login_password"):
            session_file = _session_path_for_login(username)
            if os.path.exists(session_file):
                entry = dict(entry)
                entry["login_password"] = None
                changed = True
        scrubbed_entries.append(entry)
        if username in seen:
            continue
        profiles.append(
            {
                "login_username": username,
                "prefix": None,
                "cookie_file": entry.get("cookie_file") or f"cookies_{username}.txt",
                "db_path": entry.get("db_path") or str(DB_PATH_DEFAULT),
                "login_password": entry.get("login_password"),
                "source": "file",
            }
        )
        seen.add(username)
    if changed:
        try:
            _write_login_file(scrubbed_entries)
        except (IOError, OSError) as e:
            print(f"Warning: Could not write login file: {e}", file=sys.stderr)
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
    if not is_postgres():
        try:
            conn.execute("PRAGMA busy_timeout=30000")
        except Exception as e:
            print(f"Warning: Could not set busy_timeout: {e}", file=sys.stderr)
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
        if is_postgres():
            cur = conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
            return {row[0] for row in cur.fetchall()}
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
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
    payload = {"status": "ok", "details": {}}
    conn = None
    try:
        conn = _get_db()
        conn.execute("SELECT 1")
        tables = _list_tables(conn)
        required = {
            "runs",
            "run_followers",
            "run_followees",
            "schedules",
            "count_checks",
            "unfollow_actions",
            "config",
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
        "job_tmp_dir": _path_state(JOB_TMP_DIR, expect_dir=True, writable=True),
        "data_dir": _path_state(Path("/data/instalab"), expect_dir=True, writable=True),
    }
    status = "ok"
    if not checks["env"]["exists"] or not checks["env"]["readable"]:
        status = "degraded"
    if not checks["job_tmp_dir"]["exists"] or not checks["job_tmp_dir"]["writable"]:
        status = "fail"
    if not checks["cookie_dir"]["exists"] or not checks["cookie_dir"]["writable"]:
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
        session_file = _session_path_for_login(username)
        session_exists = os.path.exists(session_file)
        cookie_file = (p.get("cookie_file") or "").strip()
        cookie_path = COOKIE_DIR / cookie_file if cookie_file else None
        cookie_exists = bool(cookie_path and cookie_path.exists())
        has_password = bool(p.get("login_password"))
        ready = has_password or session_exists or cookie_exists
        if not ready:
            missing_auth.append(username)
        details.append(
            {
                "login_username": username,
                "source": p.get("source", "env"),
                "session_exists": session_exists,
                "cookie_file": cookie_file or None,
                "cookie_exists": cookie_exists,
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
    backend = (os.getenv("INSTALAB_SCRAPER_BACKEND") or os.getenv("SCRAPER_BACKEND") or "selenium").strip().lower()
    
    # Check cache first
    now = time.time()
    if (_BACKEND_HEALTH_CACHE["backend"] == backend and 
        _BACKEND_HEALTH_CACHE["ts"] > 0 and 
        now - _BACKEND_HEALTH_CACHE["ts"] < _BACKEND_HEALTH_CACHE_TTL):
        return _BACKEND_HEALTH_CACHE["status"]
    
    details = {"backend": backend}
    status = "ok"
    if backend in {"instaloader", "insta", "iloader"}:
        try:
            import instaloader  # noqa: F401
            details["instaloader"] = "ok"
        except Exception as exc:  # noqa: BLE001
            status = "fail"
            details["instaloader"] = f"error: {exc}"
    else:
        try:
            import selenium  # noqa: F401
            details["selenium"] = "ok"
        except Exception as exc:  # noqa: BLE001
            status = "fail"
            details["selenium"] = f"error: {exc}"
        chromedriver = shutil.which("chromedriver")
        details["chromedriver"] = chromedriver
        if not chromedriver:
            status = "degraded" if status == "ok" else status
    
    result = {"status": status, "details": details}
    # Update cache
    _BACKEND_HEALTH_CACHE["backend"] = backend
    _BACKEND_HEALTH_CACHE["status"] = result
    _BACKEND_HEALTH_CACHE["ts"] = now
    return result


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

    for jid, meta in RUN_META.items():
        fut = RUN_FUTURES.get(jid)
        if not fut or fut.done():
            continue
        if meta.get("login_username") in {j["login_username"] for j in active}:
            continue
        queued.append({"job_id": jid, "meta": meta})

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
    CONFIG_CACHE["data"] = merged
    CONFIG_CACHE["ts"] = now
    return merged


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

def _init_unfollow_table():
    conn = _get_db()
    try:
        conn.execute(
            ddl(
                """
                CREATE TABLE IF NOT EXISTS unfollow_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    login_username TEXT NOT NULL,
                    target_username TEXT NOT NULL,
                    username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                )
                """,
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
                """,
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


def _init_instaloader_tables():
    conn = _get_db()
    try:
        _init_instaloader_db(conn)
        conn.commit()
    finally:
        conn.close()




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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    login_username TEXT NOT NULL,
                    target_username TEXT NOT NULL,
                    interval_minutes INTEGER,
                    interval TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id SERIAL PRIMARY KEY,
                    login_username TEXT NOT NULL,
                    target_username TEXT NOT NULL,
                    interval_minutes INTEGER,
                    interval TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """,
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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_username TEXT NOT NULL,
                    login_username TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    followers_count INTEGER,
                    followees_count INTEGER,
                    run_requested INTEGER NOT NULL DEFAULT 0,
                    triggered_run_id TEXT,
                    trigger_reason TEXT
                )
                """,
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
                """,
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
        if SCHEMA_HAS_INTERVAL_MINUTES:
            cur = conn.execute(
                """
                INSERT INTO schedules (login_username, target_username, interval_minutes, interval, created_at)
                VALUES (?, ?, 0, ?, ?)
                """,
                (login_username, target_username, cron_expr, datetime.now(LOCAL_TZ).isoformat()),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO schedules (login_username, target_username, interval, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (login_username, target_username, cron_expr, datetime.now(LOCAL_TZ).isoformat()),
            )
        conn.commit()
        return cur.lastrowid
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
            res = guarded_run(login_username, target_username, rebuild_dashboard=True, source=f"schedule:{schedule_id}")
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
    creds = _get_credentials(login_username)
    job_dir = JOB_TMP_DIR / f"count_{uuid4().hex}"
    job_dir.mkdir(parents=True, exist_ok=True)
    result_path = job_dir / "result.json"
    out_path = job_dir / "worker.out"
    err_path = job_dir / "worker.err"

    env = os.environ.copy()
    env["RUN_LOGIN_PASSWORD"] = creds["login_password"]

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
        "--request-timeout",
        "120",
    ]
    with open(out_path, "w", encoding="utf-8") as out_fh, open(err_path, "w", encoding="utf-8") as err_fh:
        proc = subprocess.Popen(cmd, stdout=out_fh, stderr=err_fh, env=env)
    try:
        proc.communicate(timeout=180)
    except Exception:
        _terminate_proc(proc)
        # Clean up temp directory on failure
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass
        return None

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


def _get_credentials(login_username):
    if _is_blocked_login(login_username):
        raise ValueError(f"Login is blocked: {login_username}")
    profile = _get_login_lookup().get(login_username)
    if not profile:
        raise ValueError(f"Unknown login_username: {login_username}")
    password = profile.get("login_password")
    if not password:
        prefix = profile.get("prefix")
        if prefix:
            password = os.getenv(f"{prefix}_LOGIN_PASSWORD")
    cookie_file = _resolve_cookie_file(profile.get("cookie_file"))
    if not password:
        session_file = _session_path_for_login(login_username)
        if not os.path.exists(session_file):
            if not (cookie_file and os.path.exists(cookie_file)):
                raise ValueError(f"Missing password or cookie for login: {login_username}")
        password = ""
    return {
        "login_username": login_username,
        "login_password": password,
        "cookie_file": cookie_file,
        "db_path": profile.get("db_path", str(DB_PATH_DEFAULT)),
    }

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
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass


def run_snapshot(login_username, target_username, rebuild_dashboard=True, job_id=None, two_factor_code=None):
    _ensure_job_tmp_dir()
    creds = _get_credentials(login_username)
    job = ACTIVE_JOBS.get(login_username)
    run_id = job_id or uuid4().hex
    job_dir = JOB_TMP_DIR / f"job_{run_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    progress_path = job_dir / "progress.json"
    result_path = job_dir / "result.json"
    out_path = job_dir / "worker.out"
    err_path = job_dir / "worker.err"

    env = os.environ.copy()
    env["RUN_LOGIN_PASSWORD"] = creds["login_password"]
    if two_factor_code:
        env["RUN_2FA_CODE"] = str(two_factor_code).strip()
    else:
        env.pop("RUN_2FA_CODE", None)

    request_timeout = float(_get_config_value("run_request_timeout", 600))
    item_delay_min = float(_get_config_value("run_item_delay_min", 0.25))
    item_delay_max = float(_get_config_value("run_item_delay_max", 0.75))
    stall_seconds = int(_get_config_value("run_stall_seconds", 1200))
    max_seconds = int(_get_config_value("run_max_seconds", 10800))
    env["RUN_ITEM_DELAY_MIN"] = str(item_delay_min)
    env["RUN_ITEM_DELAY_MAX"] = str(item_delay_max)
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
        "--request-timeout",
        str(request_timeout),
    ]

    with open(out_path, "w", encoding="utf-8") as out_fh, open(err_path, "w", encoding="utf-8") as err_fh:
        proc = subprocess.Popen(cmd, stdout=out_fh, stderr=err_fh, env=env)

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

    result_payload = _read_json_file(result_path)
    if not result_payload:
        tail = _tail_file(err_path)
        # Clean up temp directory on failure
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass
        raise RuntimeError(f"worker exited without result{(': ' + tail) if tail else ''}")
    if result_payload.get("status") != "success":
        # Clean up temp directory on failure
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass
        raise RuntimeError(result_payload.get("error", "worker failed"))

    result = result_payload.get("result") or {}
    
    # Clean up temp directory after successful completion
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception:
        pass
    
    if rebuild_dashboard:
        dashboard_builder.build_dashboard(
            base_dir=str(BASE_DIR),
            output_path=str(BASE_DIR / "dashboard" / "index.html"),
            db_path=str(DB_PATH_DEFAULT),
        )
    return result


def guarded_run(login_username, target_username, rebuild_dashboard=True, source="api", job_id=None, two_factor_code=None):
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
            rebuild_dashboard=rebuild_dashboard,
            job_id=job_id,
            two_factor_code=two_factor_code,
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
RUN_LOCKS = {}
RUN_LOCKS_GUARD = threading.Lock()
ACTIVE_JOBS = {}
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
RUN_WATCHDOG_STOP = threading.Event()


def _get_run_lock(login_username: str) -> threading.Lock:
    key = login_username or "_default"
    with RUN_LOCKS_GUARD:
        lock = RUN_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            RUN_LOCKS[key] = lock
    return lock


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


def _session_path_for_login(login_username: str) -> str:
    session_dir = os.path.expanduser("~/.config/instaloader")
    return os.path.join(session_dir, f"session-{login_username.lower()}")


_init_schedule_table()
_init_config_table()
_init_unfollow_table()
_init_instaloader_tables()
_init_monitor_table()
_restore_schedules()
_schedule_monitor_job()
threading.Thread(target=_watchdog_loop, daemon=True).start()

app = Flask(__name__, static_folder=str(BASE_DIR / "dashboard"), static_url_path="/dashboard")


@app.route("/api/logins", methods=["GET"])
def api_logins():
    def _session_info(username: str):
        session_file = _session_path_for_login(username)
        if os.path.exists(session_file):
            try:
                return True, os.path.getmtime(session_file)
            except Exception:
                return True, None
        return False, None
    profiles = _get_login_profiles()
    return jsonify(
        [
            {
                "login_username": p["login_username"],
                "cookie_file": p.get("cookie_file"),
                "db_path": p.get("db_path"),
                "source": p.get("source", "env"),
                "session_exists": (info := _session_info(p["login_username"]))[0],
                "session_mtime": info[1],
            }
            for p in profiles
        ]
    )


@app.route("/api/logins/add", methods=["POST"])
def api_logins_add():
    data = request.get_json(silent=True) or {}
    login_username = (data.get("login_username") or "").strip()
    login_password = (data.get("login_password") or "").strip()
    cookie_file = (data.get("cookie_file") or "").strip()
    if not login_username or (not login_password and not cookie_file):
        return jsonify({"error": "login_username and (login_password or cookie_file) are required"}), 400
    if _is_blocked_login(login_username):
        return jsonify({"error": "login_username is blocked"}), 400

    lookup = _get_login_lookup(force=True)
    
    # Read login file once at the beginning
    logins = _read_login_file()
    
    if login_username in lookup:
        profile = lookup.get(login_username) or {}
        session_file = _session_path_for_login(login_username)
        if (
            profile.get("source") == "file"
            and not profile.get("login_password")
            and not os.path.exists(session_file)
        ):
            updated = False
            for entry in logins:
                if entry.get("login_username") == login_username:
                    if login_password:
                        entry["login_password"] = login_password
                    if cookie_file:
                        entry["cookie_file"] = cookie_file
                    elif not entry.get("cookie_file"):
                        entry["cookie_file"] = f"cookies_{login_username}.txt"
                    updated = True
            if updated:
                try:
                    _write_login_file(logins)
                    _clear_login_cache()
                except Exception as exc:  # noqa: BLE001
                    return jsonify({"error": f"failed to save login: {exc}"}), 500
                return jsonify({"updated": login_username})
        return jsonify({"error": "login_username already exists"}), 409

    # Check for disabled login or existing entry (using already-loaded logins)
    for entry in logins:
        if entry.get("login_username") == login_username:
            if entry.get("disabled"):
                entry["disabled"] = False
                if login_password:
                    entry["login_password"] = login_password
                if cookie_file:
                    entry["cookie_file"] = cookie_file
                elif not entry.get("cookie_file"):
                    entry["cookie_file"] = f"cookies_{login_username}.txt"
                try:
                    _write_login_file(logins)
                    _clear_login_cache()
                except Exception as exc:  # noqa: BLE001
                    return jsonify({"error": f"failed to save login: {exc}"}), 500
                return jsonify({"updated": login_username})
            session_file = _session_path_for_login(login_username)
            if not entry.get("login_password") and not os.path.exists(session_file):
                if login_password:
                    entry["login_password"] = login_password
                if cookie_file:
                    entry["cookie_file"] = cookie_file
                elif not entry.get("cookie_file"):
                    entry["cookie_file"] = f"cookies_{login_username}.txt"
                try:
                    _write_login_file(logins)
                    _clear_login_cache()
                except Exception as exc:  # noqa: BLE001
                    return jsonify({"error": f"failed to save login: {exc}"}), 500
                return jsonify({"updated": login_username})
            return jsonify({"error": "login_username already exists"}), 409

    logins.append(
        {
            "login_username": login_username,
            "login_password": login_password or None,
            "cookie_file": cookie_file or f"cookies_{login_username}.txt",
            "db_path": str(DB_PATH_DEFAULT),
        }
    )
    try:
        _write_login_file(logins)
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
    session_file = _session_path_for_login(login_username)
    try:
        removed = False
        if os.path.exists(session_file):
            os.remove(session_file)
            removed = True
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
    profile = _get_login_lookup().get(login_username)
    if not profile:
        return jsonify({"error": "unknown login_username"}), 404
    if _get_run_lock(login_username).locked():
        return jsonify({"error": "run already in progress for this login"}), 409
    if UNFOLLOW_LOCK.locked() and (UNFOLLOW_JOB.get("login_username") or "") == login_username:
        return jsonify({"error": "unfollow job running for this login"}), 409
    logins = _read_login_file()
    if profile.get("source") != "file":
        found = False
        for entry in logins:
            if (entry.get("login_username") or "") == login_username:
                entry["disabled"] = True
                entry["login_password"] = None
                found = True
        if not found:
            logins.append(
                {
                    "login_username": login_username,
                    "login_password": None,
                    "disabled": True,
                    "cookie_file": f"cookies_{login_username}.txt",
                    "db_path": str(DB_PATH_DEFAULT),
                }
            )
        try:
            _write_login_file(logins)
            _clear_login_cache()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"failed to delete login: {exc}"}), 500
    else:
        new_logins = [entry for entry in logins if (entry.get("login_username") or "") != login_username]
        if len(new_logins) == len(logins):
            return jsonify({"error": "login_username not found in file"}), 404
        try:
            _write_login_file(new_logins)
            _clear_login_cache()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"failed to delete login: {exc}"}), 500
    removed_session = False
    if delete_session:
        session_file = _session_path_for_login(login_username)
        if os.path.exists(session_file):
            try:
                os.remove(session_file)
                removed_session = True
            except Exception:
                pass
    return jsonify({"deleted": login_username, "session_removed": removed_session})


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
                   non_followbacks_count, login_username, duration_seconds, confidence_score, confidence_flag
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
        # Filter by rn <= 2 in the subquery to reduce data transfer
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
        # Filter by rn <= 30 in the WHERE clause for efficiency
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
                   followers_rate, followees_rate, confidence_score, confidence_flag
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
        return jsonify(run)
    finally:
        conn.close()


def _is_target_busy(target_username: str) -> bool:
    for job in ACTIVE_JOBS.values():
        if job.get("target_username") == target_username:
            return True
    for jid, meta in RUN_META.items():
        fut = RUN_FUTURES.get(jid)
        if fut and not fut.done() and meta.get("target_username") == target_username:
            return True
    return False


def _queue_run(login_username, target_username, *, source="api", two_factor_code=None, rebuild=True):
    job_id = str(uuid4())

    def _runner():
        started = datetime.now(LOCAL_TZ).isoformat()
        meta = RUN_META.setdefault(
            job_id,
            {
                "login_username": login_username,
                "target_username": target_username,
                "submitted_at": started,
                "state": "queued",
                "source": source,
            },
        )
        meta["started_at"] = started
        meta["state"] = "running"
        try:
            res = guarded_run(
                login_username,
                target_username,
                rebuild_dashboard=rebuild,
                source=source,
                job_id=job_id,
                two_factor_code=two_factor_code,
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
            RUN_META[job_id]["state"] = "done"
            return {"status": "success", "started_at": started, "finished_at": finished, "result": res}
        except Exception as exc:  # noqa: BLE001
            finished = datetime.now(LOCAL_TZ).isoformat()
            RUN_META[job_id]["state"] = "error"
            return {"status": "error", "started_at": started, "finished_at": finished, "error": str(exc)}

    RUN_META[job_id] = {
        "login_username": login_username,
        "target_username": target_username,
        "submitted_at": datetime.now(LOCAL_TZ).isoformat(),
        "state": "queued",
        "source": source,
    }
    RUN_FUTURES[job_id] = executor.submit(_runner)
    return job_id


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True)
    login_username = data.get("login_username")
    target_username = data.get("target_username")
    two_factor_code = data.get("two_factor_code") or data.get("twoFactorCode")
    # Always rebuild dashboard after a run so new targets appear
    rebuild = True
    if not login_username or not target_username:
        return jsonify({"error": "login_username and target_username are required"}), 400
    if _is_blocked_login(login_username):
        return jsonify({"error": "login_username is blocked"}), 400

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
        rebuild=rebuild,
    )
    return jsonify({"job_id": job_id})


@app.route("/api/run/<job_id>", methods=["GET"])
def api_run_status(job_id):
    fut = RUN_FUTURES.get(job_id)
    if not fut:
        return jsonify({"error": "unknown job_id"}), 404
    if fut.done():
        try:
            payload = fut.result()
        except Exception as exc:  # noqa: BLE001
            payload = {"status": "error", "error": str(exc)}
        return jsonify({"done": True, "payload": payload, "meta": RUN_META.get(job_id)})
    return jsonify({"done": False, "meta": RUN_META.get(job_id)})


@app.route("/api/jobs/<job_id>/detail", methods=["GET"])
def api_job_detail(job_id):
    job_dir = JOB_TMP_DIR / f"job_{job_id}"
    if not job_dir.exists():
        return jsonify({"error": "job not found"}), 404
    progress_path = job_dir / "progress.json"
    result_path = job_dir / "result.json"
    out_path = job_dir / "worker.out"
    err_path = job_dir / "worker.err"
    return jsonify(
        {
            "job_id": job_id,
            "progress": _read_json_file(progress_path),
            "result": _read_json_file(result_path),
            "worker_out_tail": _tail_file(out_path, lines=30),
            "worker_err_tail": _tail_file(err_path, lines=30),
        }
    )


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

    queued = []
    inflight = []
    for jid, meta in RUN_META.items():
        fut = RUN_FUTURES.get(jid)
        if not fut or fut.done():
            continue
        if meta.get("login_username") in active_logins:
            continue
        # Determine running vs queued based on meta state/started_at
        if meta.get("state") == "running" or meta.get("started_at"):
            inflight.append({"job_id": jid, "meta": meta})
        else:
            queued.append({"job_id": jid, "meta": meta})

    # If we have inflight runs without ACTIVE_JOBS, surface them as running.
    for item in inflight:
        meta = item["meta"] or {}
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
                "job_id": item["job_id"],
                "elapsed_seconds": elapsed,
                "avg_duration_seconds": avg,
                "eta_seconds": eta,
            }
        )
        active_logins.add(meta.get("login_username"))

    if active_jobs:
        state = "running"
    elif queued:
        state = "queued"
    else:
        state = "idle"

    payload = {
        "state": state,
        "active_jobs": active_jobs,
        "active_logins": list(active_logins),
        "queued_jobs": queued,
        "queued_logins": [q["meta"].get("login_username") for q in queued],
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
    return jsonify({"config": _get_config(force=True), "defaults": CONFIG_DEFAULTS})


@app.route("/api/config", methods=["PUT"])
def api_config_update():
    data = request.get_json(force=True) or {}
    if not isinstance(data, dict) or not data:
        return jsonify({"error": "config payload required"}), 400
    try:
        updated = _set_config_values(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        _schedule_monitor_job()
    except Exception:
        pass
    return jsonify({"updated": list(updated.keys()), "config": _get_config(force=True)})


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
    if UNFOLLOW_LOCK.locked():
        return jsonify({"error": "unfollow job running"}), 409
    # Launch interactive login in background (requires display on server)
    executor.submit(init_login, UNFOLLOW_STORAGE)
    return jsonify({"started": True, "note": "interactive login opened"})


@app.route("/api/run/cancel", methods=["POST"])
def api_run_cancel():
    data = request.get_json(force=True) or {}
    login_username = data.get("login_username")
    target_username = data.get("target_username")
    job_id = data.get("job_id")
    if (not login_username or not target_username) and job_id:
        meta = RUN_META.get(job_id) or {}
        login_username = login_username or meta.get("login_username")
        target_username = target_username or meta.get("target_username")
        if not login_username or not target_username:
            for login, job in list(ACTIVE_JOBS.items()):
                if job.get("job_id") == job_id:
                    login_username = login_username or job.get("login_username") or login
                    target_username = target_username or job.get("target_username")
                    break
    if not login_username or not target_username:
        return jsonify({"error": "login_username and target_username are required"}), 400
    # mark cancel
    CANCEL_REQUESTS.add((login_username, target_username))
    # best-effort: if current active matches, mark cancelled flag
    job = ACTIVE_JOBS.get(login_username)
    if job and job.get("target_username") == target_username:
        job["cancelled"] = True
    return jsonify({"cancelled": True})


@app.route("/api/run/<int:run_id>", methods=["DELETE"])
def api_run_delete(run_id):
    target = _delete_run(run_id)
    if not target:
        return jsonify({"error": "run not found"}), 404
    # Rebuild dashboard so UI reflects deletion
    dashboard_builder.build_dashboard(
        base_dir=str(BASE_DIR),
        output_path=str(BASE_DIR / "dashboard" / "index.html"),
        db_path=str(DB_PATH_DEFAULT),
    )
    return jsonify({"deleted": run_id, "target": target, "undo": True})


@app.route("/api/run/undo/<int:run_id>", methods=["POST"])
def api_run_undo(run_id):
    ok, msg = _restore_run(run_id)
    if not ok:
        return jsonify({"error": msg}), 400
    dashboard_builder.build_dashboard(
        base_dir=str(BASE_DIR),
        output_path=str(BASE_DIR / "dashboard" / "index.html"),
        db_path=str(DB_PATH_DEFAULT),
    )
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


@app.route("/api/rebuild", methods=["POST"])
def api_rebuild():
    dashboard_builder.build_dashboard(
        base_dir=str(BASE_DIR),
        output_path=str(BASE_DIR / "dashboard" / "index.html"),
        db_path=str(DB_PATH_DEFAULT),
    )
    return jsonify({"status": "rebuilt"})


@app.route("/control")
def control_page():
    host = request.host.split(":")[0]
    return redirect(f"http://{host}:8000/", code=302)


@app.route("/")
def root():
    host = request.host.split(":")[0]
    return redirect(f"http://{host}:8000/", code=302)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
