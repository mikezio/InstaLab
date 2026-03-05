"""Private Instagram API tracker using instagrapi.

Reference implementation patterned after Osintgram-style workflows but using
the actively maintained instagrapi client.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from uuid import uuid4
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests
from instagrapi import Client
from instagrapi.exceptions import (
    AgeEligibilityError,
    BadPassword,
    ChallengeRequired,
    ChallengeError,
    FeedbackRequired,
    ClientThrottledError,
    EmailInvalidError,
    EmailNotAvailableError,
    EmailVerificationSendError,
    LoginRequired,
    LegacyForceSetNewPasswordForm,
    PleaseWaitFewMinutes,
    RateLimitError,
    TwoFactorRequired,
)
from instagrapi import utils as instagrapi_utils
from instagrapi.mixins.totp import TOTP
from requests.exceptions import ProxyError as RequestsProxyError
from requests.exceptions import SSLError as RequestsSSLError

from tracker_db import write_run_metadata, write_run_profile_counts
from login_store import (
    clear_session_settings,
    consume_challenge_code,
    consume_new_password,
    get_login,
    record_session_validation_failure,
    reset_session_validation_failures,
    set_last_error,
    set_last_login,
    set_login_password,
    set_session_settings,
    set_totp_seed,
)
from proxy_utils import load_proxy_from_env


SETTINGS_DIR = Path(os.getenv("INSTALAB_PRIVATE_SETTINGS_DIR", "/data/instalab/private"))
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]+$")
DEVICE_SETTINGS_ENV = "RUN_DEVICE_SETTINGS_JSON"
DEVICE_SETTINGS_GLOBAL_ENV = "INSTALAB_PRIVATE_DEVICE_SETTINGS_JSON"
USER_AGENT_ENV = "RUN_USER_AGENT"
USER_AGENT_GLOBAL_ENV = "INSTALAB_PRIVATE_USER_AGENT"
TWO_FACTOR_POLL_SECONDS = int(os.getenv("RUN_2FA_POLL_SECONDS", "180") or 180)
TWO_FACTOR_POLL_INTERVAL = float(os.getenv("RUN_2FA_POLL_INTERVAL", "5") or 5)
REQUEST_SLEEP_MAX = float(os.getenv("INSTALAB_PRIVATE_REQUEST_SLEEP_MAX", "2.0") or 2.0)
REQUEST_SLEEP_FALLBACK = float(os.getenv("INSTALAB_PRIVATE_REQUEST_SLEEP", "1.0") or 1.0)
PRE_LOGIN_FLOW_ENABLED = str(os.getenv("RUN_PRE_LOGIN_FLOW", "false")).strip().lower() in {"1", "true", "yes", "on"}
POST_LOGIN_FLOW_ENABLED = str(os.getenv("RUN_POST_LOGIN_FLOW", "false")).strip().lower() in {"1", "true", "yes", "on"}
ANONYMOUS_LOGIN_MODES = {"anonymous", "public", "no_login", "no-login", "anon"}

DEFAULT_DEVICE_SETTINGS = {
    "app_version": "414.0.0.40.83",
    "android_version": 33,
    "android_release": "13",
    "dpi": "420dpi",
    "resolution": "1080x2400",
    "manufacturer": "Google",
    "device": "panther",
    "model": "Pixel 7",
    "cpu": "arm64-v8a",
    "version_code": "382006496",
}
DEVICE_PROFILE_POOL = [
    {
        "manufacturer": "Google",
        "device": "panther",
        "model": "Pixel 7",
        "resolution": "1080x2400",
        "dpi": "420dpi",
        "android_version": 33,
        "android_release": "13",
        "cpu": "arm64-v8a",
    },
    {
        "manufacturer": "Google",
        "device": "husky",
        "model": "Pixel 8 Pro",
        "resolution": "1344x2992",
        "dpi": "480dpi",
        "android_version": 34,
        "android_release": "14",
        "cpu": "arm64-v8a",
    },
    {
        "manufacturer": "samsung",
        "device": "dm3q",
        "model": "SM-S918B",
        "resolution": "1440x3088",
        "dpi": "560dpi",
        "android_version": 34,
        "android_release": "14",
        "cpu": "exynos2200",
    },
    {
        "manufacturer": "samsung",
        "device": "a54x",
        "model": "SM-A546E",
        "resolution": "1080x2340",
        "dpi": "420dpi",
        "android_version": 33,
        "android_release": "13",
        "cpu": "exynos1380",
    },
    {
        "manufacturer": "Xiaomi",
        "device": "marble",
        "model": "23049PCD8G",
        "resolution": "1220x2712",
        "dpi": "480dpi",
        "android_version": 33,
        "android_release": "13",
        "cpu": "arm64-v8a",
    },
]
TOTP_INTERVAL_SECONDS = 30
SESSION_STALE_THRESHOLD = int(os.getenv("INSTALAB_SESSION_STALE_THRESHOLD", "3") or 3)
AUTH_TRACE_LIMIT = int(os.getenv("INSTALAB_AUTH_TRACE_LIMIT", "400") or 400)


class PrivateAPIError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _normalize_login_mode(login_mode: str | None) -> str:
    mode = (login_mode or "auto").strip().lower()
    if mode in {"session", "session-only"}:
        return "session_only"
    if mode == "password-only":
        return "password"
    if mode in ANONYMOUS_LOGIN_MODES:
        return "anonymous"
    if mode in {"auto", "session_only", "password"}:
        return mode
    return "auto"


def _is_anonymous_login_mode(login_mode: str | None) -> bool:
    return _normalize_login_mode(login_mode) == "anonymous"


def _trace_span(name, **tags):
    try:
        from ddtrace import tracer
    except Exception:  # pragma: no cover
        tracer = None
    if tracer is None:
        class _Noop:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Noop()
    span = tracer.trace(name)
    for key, value in tags.items():
        if value is not None:
            span.set_tag(key, value)
    return span


SENSITIVE_TRACE_KEYS = {
    "enc_password",
    "password",
    "phone_number",
    "verification_code",
    "two_factor_identifier",
    "sms_code",
    "seed",
    "totp_seed",
    "email",
    "email_code",
}
PAGINATED_FRIENDSHIPS_ENDPOINT_RE = re.compile(
    r"^friendships/(?P<target_id>\d+)/(?P<kind>followers|following)/?$"
)
AUTH_TRACE = deque(maxlen=max(50, AUTH_TRACE_LIMIT))
AUTH_TRACE_LOCK = threading.Lock()
AUTH_STATE: dict[str, dict] = {}


def _auth_trace(login_username: str, event: str, **details) -> None:
    username = str(login_username or "").strip()
    if not username:
        return
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "login_username": username,
        "event": str(event or "").strip() or "event",
    }
    for key, value in details.items():
        if value is None:
            continue
        payload[str(key)] = value
    with AUTH_TRACE_LOCK:
        AUTH_TRACE.appendleft(payload)
        state = dict(AUTH_STATE.get(username) or {})
        state["last_event_at"] = payload["at"]
        state["last_event"] = payload["event"]
        for key in (
            "two_factor_method",
            "session_fail_streak",
            "session_marked_stale",
            "session_validation_ok",
            "totp_source",
            "error_code",
            "error",
        ):
            if key in payload:
                state[key] = payload.get(key)
        AUTH_STATE[username] = state


def get_auth_trace(login_username: str, limit: int = 30) -> list[dict]:
    username = str(login_username or "").strip()
    limit = max(1, min(int(limit or 30), 200))
    with AUTH_TRACE_LOCK:
        rows = [row for row in AUTH_TRACE if row.get("login_username") == username]
    return rows[:limit]


def get_auth_state(login_username: str) -> dict:
    username = str(login_username or "").strip()
    with AUTH_TRACE_LOCK:
        return dict(AUTH_STATE.get(username) or {})


def _derive_two_factor_method(two_factor_info) -> str:
    info = two_factor_info if isinstance(two_factor_info, dict) else {}
    if info.get("totp_two_factor_on"):
        return "totp"
    if info.get("sms_two_factor_on"):
        return "sms"
    if info.get("email_two_factor_on"):
        return "email"
    return "unknown"


def get_instagram_clock_skew(proxy_url: str | None = None, timeout_seconds: float = 12.0) -> dict:
    url = "https://i.instagram.com/"
    opener = None
    if proxy_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        if opener:
            response = opener.open(req, timeout=max(1.0, float(timeout_seconds)))
        else:
            response = urllib.request.urlopen(req, timeout=max(1.0, float(timeout_seconds)))
        with response:
            date_header = response.headers.get("Date")
        if not date_header:
            return {"ok": False, "error": "instagram response missing Date header"}
        server_dt = parsedate_to_datetime(date_header).astimezone(timezone.utc)
        local_dt = datetime.now(timezone.utc)
        skew_seconds = int((local_dt - server_dt).total_seconds())
        return {
            "ok": True,
            "source": "instagram_date_header",
            "date_header": date_header,
            "skew_seconds": skew_seconds,
            "abs_skew_seconds": abs(skew_seconds),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _truncate(value, limit=2000):
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…"


def _redact_payload(payload):
    if payload is None:
        return None
    if isinstance(payload, dict):
        redacted = {}
        for key, value in payload.items():
            if key in SENSITIVE_TRACE_KEYS:
                redacted[key] = "***"
            else:
                redacted[key] = _redact_payload(value)
        return redacted
    if isinstance(payload, (list, tuple)):
        return [_redact_payload(value) for value in payload]
    return payload


def _summarize_last_json(last_json):
    if not isinstance(last_json, dict):
        return last_json
    summary = {k: last_json.get(k) for k in ("status", "message", "error_type", "checkpoint_url")}
    if "two_factor_info" in last_json:
        summary["two_factor_info"] = last_json.get("two_factor_info")
    summary["keys"] = sorted(list(last_json.keys()))[:40]
    return summary


def _append_trace(trace_path: str, payload: dict) -> None:
    if not trace_path:
        return
    try:
        with open(trace_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _extract_progress_user_ids(last_json) -> set[str]:
    if not isinstance(last_json, dict):
        return set()
    users = last_json.get("users")
    if not isinstance(users, list):
        return set()
    user_ids: set[str] = set()
    for user in users:
        if not isinstance(user, dict):
            continue
        value = user.get("pk")
        if value is None:
            value = user.get("id")
        if value is None:
            continue
        user_id = str(value).strip()
        if user_id:
            user_ids.add(user_id)
    return user_ids


def _emit_paginated_progress(client: Client, endpoint) -> None:
    state = getattr(client, "_instalab_page_progress", None)
    if not isinstance(state, dict):
        return
    callback = state.get("callback")
    if not callable(callback):
        return

    endpoint_value = str(endpoint or "").strip().lstrip("/")
    endpoint_value = endpoint_value.split("?", 1)[0]
    match = PAGINATED_FRIENDSHIPS_ENDPOINT_RE.match(endpoint_value)
    if not match:
        return

    target_id = str(state.get("target_id") or "").strip()
    if target_id and match.group("target_id") != target_id:
        return

    kind = match.group("kind")
    seen_key = f"{kind}_seen"
    seen = state.get(seen_key)
    if not isinstance(seen, set):
        seen = set()
        state[seen_key] = seen

    user_ids = _extract_progress_user_ids(getattr(client, "last_json", None))
    if not user_ids:
        return

    before = len(seen)
    seen.update(user_ids)
    if len(seen) <= before:
        return
    try:
        callback(kind, len(seen))
    except Exception:
        pass


def _enable_progress_private_request(client: Client) -> None:
    if getattr(client, "_instalab_progress_hook_enabled", False):
        return

    original_private_request = client.private_request

    def _wrapped_private_request(
        endpoint,
        data=None,
        params=None,
        login=False,
        with_signature=True,
        headers=None,
        extra_sig=None,
        domain: str | None = None,
    ):
        try:
            return original_private_request(
                endpoint,
                data=data,
                params=params,
                login=login,
                with_signature=with_signature,
                headers=headers,
                extra_sig=extra_sig,
                domain=domain,
            )
        finally:
            _emit_paginated_progress(client, endpoint)

    client.private_request = _wrapped_private_request
    client._instalab_progress_hook_enabled = True


def _enable_trace(client: Client, login_username: str, trace_path: str | None) -> None:
    if not trace_path:
        return
    original_private_request = client.private_request

    def _wrapped_private_request(
        endpoint,
        data=None,
        params=None,
        login=False,
        with_signature=True,
        headers=None,
        extra_sig=None,
        domain: str | None = None,
    ):
        started = time.time()
        error = None
        try:
            return original_private_request(
                endpoint,
                data=data,
                params=params,
                login=login,
                with_signature=with_signature,
                headers=headers,
                extra_sig=extra_sig,
                domain=domain,
            )
        except Exception as exc:
            error = repr(exc)
            raise
        finally:
            response = getattr(client, "last_response", None)
            status_code = getattr(response, "status_code", None)
            response_text = None
            if response is not None:
                try:
                    response_text = response.text
                except Exception:
                    response_text = None
            payload = {
                "ts": time.time(),
                "elapsed_ms": int((time.time() - started) * 1000),
                "login_username": login_username,
                "endpoint": endpoint,
                "login": bool(login),
                "status_code": status_code,
                "request": {
                    "data": _redact_payload(data),
                    "params": _redact_payload(params),
                },
                "response": {
                    "last_json": _summarize_last_json(getattr(client, "last_json", None)),
                    "text": _truncate(response_text, 2000),
                },
                "error": error,
            }
            _append_trace(trace_path, payload)

    client.private_request = _wrapped_private_request


def _safe_name(value: str) -> str:
    value = (value or "").strip()
    if USERNAME_RE.match(value):
        return value
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value) or "login"


def _settings_path(login_username: str) -> Path:
    return SETTINGS_DIR / f"{_safe_name(login_username)}.json"


def settings_path(login_username: str) -> Path:
    return _settings_path(login_username)


def has_session(login_username: str) -> bool:
    entry = get_login(login_username, include_secrets=False)
    if entry and entry.get("session_settings"):
        return True
    return _settings_path(login_username).exists()


def session_mtime(login_username: str) -> float | None:
    path = _settings_path(login_username)
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def _load_settings(login_username: str) -> dict | None:
    entry = get_login(login_username, include_secrets=False)
    if entry and entry.get("session_settings"):
        return entry.get("session_settings")
    path = _settings_path(login_username)
    if not path.exists():
        return None
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        try:
            set_session_settings(login_username, settings)
        except Exception:
            pass
        return settings
    except Exception:
        try:
            path.unlink()
        except Exception:
            pass
        return None


def _clear_cached_session(login_username: str) -> None:
    clear_session_settings(login_username)
    path = _settings_path(login_username)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _save_settings(login_username: str, settings: dict) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    path = _settings_path(login_username)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    try:
        set_session_settings(login_username, settings)
    except Exception:
        pass


def _normalize_two_factor_code(code: str | None) -> str:
    if not code:
        return ""
    return re.sub(r"[^0-9]", "", str(code))


def _normalize_totp_seed(seed: str | None) -> str:
    if not seed:
        return ""
    value = str(seed).strip()
    if not value:
        return ""
    if value.lower().startswith("otpauth://"):
        try:
            parsed = urlparse(value)
            secret = parse_qs(parsed.query).get("secret", [""])[0]
            value = str(secret or "").strip()
        except Exception:
            value = ""
    value = re.sub(r"[\s-]+", "", value).upper()
    value = re.sub(r"[^A-Z2-7=]", "", value)
    return value.rstrip("=")


def _totp_code_for_timestamp(seed: str, when: float | None = None) -> str:
    normalized_seed = _normalize_totp_seed(seed)
    if not normalized_seed:
        raise ValueError("invalid TOTP seed")
    totp = TOTP(normalized_seed)
    if when is None:
        return totp.code()
    timecode = int(float(when) / TOTP_INTERVAL_SECONDS)
    return totp.generate_otp(timecode)


def _totp_candidate_codes(seed: str, when: float | None = None) -> list[str]:
    base = float(time.time() if when is None else when)
    candidates: list[str] = []
    for offset in (0, -TOTP_INTERVAL_SECONDS, TOTP_INTERVAL_SECONDS):
        try:
            code = _totp_code_for_timestamp(seed, base + offset)
        except Exception:
            continue
        if code and code not in candidates:
            candidates.append(code)
    return candidates


def _challenge_code_handler_factory(login_username: str, code: str | None):
    normalized = _normalize_two_factor_code(code)

    def _handler(username, choice):
        if normalized:
            print("Challenge code supplied (env)", flush=True)
            return normalized
        try:
            db_code = consume_challenge_code(login_username)
        except Exception:
            db_code = None
        if db_code:
            print("Challenge code supplied (store)", flush=True)
            return _normalize_two_factor_code(db_code)
        return False

    return _handler


def _poll_two_factor_code(login_username: str) -> str:
    if TWO_FACTOR_POLL_SECONDS <= 0:
        return ""
    deadline = time.time() + TWO_FACTOR_POLL_SECONDS
    while time.time() < deadline:
        try:
            db_code = consume_challenge_code(login_username)
        except Exception:
            db_code = None
        normalized = _normalize_two_factor_code(db_code)
        if normalized:
            return normalized
        time.sleep(TWO_FACTOR_POLL_INTERVAL)
    return ""


def _raise_login_error(exc: Exception, last_json: dict | None = None) -> None:
    payload = last_json if isinstance(last_json, dict) else {}
    err_type = str(payload.get("error_type") or "").strip().lower()
    msg = str(payload.get("message") or "").strip().lower()
    if err_type == "invalid_user" or "can't find an account" in msg:
        raise PrivateAPIError(
            "invalid_username",
            "instagram could not find this login account (invalid username)",
        )
    if isinstance(exc, ChallengeRequired):
        checkpoint_url = getattr(exc, "checkpoint_url", None) or getattr(exc, "url", None)
        if checkpoint_url and "unsupported_version" in checkpoint_url:
            raise PrivateAPIError(
                "unsupported_version",
                "instagram reports unsupported app version; update device profile",
            )
        url = getattr(exc, "url", None)
        if url:
            raise PrivateAPIError("challenge_required", f"private API challenge required: {url}")
        raise PrivateAPIError("challenge_required", "private API challenge required")
    if isinstance(exc, LegacyForceSetNewPasswordForm):
        raise PrivateAPIError("password_reset_required", str(exc))
    if isinstance(exc, ChallengeError):
        raise PrivateAPIError("challenge_required", f"private API challenge required: {exc}")
    if isinstance(exc, TwoFactorRequired):
        raise PrivateAPIError("two_factor_required", "two_factor_required; provide RUN_2FA_CODE")
    if isinstance(exc, BadPassword):
        lowered = str(exc).strip().lower()
        if "change your ip address" in lowered or "blacklist" in lowered:
            raise PrivateAPIError(
                "proxy_blocked_or_bad_password",
                "instagram rejected login: password may be wrong or proxy IP reputation is blocked",
            )
        raise PrivateAPIError("bad_password", "invalid password")
    if isinstance(exc, LoginRequired):
        raise PrivateAPIError("login_required", "private API login required")
    if isinstance(exc, PleaseWaitFewMinutes):
        raise PrivateAPIError("rate_limited", "instagram rate limited; wait and retry")
    if isinstance(exc, FeedbackRequired):
        raise PrivateAPIError("feedback_required", "instagram feedback required (rate limit / risk)")
    if isinstance(exc, RateLimitError):
        raise PrivateAPIError("rate_limited", "instagram rate limit exceeded")
    raise PrivateAPIError("private_api_error", f"private API error: {exc}")


def _wrap_requests_timeout(session, timeout_seconds: float | None) -> None:
    """Inject a default requests timeout for instagrapi HTTP calls.

    instagrapi's `Client.request_timeout` is used as a *sleep* in the private API
    request loop, so we must not use it as a network timeout.
    """

    if session is None or timeout_seconds is None:
        return
    try:
        timeout_seconds = float(timeout_seconds)
    except Exception:
        return
    if timeout_seconds <= 0:
        return
    if getattr(session, "_instalab_timeout_wrapped", False):
        return

    try:
        original_request = session.request
    except Exception:
        return

    def _wrapped_request(method, url, *args, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = timeout_seconds
        return original_request(method, url, *args, **kwargs)

    try:
        session.request = _wrapped_request
        session._instalab_timeout_wrapped = True
    except Exception:
        return


def _default_device_settings_for_login(login_username: str | None = None) -> dict:
    username = str(login_username or "").strip().lower()
    if not username:
        return dict(DEFAULT_DEVICE_SETTINGS)
    idx = int(hashlib.sha256(username.encode("utf-8")).hexdigest()[:8], 16) % len(DEVICE_PROFILE_POOL)
    merged = dict(DEFAULT_DEVICE_SETTINGS)
    merged.update(DEVICE_PROFILE_POOL[idx])
    return merged


def _load_device_settings(device_settings_json: str | None, *, login_username: str | None = None) -> dict:
    raw = (device_settings_json or os.getenv(DEVICE_SETTINGS_ENV) or os.getenv(DEVICE_SETTINGS_GLOBAL_ENV) or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return _default_device_settings_for_login(login_username)


def _load_user_agent(user_agent: str | None) -> str | None:
    value = (user_agent or os.getenv(USER_AGENT_ENV) or os.getenv(USER_AGENT_GLOBAL_ENV) or "").strip()
    return value or None


def _needs_device_upgrade(current: dict | None, desired: dict | None) -> bool:
    if not desired:
        return False
    if not current:
        return True
    for key in (
        "app_version",
        "version_code",
        "android_version",
        "android_release",
        "manufacturer",
        "device",
        "model",
        "resolution",
        "dpi",
        "cpu",
    ):
        if str(current.get(key) or "") != str(desired.get(key) or ""):
            return True
    return False


def _is_unsupported_version(last_json: dict | None, exc: Exception | None = None) -> bool:
    checkpoint_url = None
    if exc is not None:
        checkpoint_url = getattr(exc, "checkpoint_url", None) or getattr(exc, "url", None)
    if not checkpoint_url and last_json:
        checkpoint_url = last_json.get("checkpoint_url")
    if checkpoint_url and "unsupported_version" in str(checkpoint_url):
        return True
    return False


def _apply_device_settings(
    client: Client,
    login_username: str,
    desired: dict | None,
    user_agent: str | None,
    *,
    force: bool = False,
) -> bool:
    current_settings = client.get_settings() or {}
    current = current_settings.get("device_settings") or client.device_settings or {}
    changed = False
    needs_device_upgrade = bool(desired) and (force or _needs_device_upgrade(current, desired))
    if needs_device_upgrade:
        merged = dict(current or {})
        merged.update({k: v for k, v in (desired or {}).items() if v is not None and v != ""})
        client.set_device(merged)
        changed = True
    normalized_user_agent = (user_agent or "").strip()
    if normalized_user_agent:
        current_user_agent = str(
            current_settings.get("user_agent")
            or getattr(client, "user_agent", "")
            or ""
        ).strip()
        if force or current_user_agent != normalized_user_agent:
            client.set_user_agent(normalized_user_agent)
            changed = True
    if not changed:
        return False
    try:
        _save_settings(login_username, client.get_settings())
    except Exception:
        pass
    return True


def _build_public_client(
    proxy_url: str | None,
    *,
    http_timeout_seconds: float | None = None,
    request_sleep_seconds: float | None = None,
    request_timeout: float = 120.0,
    delay_min: float | None = None,
    delay_max: float | None = None,
):
    cl = Client()
    if http_timeout_seconds is None:
        http_timeout_seconds = request_timeout
    _wrap_requests_timeout(getattr(cl, "public", None), http_timeout_seconds)
    if request_sleep_seconds is None:
        request_sleep_seconds = REQUEST_SLEEP_FALLBACK
    try:
        sleep_seconds = float(request_sleep_seconds or 0.0)
    except Exception:
        sleep_seconds = 0.0
    if sleep_seconds < 0:
        sleep_seconds = 0.0
    cl.request_timeout = min(sleep_seconds, REQUEST_SLEEP_MAX)
    if delay_min is not None or delay_max is not None:
        min_delay = float(delay_min or 0.0)
        max_delay = float(delay_max or 0.0)
        if max_delay and min_delay > max_delay:
            min_delay, max_delay = max_delay, min_delay
        if min_delay > 0 or max_delay > 0:
            cl.delay_range = [min_delay, max_delay or min_delay]
    if proxy_url:
        cl.set_proxy(proxy_url)
    return cl


def _build_client(
    login_username: str,
    login_password: str,
    proxy_url: str | None,
    *,
    http_timeout_seconds: float | None = None,
    request_sleep_seconds: float | None = None,
    # Back-compat: older callsites used `request_timeout` but instagrapi uses it as a sleep.
    request_timeout: float = 120.0,
    two_factor_code: str | None = None,
    challenge_code: str | None = None,
    totp_seed: str | None = None,
    login_mode: str | None = None,
    delay_min: float | None = None,
    delay_max: float | None = None,
    device_settings_json: str | None = None,
    user_agent: str | None = None,
    trace_enabled: bool | None = None,
    trace_path: str | None = None,
):
    settings = _load_settings(login_username)
    entry = get_login(login_username, include_secrets=True) or {}
    if not login_password:
        login_password = entry.get("login_password") or ""
    if not totp_seed:
        totp_seed = entry.get("totp_seed")
    normalized_totp_seed = _normalize_totp_seed(totp_seed)
    if not login_password and not settings:
        raise RuntimeError("missing RUN_LOGIN_PASSWORD (no private session cached)")
    _auth_trace(
        login_username,
        "build_client_start",
        login_mode=_normalize_login_mode(login_mode),
        has_settings=bool(settings),
    )

    if settings:
        cl = Client(settings=settings or {})
    else:
        # Seed stable device + uuid settings before first login attempt.
        cl = Client()
        try:
            settings = cl.get_settings()
            _save_settings(login_username, settings)
        except Exception:
            settings = None
        if settings:
            try:
                cl.set_settings(settings)
            except Exception:
                pass
    cl.username = login_username
    cl.password = login_password
    device_settings_raw = (
        device_settings_json
        or os.getenv(DEVICE_SETTINGS_ENV)
        or os.getenv(DEVICE_SETTINGS_GLOBAL_ENV)
        or ""
    ).strip()
    has_device_override = bool(device_settings_raw)
    desired_device_settings = _load_device_settings(device_settings_json, login_username=login_username)
    user_agent_override = _load_user_agent(user_agent)
    should_apply_device_settings = (not settings) or has_device_override or bool(user_agent_override)
    updated_device = False
    if should_apply_device_settings:
        updated_device = _apply_device_settings(
            cl,
            login_username,
            desired_device_settings,
            user_agent_override,
            force=not settings,
        )
    if updated_device:
        try:
            settings = cl.get_settings()
        except Exception:
            pass

    def _handle_exception(client, exc):
        if isinstance(exc, BadPassword):
            # Preserve this signal so we do not trigger extra automatic reauth attempts.
            client._instalab_bad_password = True
            raise exc
        if isinstance(exc, ChallengeRequired):
            if _is_unsupported_version(client.last_json, exc):
                if not getattr(client, "_instalab_device_upgrade", False):
                    _apply_device_settings(
                        client,
                        login_username,
                        desired_device_settings,
                        user_agent_override,
                        force=True,
                    )
                    client._instalab_device_upgrade = True
                    _login_with_password()
                    return
            # Do not auto-resolve unknown challenge flows here.
            # Repeated resolver attempts can amplify risk signals.
            raise exc
        if isinstance(exc, LoginRequired):
            if getattr(client, "_instalab_bad_password", False):
                raise exc
            if loginrequired_reauth_count["value"] >= 1:
                raise exc
            loginrequired_reauth_count["value"] += 1
            _login_with_password()
            try:
                _save_settings(login_username, client.get_settings())
            except Exception:
                pass
            return
        if isinstance(exc, (PleaseWaitFewMinutes, FeedbackRequired, RateLimitError, ClientThrottledError)):
            raise exc
        raise exc

    cl.handle_exception = _handle_exception
    if http_timeout_seconds is None:
        http_timeout_seconds = request_timeout
    _wrap_requests_timeout(getattr(cl, "private", None), http_timeout_seconds)
    try:
        _wrap_requests_timeout(getattr(cl, "public", None), http_timeout_seconds)
    except Exception:
        # Best-effort: if wrapping timeouts for the public client fails, continue without modifying it.
        pass

    if request_sleep_seconds is None:
        request_sleep_seconds = REQUEST_SLEEP_FALLBACK
    try:
        sleep_seconds = float(request_sleep_seconds or 0.0)
    except Exception:
        sleep_seconds = 0.0
    if sleep_seconds < 0:
        sleep_seconds = 0.0
    cl.request_timeout = min(sleep_seconds, REQUEST_SLEEP_MAX)
    if delay_min is not None or delay_max is not None:
        min_delay = float(delay_min or 0.0)
        max_delay = float(delay_max or 0.0)
        if max_delay and min_delay > max_delay:
            min_delay, max_delay = max_delay, min_delay
        if min_delay > 0 or max_delay > 0:
            cl.delay_range = [min_delay, max_delay or min_delay]
    if proxy_url:
        cl.set_proxy(proxy_url)
    cl.challenge_code_handler = _challenge_code_handler_factory(login_username, challenge_code or two_factor_code)
    if trace_enabled and trace_path:
        _enable_trace(cl, login_username, trace_path)
    new_password_used = {"value": None}
    loginrequired_reauth_count = {"value": 0}

    def _change_password_handler(username):
        try:
            pwd = consume_new_password(login_username)
        except Exception:
            pwd = None
        if pwd:
            new_password_used["value"] = pwd
        return pwd or False

    cl.change_password_handler = _change_password_handler
    verification_code = _normalize_two_factor_code(two_factor_code)

    def _login_with_trust():
        enc_password = cl.password_encrypt(login_password or "")
        data = {
            "jazoest": instagrapi_utils.generate_jazoest(cl.phone_id),
            "country_codes": '[{"country_code":"%d","source":["default"]}]' % int(cl.country_code),
            "phone_id": cl.phone_id,
            "enc_password": enc_password,
            "username": login_username,
            "adid": cl.advertising_id,
            "guid": cl.uuid,
            "device_id": cl.android_device_id,
            "google_tokens": "[]",
            "login_attempt_count": "0",
        }
        cl.private_request("accounts/login/", data, login=True)
        cl.authorization_data = cl.parse_authorization(cl.last_response.headers.get("ig-set-authorization"))

    def _two_factor_login_with_trust(code: str):
        two_factor_info = cl.last_json.get("two_factor_info", {}) or {}
        two_factor_identifier = two_factor_info.get("two_factor_identifier")
        verification_method = "3"
        # Prefer explicit hints from Instagram about which method is enabled.
        if two_factor_info.get("totp_two_factor_on"):
            verification_method = "3"
        elif two_factor_info.get("sms_two_factor_on"):
            verification_method = "1"
        elif two_factor_info.get("email_two_factor_on"):
            verification_method = "2"
        data = {
            "verification_code": code,
            "phone_id": cl.phone_id,
            "_csrftoken": cl.token,
            "two_factor_identifier": two_factor_identifier,
            "username": login_username,
            "trust_this_device": "1",
            "guid": cl.uuid,
            "device_id": cl.android_device_id,
            "waterfall_id": str(uuid4()),
            "verification_method": verification_method,
        }
        cl.private_request("accounts/two_factor_login/", data, login=True)
        cl.authorization_data = cl.parse_authorization(cl.last_response.headers.get("ig-set-authorization"))

    def _login_with_password():
        # Reset auth state to ensure we trigger a real login (keeps device IDs stable).
        try:
            cl.authorization_data = {}
        except Exception:
            pass
        try:
            cl.private.headers.pop("Authorization", None)
        except Exception:
            pass
        try:
            cl.private.cookies.clear()
        except Exception:
            pass
        try:
            if PRE_LOGIN_FLOW_ENABLED:
                try:
                    cl.pre_login_flow()
                except Exception:
                    pass
            _login_with_trust()
        except TwoFactorRequired:
            two_factor_info = cl.last_json.get("two_factor_info", {}) or {}
            detected_method = _derive_two_factor_method(two_factor_info)
            _auth_trace(login_username, "two_factor_required", two_factor_method=detected_method)
            if verification_code:
                _auth_trace(
                    login_username,
                    "two_factor_submit",
                    two_factor_method=detected_method,
                    totp_source="manual_code",
                )
                print("Two-factor code supplied (env/request)", flush=True)
                _two_factor_login_with_trust(verification_code.strip())
            elif normalized_totp_seed:
                print("Two-factor required: using configured TOTP seed", flush=True)
                last_exc = None
                for code in _totp_candidate_codes(normalized_totp_seed):
                    try:
                        _auth_trace(
                            login_username,
                            "two_factor_submit",
                            two_factor_method=detected_method,
                            totp_source="seed_window",
                        )
                        _two_factor_login_with_trust(code)
                        last_exc = None
                        break
                    except Exception as exc:
                        last_exc = exc
                if last_exc:
                    raise last_exc
            else:
                print("Two-factor required: waiting for code", flush=True)
                code = _poll_two_factor_code(login_username)
                if not code:
                    raise TwoFactorRequired("Two-factor authentication required (missing code)")
                print("Two-factor code received", flush=True)
                _auth_trace(
                    login_username,
                    "two_factor_submit",
                    two_factor_method=detected_method,
                    totp_source="challenge_store",
                )
                _two_factor_login_with_trust(code)
        if POST_LOGIN_FLOW_ENABLED:
            try:
                cl.login_flow()
            except Exception:
                pass
        try:
            cl.last_login = time.time()
        except Exception:
            pass

    def _probe_session():
        """Low-noise session validation probe; avoid feed/timeline churn."""
        try:
            cl.account_info()
            return
        except Exception:
            pass
        cl.private_request("accounts/current_user/?edit=true")

    try:
        mode = _normalize_login_mode(login_mode)
        if mode == "session_only":
            mode = "session"
        if settings:
            cl.set_settings(settings)
            if mode == "password":
                _login_with_password()
            else:
                try:
                    _probe_session()
                    reset_session_validation_failures(login_username)
                    _auth_trace(login_username, "session_validation_ok", session_validation_ok=True)
                except LoginRequired:
                    streak = record_session_validation_failure(login_username)
                    _auth_trace(
                        login_username,
                        "session_validation_failed",
                        error_code="login_required",
                        session_fail_streak=streak,
                    )
                    if streak >= SESSION_STALE_THRESHOLD:
                        _clear_cached_session(login_username)
                        _auth_trace(
                            login_username,
                            "session_marked_stale",
                            session_marked_stale=True,
                            session_fail_streak=streak,
                        )
                    if mode == "session":
                        raise
                    _login_with_password()
                except TwoFactorRequired:
                    streak = record_session_validation_failure(login_username)
                    _auth_trace(
                        login_username,
                        "session_validation_failed",
                        error_code="two_factor_required",
                        session_fail_streak=streak,
                    )
                    if streak >= SESSION_STALE_THRESHOLD:
                        _clear_cached_session(login_username)
                        _auth_trace(
                            login_username,
                            "session_marked_stale",
                            session_marked_stale=True,
                            session_fail_streak=streak,
                        )
                    if mode == "session":
                        raise
                    _login_with_password()
                except Exception:
                    streak = record_session_validation_failure(login_username)
                    _auth_trace(
                        login_username,
                        "session_validation_failed",
                        error_code="session_probe_error",
                        session_fail_streak=streak,
                    )
                    if streak >= SESSION_STALE_THRESHOLD:
                        _clear_cached_session(login_username)
                        _auth_trace(
                            login_username,
                            "session_marked_stale",
                            session_marked_stale=True,
                            session_fail_streak=streak,
                        )
                    if mode == "session":
                        raise
                    _login_with_password()
        else:
            if mode == "session":
                raise LoginRequired("session-only requested but no session cached")
            _login_with_password()
    except Exception as exc:
        try:
            set_last_error(login_username, str(exc))
        except Exception:
            pass
        _raise_login_error(exc, getattr(cl, "last_json", None))

    try:
        _save_settings(login_username, cl.get_settings())
    except Exception:
        pass
    try:
        reset_session_validation_failures(login_username)
    except Exception:
        pass
    try:
        set_last_login(login_username)
    except Exception:
        pass
    _auth_trace(login_username, "login_success", session_validation_ok=True)
    if new_password_used["value"]:
        try:
            set_login_password(login_username, new_password_used["value"])
        except Exception:
            pass
    return cl


def _sleep_jitter(delay_min: float, delay_max: float) -> None:
    delay_min = float(delay_min or 0)
    delay_max = float(delay_max or 0)
    if delay_min <= 0 and delay_max <= 0:
        return
    if delay_max and delay_min > delay_max:
        delay_min, delay_max = delay_max, delay_min
    time.sleep(random.uniform(delay_min, max(delay_min, delay_max)))


def _user_info_private_first(client: Client, username: str):
    """Prefer private API user info to avoid public web 429s."""
    try:
        return client.user_info_by_username_v1(username)
    except Exception:
        return client.user_info_by_username(username)


def _maybe_pause(
    page_index: int,
    pause_every_min: int,
    pause_every_max: int,
    pause_seconds_min: float,
    pause_seconds_max: float,
):
    if not pause_every_min and not pause_every_max:
        return
    pause_every_min = int(pause_every_min or 0)
    pause_every_max = int(pause_every_max or 0)
    if pause_every_max and pause_every_min > pause_every_max:
        pause_every_min, pause_every_max = pause_every_max, pause_every_min
    if pause_every_min <= 0 and pause_every_max <= 0:
        return
    pause_every = random.randint(pause_every_min or 1, pause_every_max or pause_every_min or 1)
    if pause_every <= 0:
        return
    if page_index % pause_every == 0:
        pause_seconds_min = float(pause_seconds_min or 0)
        pause_seconds_max = float(pause_seconds_max or 0)
        if pause_seconds_max and pause_seconds_min > pause_seconds_max:
            pause_seconds_min, pause_seconds_max = pause_seconds_max, pause_seconds_min
        duration = random.uniform(pause_seconds_min, max(pause_seconds_min, pause_seconds_max))
        if duration > 0:
            time.sleep(duration)


def generate_totp_seed(
    *,
    login_username,
    login_password,
    http_timeout_seconds=None,
    request_sleep_seconds=None,
    request_timeout=120.0,
    two_factor_code=None,
    challenge_code=None,
    device_settings_json=None,
    user_agent=None,
):
    proxy = load_proxy_from_env()
    proxy_url = proxy.get("url") if proxy else None
    client = _build_client(
        login_username,
        login_password or "",
        proxy_url,
        http_timeout_seconds=http_timeout_seconds,
        request_sleep_seconds=request_sleep_seconds,
        request_timeout=request_timeout,
        two_factor_code=two_factor_code,
        challenge_code=challenge_code,
        login_mode="password",
        device_settings_json=device_settings_json,
        user_agent=user_agent,
    )
    return client.totp_generate_seed()


def enable_totp(
    *,
    login_username,
    login_password,
    totp_seed,
    verification_code=None,
    http_timeout_seconds=None,
    request_sleep_seconds=None,
    request_timeout=120.0,
    two_factor_code=None,
    challenge_code=None,
    device_settings_json=None,
    user_agent=None,
):
    proxy = load_proxy_from_env()
    proxy_url = proxy.get("url") if proxy else None
    client = _build_client(
        login_username,
        login_password or "",
        proxy_url,
        http_timeout_seconds=http_timeout_seconds,
        request_sleep_seconds=request_sleep_seconds,
        request_timeout=request_timeout,
        two_factor_code=two_factor_code,
        challenge_code=challenge_code,
        login_mode="password",
        device_settings_json=device_settings_json,
        user_agent=user_agent,
    )
    normalized_seed = _normalize_totp_seed(totp_seed)
    if not normalized_seed:
        raise ValueError("invalid totp seed")
    if not verification_code:
        verification_code = _totp_code_for_timestamp(normalized_seed)
    backup_codes = client.totp_enable(verification_code)
    set_totp_seed(login_username, normalized_seed)
    return backup_codes


def disable_totp(
    *,
    login_username,
    login_password,
    http_timeout_seconds=None,
    request_sleep_seconds=None,
    request_timeout=120.0,
    two_factor_code=None,
    challenge_code=None,
    device_settings_json=None,
    user_agent=None,
):
    proxy = load_proxy_from_env()
    proxy_url = proxy.get("url") if proxy else None
    client = _build_client(
        login_username,
        login_password or "",
        proxy_url,
        http_timeout_seconds=http_timeout_seconds,
        request_sleep_seconds=request_sleep_seconds,
        request_timeout=request_timeout,
        two_factor_code=two_factor_code,
        challenge_code=challenge_code,
        login_mode="password",
        device_settings_json=device_settings_json,
        user_agent=user_agent,
    )
    ok = client.totp_disable()
    if ok:
        set_totp_seed(login_username, "")
    return ok


def generate_totp_code(seed: str) -> str:
    return _totp_code_for_timestamp(seed)


def request_password_reset(
    *,
    email_or_username: str,
    proxy_url: str | None = None,
    http_timeout_seconds: float | None = None,
    user_agent: str | None = None,
) -> dict:
    identifier = (email_or_username or "").strip()
    if not identifier:
        raise ValueError("email_or_username is required")
    timeout_seconds = float(http_timeout_seconds or 30.0)
    if timeout_seconds <= 0:
        timeout_seconds = 30.0
    effective_user_agent = _load_user_agent(user_agent) or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/11.1.2 Safari/605.1.15"
    )
    headers = {
        "x-requested-with": "XMLHttpRequest",
        "x-csrftoken": instagrapi_utils.gen_token(),
        "Connection": "Keep-Alive",
        "Accept": "*/*",
        "Accept-Encoding": "gzip,deflate",
        "Accept-Language": "en-US",
        "User-Agent": effective_user_agent,
    }
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    response = requests.post(
        "https://www.instagram.com/accounts/account_recovery_send_ajax/",
        data={"email_or_username": identifier, "recaptcha_challenge_field": ""},
        headers=headers,
        proxies=proxies,
        timeout=timeout_seconds,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": (response.text or "")[:500]}
    if response.status_code >= 400:
        raise RuntimeError(f"password reset request failed: http {response.status_code} payload={payload}")
    return {"http_status": response.status_code, "payload": payload}


def signup_account_private_api(
    *,
    login_username: str,
    login_password: str,
    email: str,
    full_name: str,
    phone_number: str = "",
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
    poll_seconds: int = 300,
    poll_interval: float = 5.0,
    proxy_url: str | None = None,
    http_timeout_seconds=None,
    request_sleep_seconds=None,
):
    if not proxy_url:
        proxy = load_proxy_from_env()
        proxy_url = proxy.get("url") if proxy else None
    client = _build_public_client(
        proxy_url,
        http_timeout_seconds=http_timeout_seconds,
        request_sleep_seconds=request_sleep_seconds,
        request_timeout=120.0,
    )
    # Compatibility shim: some instagrapi versions reference `self.device_id`
    # in signup() even though only `android_device_id` is initialized.
    if not getattr(client, "device_id", None):
        try:
            client.device_id = getattr(client, "android_device_id", None) or client.generate_android_device_id()
        except Exception:
            client.device_id = getattr(client, "android_device_id", "")
    # signup() polls challenge_code_handler for the email OTP code.
    # We route that through the same challenge-code store used elsewhere.
    base_challenge_handler = _challenge_code_handler_factory(login_username, None)
    client.challenge_code_handler = base_challenge_handler
    client.wait_seconds = max(1, int(poll_interval or 5.0))

    started = time.time()

    def _timed_out() -> bool:
        return (time.time() - started) > max(60, int(poll_seconds or 300))

    def _timed_handler(username, choice):
        if _timed_out():
            return False
        return base_challenge_handler(username, choice)

    client.challenge_code_handler = _timed_handler

    try:
        user = client.signup(
            username=login_username,
            password=login_password,
            email=email,
            phone_number=phone_number or "",
            full_name=full_name or "",
            year=year,
            month=month,
            day=day,
        )
    except EmailInvalidError as exc:
        raise PrivateAPIError("email_invalid", str(exc))
    except EmailNotAvailableError as exc:
        raise PrivateAPIError("email_not_available", str(exc))
    except EmailVerificationSendError as exc:
        raise PrivateAPIError("email_send_failed", str(exc))
    except AgeEligibilityError as exc:
        raise PrivateAPIError("age_not_eligible", str(exc))
    except (RequestsSSLError, RequestsProxyError) as exc:
        raise PrivateAPIError(
            "ssl_or_proxy_error",
            f"signup transport error (ssl/proxy): {exc}",
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "ssl" in msg and ("wrong version number" in msg or "certificate verify failed" in msg):
            raise PrivateAPIError(
                "ssl_or_proxy_error",
                f"signup transport error (ssl/proxy): {exc}",
            )
        if "429" in msg or "too many requests" in msg or "too many 429" in msg:
            raise PrivateAPIError(
                "rate_limited",
                "instagram rate limited signup (429); retry later or rotate proxy session",
            )
        _raise_login_error(exc, getattr(client, "last_json", None))

    try:
        _save_settings(login_username, client.get_settings())
    except Exception:
        pass
    try:
        set_last_login(login_username)
    except Exception:
        pass
    return {
        "username": getattr(user, "username", login_username),
        "pk": getattr(user, "pk", None),
    }


def fetch_counts(
    *,
    login_username,
    login_password,
    target_username,
    cookie_file=None,
    http_timeout_seconds=None,
    request_sleep_seconds=None,
    request_timeout=120.0,
    login_mode="auto",
    two_factor_code=None,
    challenge_code=None,
    totp_seed=None,
    delay_min=None,
    delay_max=None,
    device_settings_json=None,
    user_agent=None,
):
    _ = cookie_file
    mode = _normalize_login_mode(login_mode)
    proxy = load_proxy_from_env()
    proxy_url = proxy.get("url") if proxy else None
    if _is_anonymous_login_mode(mode):
        client = _build_public_client(
            proxy_url,
            http_timeout_seconds=http_timeout_seconds,
            request_sleep_seconds=request_sleep_seconds,
            request_timeout=request_timeout,
            delay_min=delay_min,
            delay_max=delay_max,
        )
        with _trace_span(
            "instalab.public_api.username_info",
            login_username=login_username,
            target_username=target_username,
        ):
            user = client.user_info_by_username_gql(target_username)
        if getattr(user, "is_private", False):
            raise RuntimeError("anonymous mode only supports public target accounts")
    else:
        client = _build_client(
            login_username,
            login_password or "",
            proxy_url,
            http_timeout_seconds=http_timeout_seconds,
            request_sleep_seconds=request_sleep_seconds,
            request_timeout=request_timeout,
            two_factor_code=two_factor_code,
            challenge_code=challenge_code,
            totp_seed=totp_seed,
            login_mode=mode,
            delay_min=delay_min,
            delay_max=delay_max,
            device_settings_json=device_settings_json,
            user_agent=user_agent,
        )

        with _trace_span(
            "instalab.private_api.username_info",
            login_username=login_username,
            target_username=target_username,
        ):
            user = _user_info_private_first(client, target_username)
    return {
        "followers_count": int(getattr(user, "follower_count", 0) or 0),
        "followees_count": int(getattr(user, "following_count", 0) or 0),
        "target_id": getattr(user, "pk", None),
    }


def snapshot_profile(
    *,
    login_username,
    login_password,
    target_username,
    cookie_file=None,
    http_timeout_seconds=None,
    request_sleep_seconds=None,
    request_timeout=600.0,
    db_path=None,
    cancel_check=None,
    progress=None,
    profile_only=False,
    login_mode="auto",
    item_delay_min=0.0,
    item_delay_max=0.0,
    fetch_order="followers_first",
    initial_fetch_delay_seconds=0.0,
    pause_every_min=0,
    pause_every_max=0,
    pause_seconds_min=0.0,
    pause_seconds_max=0.0,
    trace_enabled=False,
    trace_path=None,
    two_factor_code=None,
    challenge_code=None,
    totp_seed=None,
    device_settings_json=None,
    user_agent=None,
):
    tz = ZoneInfo("America/New_York")
    mode = _normalize_login_mode(login_mode)
    anonymous_mode = _is_anonymous_login_mode(mode)

    proxy = load_proxy_from_env()
    proxy_url = proxy.get("url") if proxy else None

    if progress:
        try:
            progress("login", 0)
        except Exception:
            pass

    if anonymous_mode:
        client = _build_public_client(
            proxy_url,
            http_timeout_seconds=http_timeout_seconds,
            request_sleep_seconds=request_sleep_seconds,
            request_timeout=request_timeout,
            delay_min=item_delay_min,
            delay_max=item_delay_max,
        )
        with _trace_span(
            "instalab.public_api.username_info",
            login_username=login_username,
            target_username=target_username,
        ):
            user = client.user_info_by_username_gql(target_username)
        if getattr(user, "is_private", False):
            raise RuntimeError("anonymous mode only supports public target accounts")
    else:
        client = _build_client(
            login_username,
            login_password or "",
            proxy_url,
            http_timeout_seconds=http_timeout_seconds,
            request_sleep_seconds=request_sleep_seconds,
            request_timeout=request_timeout,
            two_factor_code=two_factor_code,
            challenge_code=challenge_code,
            totp_seed=totp_seed,
            login_mode=mode,
            delay_min=item_delay_min,
            delay_max=item_delay_max,
            device_settings_json=device_settings_json,
            user_agent=user_agent,
            trace_enabled=trace_enabled,
            trace_path=trace_path,
        )

        with _trace_span(
            "instalab.private_api.username_info",
            login_username=login_username,
            target_username=target_username,
        ):
            user = _user_info_private_first(client, target_username)

    target_id = getattr(user, "pk", None)
    if not target_id:
        raise RuntimeError("failed to resolve target user id")

    followers_total = int(getattr(user, "follower_count", 0) or 0)
    following_total = int(getattr(user, "following_count", 0) or 0)

    if progress:
        try:
            progress("totals", {"followers_total": followers_total, "following_total": following_total})
        except Exception:
            pass

    timestamp = datetime.now(tz).strftime("%Y-%m-%d_%H-%M-%S")

    if profile_only:
        changes = None
        run_id = None
        if db_path:
            changes, run_id = write_run_profile_counts(
                db_path=db_path,
                login_username=login_username,
                target_username=target_username,
                timestamp=timestamp,
                followers_count=followers_total,
                followees_count=following_total,
            )
        return {
            "timestamp": timestamp,
            "followers_count": followers_total,
            "followees_count": following_total,
            "non_followbacks_count": 0,
            "changes": changes,
            "run_id": run_id,
            "followers_fetch_seconds": 0,
            "followees_fetch_seconds": 0,
            "followers_rate": None,
            "followees_rate": None,
            "profile_only": True,
        }

    if cancel_check and cancel_check():
        raise RuntimeError("cancelled")

    page_progress_state = None
    if progress and not anonymous_mode:
        page_progress_state = {
            "target_id": str(target_id),
            "callback": progress,
            "followers_seen": set(),
            "following_seen": set(),
        }
        client._instalab_page_progress = page_progress_state
        _enable_progress_private_request(client)

    try:
        settle = float(initial_fetch_delay_seconds or 0.0)
        if settle > 0:
            if progress:
                try:
                    progress("settle", int(settle))
                except Exception:
                    pass
            time.sleep(min(settle, 120.0))
        order = str(fetch_order or "followers_first").strip().lower()
        if order not in {"followers_first", "following_first"}:
            order = "followers_first"

        def _fetch_followers():
            t0 = time.time()
            if progress:
                try:
                    progress("followers", 0)
                except Exception:
                    pass
            if anonymous_mode:
                with _trace_span(
                    "instalab.public_api.user_followers",
                    login_username=login_username,
                    target_username=target_username,
                    target_id=target_id,
                ):
                    follower_items = client.user_followers_gql(str(target_id), amount=0)
                vals = [u.username for u in follower_items if getattr(u, "username", None)]
            else:
                with _trace_span(
                    "instalab.private_api.user_followers",
                    login_username=login_username,
                    target_username=target_username,
                    target_id=target_id,
                ):
                    followers_map = client.user_followers(target_id, amount=0)
                vals = [u.username for u in followers_map.values() if getattr(u, "username", None)]
            secs = int(time.time() - t0)
            if progress:
                try:
                    progress("followers", len(vals))
                except Exception:
                    pass
            return vals, secs

        def _fetch_following():
            t0 = time.time()
            if progress:
                try:
                    progress("following", 0)
                except Exception:
                    pass
            if anonymous_mode:
                with _trace_span(
                    "instalab.public_api.user_following",
                    login_username=login_username,
                    target_username=target_username,
                    target_id=target_id,
                ):
                    followee_items = client.user_following_gql(str(target_id), amount=0)
                vals = [u.username for u in followee_items if getattr(u, "username", None)]
            else:
                with _trace_span(
                    "instalab.private_api.user_following",
                    login_username=login_username,
                    target_username=target_username,
                    target_id=target_id,
                ):
                    followees_map = client.user_following(target_id, amount=0)
                vals = [u.username for u in followees_map.values() if getattr(u, "username", None)]
            secs = int(time.time() - t0)
            if progress:
                try:
                    progress("following", len(vals))
                except Exception:
                    pass
            return vals, secs

        if order == "following_first":
            followees, followees_fetch_seconds = _fetch_following()
            _sleep_jitter(item_delay_min, item_delay_max)
            _maybe_pause(1, pause_every_min, pause_every_max, pause_seconds_min, pause_seconds_max)
            if cancel_check and cancel_check():
                raise RuntimeError("cancelled")
            followers, followers_fetch_seconds = _fetch_followers()
        else:
            followers, followers_fetch_seconds = _fetch_followers()
            _sleep_jitter(item_delay_min, item_delay_max)
            _maybe_pause(1, pause_every_min, pause_every_max, pause_seconds_min, pause_seconds_max)
            if cancel_check and cancel_check():
                raise RuntimeError("cancelled")
            followees, followees_fetch_seconds = _fetch_following()

        non_followbacks = sorted(set(followees) - set(followers))
        followers_rate = round(len(followers) / followers_fetch_seconds, 3) if followers_fetch_seconds else None
        followees_rate = round(len(followees) / followees_fetch_seconds, 3) if followees_fetch_seconds else None

        changes = None
        run_id = None
        if db_path:
            changes, run_id = write_run_metadata(
                db_path=db_path,
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

        return {
            "timestamp": timestamp,
            "followers_count": followers_total,
            "followees_count": following_total,
            "followers": followers,
            "followees": followees,
            "non_followbacks": non_followbacks,
            "non_followbacks_count": len(non_followbacks),
            "changes": changes,
            "run_id": run_id,
            "followers_fetch_seconds": followers_fetch_seconds,
            "followees_fetch_seconds": followees_fetch_seconds,
            "followers_rate": followers_rate,
            "followees_rate": followees_rate,
        }
    finally:
        if page_progress_state is not None:
            client._instalab_page_progress = None
