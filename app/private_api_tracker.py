"""Private Instagram API tracker using instagrapi.

Reference implementation patterned after Osintgram-style workflows but using
the actively maintained instagrapi client.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime
from uuid import uuid4
from pathlib import Path
from zoneinfo import ZoneInfo

from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    ChallengeError,
    FeedbackRequired,
    ClientThrottledError,
    LoginRequired,
    LegacyForceSetNewPasswordForm,
    PleaseWaitFewMinutes,
    RateLimitError,
    TwoFactorRequired,
)
from instagrapi import utils as instagrapi_utils

from tracker_db import write_run_metadata, write_run_profile_counts
from login_store import (
    consume_challenge_code,
    consume_new_password,
    get_login,
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
ANONYMOUS_LOGIN_MODES = {"anonymous", "public", "no_login", "no-login", "anon"}

DEFAULT_DEVICE_SETTINGS = {
    "app_version": "414.0.0.40.83",
    "android_version": 28,
    "android_release": "9",
    "dpi": "480dpi",
    "resolution": "1080x1920",
    "manufacturer": "OnePlus",
    "device": "devitron",
    "model": "6T Dev",
    "cpu": "qcom",
    "version_code": "382006479",
}


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
        return None


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


def _raise_login_error(exc: Exception) -> None:
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


def _load_device_settings(device_settings_json: str | None) -> dict:
    raw = (device_settings_json or os.getenv(DEVICE_SETTINGS_ENV) or os.getenv(DEVICE_SETTINGS_GLOBAL_ENV) or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return dict(DEFAULT_DEVICE_SETTINGS)


def _load_user_agent(user_agent: str | None) -> str | None:
    value = (user_agent or os.getenv(USER_AGENT_ENV) or os.getenv(USER_AGENT_GLOBAL_ENV) or "").strip()
    return value or None


def _needs_device_upgrade(current: dict | None, desired: dict | None) -> bool:
    if not desired:
        return False
    if not current:
        return True
    for key in ("app_version", "version_code", "android_version", "android_release"):
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
    current = (client.get_settings() or {}).get("device_settings") or client.device_settings or {}
    if not desired:
        return False
    if not force and not _needs_device_upgrade(current, desired):
        return False
    merged = dict(current or {})
    merged.update({k: v for k, v in desired.items() if v is not None and v != ""})
    client.set_device(merged)
    if user_agent:
        client.set_user_agent(user_agent)
    else:
        client.set_user_agent("")
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
    if not login_password and not settings:
        raise RuntimeError("missing RUN_LOGIN_PASSWORD (no private session cached)")

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
    desired_device_settings = _load_device_settings(device_settings_json)
    user_agent_override = _load_user_agent(user_agent)
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
            # Attempt to resolve checkpoint/challenge using handler + stored code.
            client.challenge_resolve(client.last_json)
            try:
                _save_settings(login_username, client.get_settings())
            except Exception:
                pass
            return
        if isinstance(exc, LoginRequired):
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
    if not verification_code and totp_seed:
        try:
            verification_code = cl.totp_generate_code(totp_seed) or ""
        except Exception:
            verification_code = ""

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
            try:
                cl.pre_login_flow()
            except Exception:
                pass
            _login_with_trust()
        except TwoFactorRequired:
            code = verification_code.strip()
            if not code:
                print("Two-factor required: waiting for code", flush=True)
                code = _poll_two_factor_code(login_username)
            if not code:
                raise TwoFactorRequired("Two-factor authentication required (missing code)")
            print("Two-factor code received", flush=True)
            _two_factor_login_with_trust(code)
        try:
            cl.login_flow()
        except Exception:
            pass
        try:
            cl.last_login = time.time()
        except Exception:
            pass

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
                    cl.get_timeline_feed()
                except LoginRequired:
                    if mode == "session":
                        raise
                    _login_with_password()
                except TwoFactorRequired:
                    if mode == "session":
                        raise
                    _login_with_password()
                except Exception:
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
        _raise_login_error(exc)

    try:
        _save_settings(login_username, cl.get_settings())
    except Exception:
        pass
    try:
        set_last_login(login_username)
    except Exception:
        pass
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
    if not verification_code:
        verification_code = client.totp_generate_code(totp_seed)
    backup_codes = client.totp_enable(verification_code)
    set_totp_seed(login_username, totp_seed)
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
    client = Client()
    return client.totp_generate_code(seed)


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
            followers = [u.username for u in follower_items if getattr(u, "username", None)]
        else:
            with _trace_span(
                "instalab.private_api.user_followers",
                login_username=login_username,
                target_username=target_username,
                target_id=target_id,
            ):
                followers_map = client.user_followers(target_id, amount=0)
            followers = [u.username for u in followers_map.values() if getattr(u, "username", None)]
        followers_fetch_seconds = int(time.time() - t0)
        if progress:
            try:
                progress("followers", len(followers))
            except Exception:
                pass

        _sleep_jitter(item_delay_min, item_delay_max)
        _maybe_pause(1, pause_every_min, pause_every_max, pause_seconds_min, pause_seconds_max)

        if cancel_check and cancel_check():
            raise RuntimeError("cancelled")

        t1 = time.time()
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
            followees = [u.username for u in followee_items if getattr(u, "username", None)]
        else:
            with _trace_span(
                "instalab.private_api.user_following",
                login_username=login_username,
                target_username=target_username,
                target_id=target_id,
            ):
                followees_map = client.user_following(target_id, amount=0)
            followees = [u.username for u in followees_map.values() if getattr(u, "username", None)]
        followees_fetch_seconds = int(time.time() - t1)
        if progress:
            try:
                progress("following", len(followees))
            except Exception:
                pass

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
