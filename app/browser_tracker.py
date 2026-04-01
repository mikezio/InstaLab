"""Browser-backed Instagram tracker using persistent per-login browser profiles."""

from __future__ import annotations

import os
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse
import requests

try:
    from instaloader import Instaloader, Profile
    from instaloader.exceptions import (
        AbortDownloadException,
        BadCredentialsException,
        BadResponseException,
        ConnectionException,
        LoginException,
        LoginRequiredException,
        PrivateProfileNotFollowedException,
        ProfileNotExistsException,
        QueryReturnedBadRequestException,
        QueryReturnedForbiddenException,
        QueryReturnedNotFoundException,
        TooManyRequestsException,
        TwoFactorAuthRequiredException,
    )
    INSTALOADER_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - old containers may not have the dependency yet
    Instaloader = Profile = None  # type: ignore[assignment]
    AbortDownloadException = BadCredentialsException = BadResponseException = ConnectionException = None  # type: ignore[assignment]
    LoginException = LoginRequiredException = PrivateProfileNotFollowedException = None  # type: ignore[assignment]
    ProfileNotExistsException = QueryReturnedBadRequestException = QueryReturnedForbiddenException = None  # type: ignore[assignment]
    QueryReturnedNotFoundException = TooManyRequestsException = TwoFactorAuthRequiredException = None  # type: ignore[assignment]
    INSTALOADER_AVAILABLE = False
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from account_browser_flow import (
    browser_executable_path,
    browser_profile_dir_for_login,
    browser_profile_has_state,
    persist_storage_state_if_authenticated,
    seed_context_from_storage_state,
    storage_state_has_session,
)

from tracker_db import get_recent_complete_run_totals, write_run_metadata, write_run_profile_counts


ANONYMOUS_LOGIN_MODES = {"anonymous", "public", "no_login", "no-login", "anon"}
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
GRAPHQL_PAGE_SIZE = 50
FOLLOWERS_QUERY_HASH = "37479f2b8209594dde7facb0d904896a"
FOLLOWING_QUERY_HASH = "58712303d941c6855d4e888c5f0cd22f"
INSTAGRAM_WEB_APP_ID = "936619743392459"
INSTAGRAM_AJAX_VERSION = "1011846198"


class BrowserTrackerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _visibility_limited_message(payload: dict | None, kind: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    special = payload.get("special_empty_state")
    if not isinstance(special, dict):
        return None
    users = payload.get("users")
    if users not in (None, [], ()):
        return None
    body = str(special.get("body") or "").strip()
    title = str(special.get("title") or "").strip()
    detail = body or title or "instagram limited list visibility for this target"
    return f"instagram visibility limited while fetching {kind}: {detail}"


def _normalize_login_mode(login_mode: str | None) -> str:
    mode = (login_mode or "auto").strip().lower()
    if mode in {"anon", "public", "no_login", "no-login"}:
        return "anonymous"
    if mode in {"session", "session-only"}:
        return "session_only"
    if mode == "password-only":
        return "password"
    if mode in {"auto", "session_only", "password", "anonymous"}:
        return mode
    return "auto"


def _is_anonymous_login_mode(login_mode: str | None) -> bool:
    return _normalize_login_mode(login_mode) in ANONYMOUS_LOGIN_MODES


def _safe_username(value: str) -> str:
    name = (value or "").strip()
    if USERNAME_RE.match(name):
        return name
    return re.sub(r"[^A-Za-z0-9._]+", "_", name) or "login"


def _storage_path_for_login(login_username: str) -> Path:
    specific_path = str(os.getenv("INSTALAB_BROWSER_STORAGE_PATH", "") or "").strip()
    if specific_path:
        return Path(specific_path)
    base_dir = str(os.getenv("INSTALAB_BROWSER_STORAGE_DIR", "") or "").strip()
    if base_dir:
        return Path(base_dir) / f"{_safe_username(login_username)}.json"
    return Path(__file__).resolve().parent / ".playwright" / "instagram_storage.json"


def browser_storage_path_for_login(login_username: str) -> Path:
    return _storage_path_for_login(login_username)


def has_session(login_username: str) -> bool:
    return storage_state_has_session(str(_storage_path_for_login(login_username)), expected_username=login_username)


def _session_file_for_login(login_username: str) -> Path:
    return _storage_path_for_login(login_username).with_suffix(".session")


def _load_storage_cookie_dict(login_username: str) -> dict[str, str]:
    storage_path = _storage_path_for_login(login_username)
    try:
        payload = json.loads(storage_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BrowserTrackerError(
            "auth_required",
            f"missing browser session state; run /api/collector/auth/init to sign in first (expected {storage_path})",
        ) from exc
    except Exception as exc:
        raise BrowserTrackerError("browser_error", f"could not read browser session state: {exc}") from exc
    cookies: dict[str, str] = {}
    for item in payload.get("cookies") or []:
        domain = str((item or {}).get("domain") or "")
        if "instagram.com" not in domain:
            continue
        name = str((item or {}).get("name") or "").strip()
        if not name:
            continue
        cookies[name] = str((item or {}).get("value") or "")
    required_cookie_names = ("sessionid", "csrftoken", "ds_user_id")
    missing = [name for name in required_cookie_names if not cookies.get(name)]
    if missing:
        missing_list = ", ".join(missing)
        raise BrowserTrackerError(
            "auth_required",
            "browser session is incomplete "
            f"(missing {missing_list}); rerun /api/collector/auth/init and finish login in the opened browser",
        )
    return cookies


def _web_session_headers(*, login_username: str, target_username: str | None = None, csrf_token: str = "", user_agent: str | None = None) -> dict[str, str]:
    referer_target = str(target_username or login_username or "").strip().strip("/")
    referer = "https://www.instagram.com/"
    if referer_target:
        referer = f"https://www.instagram.com/{referer_target}/"
    headers = {
        "Accept": "*/*",
        "Referer": referer,
        "User-Agent": str(user_agent or os.getenv("INSTALAB_BROWSER_USER_AGENT") or DEFAULT_UA).strip() or DEFAULT_UA,
        "X-CSRFToken": csrf_token,
        "X-IG-App-ID": INSTAGRAM_WEB_APP_ID,
        "X-Instagram-AJAX": INSTAGRAM_AJAX_VERSION,
        "X-Requested-With": "XMLHttpRequest",
    }
    return headers


def _build_web_session(login_username: str, *, user_agent: str | None = None) -> tuple[requests.Session, dict[str, str]]:
    cookies = _load_storage_cookie_dict(login_username)
    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".instagram.com", path="/")
    session.headers.update(
        _web_session_headers(
            login_username=login_username,
            csrf_token=cookies.get("csrftoken", ""),
            user_agent=user_agent,
        )
    )
    return session, cookies


def _response_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        status = str(payload.get("status") or "").strip()
        message = str(payload.get("message") or payload.get("error_type") or "").strip()
        checkpoint = str(payload.get("checkpoint_url") or "").strip()
        pieces = [piece for piece in (status, message, checkpoint) if piece]
        if pieces:
            return " - ".join(pieces)
    text = ""
    try:
        text = (response.text or "").strip()
    except Exception:
        text = ""
    return text[:200] if text else f"http {response.status_code}"


def _raise_for_web_response(response: requests.Response, *, context: str) -> None:
    lowered = _response_error_message(response).lower()
    if response.status_code in {401, 403} or any(token in lowered for token in ("checkpoint", "challenge", "login_required", "login required")):
        raise BrowserTrackerError("auth_required", f"{context}: {_response_error_message(response)}")
    if response.status_code == 429 or "please wait a few minutes" in lowered or "rate limit" in lowered:
        raise BrowserTrackerError("rate_limited", f"{context}: {_response_error_message(response)}")
    if response.status_code >= 400:
        raise BrowserTrackerError("api_error", f"{context}: {_response_error_message(response)}")


def _web_profile_payload(payload: dict, target_username: str) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        raise BrowserTrackerError("parse_error", f"missing web profile payload for {target_username}")
    return user


def _fetch_profile_via_web_session(
    session: requests.Session,
    *,
    login_username: str,
    target_username: str,
    http_timeout_seconds: float,
    user_agent: str | None = None,
) -> dict:
    response = session.get(
        "https://www.instagram.com/api/v1/users/web_profile_info/",
        params={"username": target_username},
        headers=_web_session_headers(
            login_username=login_username,
            target_username=target_username,
            csrf_token=session.cookies.get("csrftoken", ""),
            user_agent=user_agent,
        ),
        timeout=max(30.0, float(http_timeout_seconds or 90.0)),
    )
    _raise_for_web_response(response, context=f"instagram web_profile_info for {target_username}")
    try:
        payload = response.json()
    except Exception as exc:
        raise BrowserTrackerError("api_error", f"invalid web_profile_info response for {target_username}: {exc}") from exc
    user = _web_profile_payload(payload, target_username)
    user_id = str(user.get("id") or user.get("pk") or "").strip()
    if not user_id:
        raise BrowserTrackerError("parse_error", f"missing target user id in web_profile_info for {target_username}")
    return {
        "id": user_id,
        "followers_count": int(user.get("edge_followed_by", {}).get("count") or user.get("follower_count") or 0),
        "followees_count": int(user.get("edge_follow", {}).get("count") or user.get("following_count") or 0),
        "is_private": bool(user.get("is_private")),
    }


def _friendship_users_payload(payload: dict, kind: str) -> tuple[list[dict], str | None]:
    if not isinstance(payload, dict):
        raise BrowserTrackerError("parse_error", f"invalid friendship payload while fetching {kind}")
    visibility_message = _visibility_limited_message(payload, kind)
    if visibility_message:
        raise BrowserTrackerError("visibility_limited", visibility_message)
    users = payload.get("users")
    if not isinstance(users, list):
        raise BrowserTrackerError("parse_error", f"missing friendship users payload while fetching {kind}")
    next_max_id = (
        payload.get("next_max_id")
        or payload.get("max_id")
        or payload.get("next_maxid")
        or payload.get("end_cursor")
    )
    next_token = str(next_max_id).strip() if next_max_id not in (None, "") else None
    return users, next_token


def _collect_usernames_via_web_session(
    session: requests.Session,
    *,
    login_username: str,
    target_username: str,
    target_user_id: str,
    kind: str,
    expected_total: int,
    progress_phase: str,
    progress=None,
    http_timeout_seconds: float = 90.0,
    delay_min: float = 0.2,
    delay_max: float = 0.6,
    pause_every_min: int = 0,
    pause_every_max: int = 0,
    pause_seconds_min: float = 0.0,
    pause_seconds_max: float = 0.0,
    user_agent: str | None = None,
) -> tuple[list[str], float]:
    started = time.time()
    usernames: set[str] = set()
    checkpoint_every = 25
    chunk_delay_min = max(0.0, float(delay_min or 0.0))
    chunk_delay_max = max(chunk_delay_min, float(delay_max or 0.0))
    pause_every_low = max(0, int(pause_every_min or 0))
    pause_every_high = max(pause_every_low, int(pause_every_max or 0))
    pause_seconds_low = max(0.0, float(pause_seconds_min or 0.0))
    pause_seconds_high = max(pause_seconds_low, float(pause_seconds_max or 0.0))

    def _next_pause_after() -> int | None:
        if pause_every_low <= 0 or pause_seconds_high <= 0:
            return None
        return random.randint(pause_every_low, pause_every_high)

    next_pause_at = _next_pause_after()
    next_max_id: str | None = None
    empty_pages = 0
    max_pages = max(60, min(3000, (expected_total // GRAPHQL_PAGE_SIZE) + 80)) if expected_total > 0 else 200
    endpoint = f"https://www.instagram.com/api/v1/friendships/{target_user_id}/{kind}/"
    if progress:
        try:
            progress(progress_phase, 0)
        except Exception:
            pass

    for _ in range(max_pages):
        params = {"count": str(GRAPHQL_PAGE_SIZE)}
        if next_max_id:
            params["max_id"] = next_max_id
        response = session.get(
            endpoint,
            params=params,
            headers=_web_session_headers(
                login_username=login_username,
                target_username=target_username,
                csrf_token=session.cookies.get("csrftoken", ""),
                user_agent=user_agent,
            ),
            timeout=max(30.0, float(http_timeout_seconds or 90.0)),
        )
        _raise_for_web_response(response, context=f"instagram friendships {kind} for {target_username}")
        try:
            payload = response.json()
        except Exception as exc:
            raise BrowserTrackerError("api_error", f"invalid friendships response while fetching {kind}: {exc}") from exc

        users, candidate_next = _friendship_users_payload(payload, kind)
        before = len(usernames)
        for user in users:
            username = str((user or {}).get("username") or "").strip()
            if USERNAME_RE.match(username):
                usernames.add(username)
        if progress and len(usernames) != before:
            try:
                progress(progress_phase, len(usernames))
            except Exception:
                pass

        if expected_total > 0 and len(usernames) >= expected_total:
            break

        if not users:
            empty_pages += 1
        else:
            empty_pages = 0
        if empty_pages >= 3:
            break

        next_max_id = candidate_next
        if not next_max_id:
            break

        if len(usernames) % checkpoint_every == 0 and chunk_delay_max > 0:
            time.sleep(random.uniform(chunk_delay_min, chunk_delay_max))
        if next_pause_at and len(usernames) >= next_pause_at:
            time.sleep(random.uniform(pause_seconds_low, pause_seconds_high))
            next_interval = _next_pause_after()
            next_pause_at = len(usernames) + next_interval if next_interval else None

    if progress:
        try:
            progress(progress_phase, len(usernames))
        except Exception:
            pass
    return sorted(usernames), max(0.0, time.time() - started)


def _build_instaloader(http_timeout_seconds: float, user_agent: str | None = None) -> Instaloader:
    if not INSTALOADER_AVAILABLE:
        raise BrowserTrackerError("dependency_error", "instaloader is not installed in this container")
    kwargs = {
        "sleep": True,
        "quiet": True,
        "max_connection_attempts": 1,
        "request_timeout": max(30.0, float(http_timeout_seconds or 300.0)),
    }
    effective_user_agent = str(user_agent or os.getenv("INSTALAB_BROWSER_USER_AGENT") or "").strip()
    if effective_user_agent:
        kwargs["user_agent"] = effective_user_agent
    return Instaloader(**kwargs)


def _fetch_profile_via_instaloader(
    *,
    login_username: str,
    target_username: str,
    login_mode: str,
    http_timeout_seconds: float,
    user_agent: str | None = None,
) -> dict:
    loader = _load_logged_in_instaloader(
        login_username=login_username,
        login_mode=login_mode,
        http_timeout_seconds=float(http_timeout_seconds or 120.0),
        user_agent=user_agent,
    )
    target = Profile.from_username(loader.context, target_username)
    target_id = getattr(target, "userid", None)
    if target_id is None:
        target_id = getattr(target, "_userid", None)
    return {
        "id": str(target_id or "").strip(),
        "followers_count": int(getattr(target, "followers", 0) or 0),
        "followees_count": int(getattr(target, "followees", 0) or 0),
        "is_private": bool(getattr(target, "is_private", False)),
    }


def _session_username_mismatch(expected_username: str, actual_username: str) -> BrowserTrackerError:
    return BrowserTrackerError(
        "auth_mismatch",
        (
            "browser session belongs to "
            f"@{actual_username}, expected @{expected_username}; interactive login required"
        ),
    )


def _load_logged_in_instaloader(
    *,
    login_username: str,
    login_mode: str,
    http_timeout_seconds: float,
    user_agent: str | None = None,
) -> Instaloader:
    loader = _build_instaloader(http_timeout_seconds=http_timeout_seconds, user_agent=user_agent)
    if _is_anonymous_login_mode(login_mode):
        return loader

    session_path = _session_file_for_login(login_username)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    def _validate_logged_in_username(actual_username: str | None) -> str:
        actual = str(actual_username or "").strip()
        if not actual:
            raise BrowserTrackerError("auth_required", "browser session expired; interactive login required")
        if actual.lower() != str(login_username).strip().lower():
            raise _session_username_mismatch(login_username, actual)
        loader.context.username = actual
        return actual

    if session_path.exists():
        try:
            loader.load_session_from_file(login_username, filename=str(session_path))
            actual = _validate_logged_in_username(loader.test_login())
            ds_user_id = loader.context._session.cookies.get("ds_user_id")  # type: ignore[attr-defined]
            if ds_user_id and str(ds_user_id).isdigit():
                loader.context.user_id = int(ds_user_id)
            return loader
        except Exception:
            try:
                session_path.unlink()
            except Exception:
                pass

    cookies = _load_storage_cookie_dict(login_username)
    try:
        loader.context.load_session(login_username, cookies)
        actual = _validate_logged_in_username(loader.test_login())
    except BrowserTrackerError:
        raise
    except Exception as exc:
        raise BrowserTrackerError("auth_required", f"browser session import failed: {exc}") from exc

    ds_user_id = cookies.get("ds_user_id")
    if ds_user_id and str(ds_user_id).isdigit():
        loader.context.user_id = int(ds_user_id)
    try:
        loader.context.username = actual
        loader.save_session_to_file(filename=str(session_path))
    except Exception:
        pass
    return loader


def _map_instaloader_exception(exc: Exception) -> BrowserTrackerError:
    if not INSTALOADER_AVAILABLE:
        return BrowserTrackerError("browser_error", str(exc) or exc.__class__.__name__)
    if isinstance(exc, BrowserTrackerError):
        return exc
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    if isinstance(exc, (LoginRequiredException, BadCredentialsException, LoginException, TwoFactorAuthRequiredException)):
        return BrowserTrackerError("auth_required", message)
    if isinstance(exc, AbortDownloadException):
        if any(token in lowered for token in ("challenge", "checkpoint", "login")):
            return BrowserTrackerError("auth_required", message)
        return BrowserTrackerError("browser_error", message)
    if isinstance(exc, TooManyRequestsException):
        return BrowserTrackerError("rate_limited", message)
    if isinstance(exc, PrivateProfileNotFollowedException):
        return BrowserTrackerError("visibility_limited", message)
    if isinstance(exc, (ProfileNotExistsException, QueryReturnedNotFoundException)):
        return BrowserTrackerError("not_found", message)
    if isinstance(exc, QueryReturnedForbiddenException):
        if "please wait" in lowered or "too many" in lowered or "429" in lowered:
            return BrowserTrackerError("rate_limited", message)
        return BrowserTrackerError("visibility_limited", message)
    if isinstance(exc, QueryReturnedBadRequestException):
        if any(token in lowered for token in ("challenge", "checkpoint", "login")):
            return BrowserTrackerError("auth_required", message)
        return BrowserTrackerError("api_error", message)
    if isinstance(exc, (ConnectionException, BadResponseException)):
        return BrowserTrackerError("browser_error", message)
    return BrowserTrackerError("browser_error", message)


def _collect_usernames_from_iterator(
    iterator,
    *,
    phase: str,
    progress=None,
    delay_min: float = 0.0,
    delay_max: float = 0.0,
    pause_every_min: int = 0,
    pause_every_max: int = 0,
    pause_seconds_min: float = 0.0,
    pause_seconds_max: float = 0.0,
) -> tuple[list[str], float]:
    started = time.time()
    usernames: set[str] = set()
    checkpoint_every = 25
    chunk_delay_min = max(0.0, float(delay_min or 0.0))
    chunk_delay_max = max(chunk_delay_min, float(delay_max or 0.0))
    pause_every_low = max(0, int(pause_every_min or 0))
    pause_every_high = max(pause_every_low, int(pause_every_max or 0))
    pause_seconds_low = max(0.0, float(pause_seconds_min or 0.0))
    pause_seconds_high = max(pause_seconds_low, float(pause_seconds_max or 0.0))

    def _next_pause_after() -> int | None:
        if pause_every_low <= 0 or pause_seconds_high <= 0:
            return None
        return random.randint(pause_every_low, pause_every_high)

    next_pause_at = _next_pause_after()
    if progress:
        try:
            progress(phase, 0)
        except Exception:
            pass
    for idx, profile in enumerate(iterator, start=1):
        username = str(getattr(profile, "username", "") or "").strip()
        if USERNAME_RE.match(username):
            usernames.add(username)
        if progress and (idx == 1 or idx % checkpoint_every == 0):
            try:
                progress(phase, len(usernames))
            except Exception:
                pass
        if idx % checkpoint_every == 0 and chunk_delay_max > 0:
            time.sleep(random.uniform(chunk_delay_min, chunk_delay_max))
        if next_pause_at and idx >= next_pause_at:
            time.sleep(random.uniform(pause_seconds_low, pause_seconds_high))
            next_interval = _next_pause_after()
            next_pause_at = idx + next_interval if next_interval else None
    if progress:
        try:
            progress(phase, len(usernames))
        except Exception:
            pass
    return sorted(usernames), max(0.0, time.time() - started)


def _parse_compact_number(value: str | None) -> int | None:
    text = (value or "").strip().replace(",", "").replace(" ", "")
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)([KMB])?", text, flags=re.IGNORECASE)
    if not m:
        return None
    base = float(m.group(1))
    suffix = (m.group(2) or "").upper()
    factor = 1
    if suffix == "K":
        factor = 1_000
    elif suffix == "M":
        factor = 1_000_000
    elif suffix == "B":
        factor = 1_000_000_000
    return int(base * factor)


def _now_snapshot_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _maybe_accept_cookies(page) -> None:
    for label in (
        "Allow all cookies",
        "Accept all cookies",
        "Accept",
        "Only allow essential cookies",
    ):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=2000)
                return
        except Exception:
            continue


def _login_required(page) -> bool:
    try:
        if "accounts/login" in (page.url or ""):
            return True
    except Exception:
        pass
    try:
        return page.locator('input[name="username"]').count() > 0
    except Exception:
        return False


def _on_expected_profile(page, target_username: str) -> bool:
    target = (target_username or "").strip().strip("/").lower()
    if not target:
        return False
    try:
        parsed = urlparse(str(page.url or ""))
    except Exception:
        return False
    path = (parsed.path or "/").strip().lower().rstrip("/")
    return path in {f"/{target}", f"/{target}/".rstrip("/")}


def _open_profile_page(page, target_username: str, *, timeout_ms: int | None = None) -> None:
    kwargs = {"wait_until": "domcontentloaded"}
    if timeout_ms and timeout_ms > 0:
        kwargs["timeout"] = int(timeout_ms)
    page.goto(f"https://www.instagram.com/{target_username}/", **kwargs)
    _maybe_accept_cookies(page)
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass
    page.wait_for_timeout(500)


def _extract_counts(page) -> tuple[int, int]:
    payload = page.evaluate(
        """
() => {
  const findCount = (kind) => {
    const link =
      document.querySelector(`a[href$='/${kind}/']`) ||
      document.querySelector(`a[href*='/${kind}/']`);
    if (!link) return "";
    const titled = link.querySelector("span[title]");
    if (titled && titled.getAttribute("title")) return titled.getAttribute("title");
    return link.textContent || "";
  };
  return {
    followers: findCount("followers"),
    following: findCount("following"),
  };
}
"""
    )
    followers = _parse_compact_number((payload or {}).get("followers"))
    following = _parse_compact_number((payload or {}).get("following"))
    if followers is None or following is None:
        fallback = page.evaluate(
            """
() => {
  const og = document.querySelector('meta[property="og:description"]')?.getAttribute("content") || "";
  const desc = document.querySelector('meta[name="description"]')?.getAttribute("content") || "";
  const txt = document.body ? (document.body.innerText || "") : "";
  return { og, desc, txt };
}
"""
        )

        def _from_text(text: str | None):
            s = (text or "").replace(",", "")
            m_followers = re.search(r"(\d+(?:\.\d+)?)\s+Followers?", s, flags=re.IGNORECASE)
            m_following = re.search(r"(\d+(?:\.\d+)?)\s+Following", s, flags=re.IGNORECASE)
            if not (m_followers and m_following):
                return None, None
            return int(float(m_followers.group(1))), int(float(m_following.group(1)))

        for candidate in ((fallback or {}).get("og"), (fallback or {}).get("desc"), (fallback or {}).get("txt")):
            f1, f2 = _from_text(candidate)
            if f1 is not None and f2 is not None:
                followers, following = f1, f2
                break

    if followers is None or following is None:
        current_url = ""
        try:
            current_url = str(page.url or "")
        except Exception:
            current_url = ""
        raise BrowserTrackerError(
            "parse_error",
            f"could not parse followers/following counts from profile page (url={current_url})",
        )
    return int(followers), int(following)


def _extract_target_user_id(page) -> str | None:
    try:
        html = page.content()
    except Exception:
        return None
    m = re.search(r"profilePage_(\d+)", html)
    if m:
        return m.group(1)
    m = re.search(r'"user_id":"(\d+)"', html)
    if m:
        return m.group(1)
    return None


def _profile_metadata_from_payload(payload: dict, target_username: str) -> dict | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        return None
    user_id = str(user.get("id") or user.get("pk") or "").strip()
    if not user_id:
        return None
    followers_count = int(user.get("edge_followed_by", {}).get("count") or user.get("follower_count") or 0)
    followees_count = int(user.get("edge_follow", {}).get("count") or user.get("following_count") or 0)
    return {
        "id": user_id,
        "followers_count": followers_count,
        "followees_count": followees_count,
        "is_private": bool(user.get("is_private")),
        "target_username": target_username,
    }


def _fetch_profile_metadata_in_page(page, target_username: str) -> dict | None:
    try:
        payload = page.evaluate(
            """
async ({ username, appId }) => {
  const response = await fetch(`/api/v1/users/web_profile_info/?username=${encodeURIComponent(username)}`, {
    method: "GET",
    credentials: "include",
    headers: {
      "X-IG-App-ID": appId,
      "X-Requested-With": "XMLHttpRequest",
    },
  });
  const text = await response.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch (err) {
    json = null;
  }
  return { status: response.status, json, text: text.slice(0, 200) };
}
""",
            {"username": target_username, "appId": INSTAGRAM_WEB_APP_ID},
        )
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    status = int(payload.get("status") or 0)
    if status >= 400:
        return None
    return _profile_metadata_from_payload(payload.get("json") or {}, target_username)


def _resolve_profile_metadata(page, target_username: str, *, timeout_ms: int | None = None) -> dict:
    captured: dict[str, object] = {}

    def _response_listener(response):
        try:
            if "users/web_profile_info" not in str(response.url or ""):
                return
            payload = response.json()
        except Exception:
            return
        meta = _profile_metadata_from_payload(payload, target_username)
        if meta:
            captured.update(meta)

    page.on("response", _response_listener)
    _open_profile_page(page, target_username, timeout_ms=timeout_ms)
    try:
        page.wait_for_timeout(750)
    except Exception:
        pass

    followers_count, followees_count = _extract_counts(page)
    target_user_id = str(captured.get("id") or _extract_target_user_id(page) or "").strip()
    if not target_user_id:
        raise BrowserTrackerError("parse_error", f"could not resolve target user id for {target_username}")

    return {
        "id": target_user_id,
        # Prefer the live profile page counts when available. The captured
        # web_profile_info payload can disagree with what Instagram is
        # actively rendering for the session we are collecting through.
        "followers_count": int(followers_count or captured.get("followers_count") or 0),
        "followees_count": int(followees_count or captured.get("followees_count") or 0),
        "is_private": bool(captured.get("is_private")),
        "target_username": target_username,
    }


def _refresh_visible_profile_counts(page, target_username: str, *, timeout_ms: int | None = None) -> tuple[int, int]:
    _open_profile_page(page, target_username, timeout_ms=timeout_ms)
    return _extract_counts(page)


def _choose_expected_total(
    samples: list[int],
    *,
    collected_total: int,
    baseline_samples: list[int] | None = None,
    tolerance: int = 5,
) -> tuple[int, dict]:
    valid = [int(v) for v in samples if int(v or 0) > 0]
    baseline_valid = [int(v) for v in (baseline_samples or []) if int(v or 0) > 0]
    if not valid:
        chosen = max(0, int(collected_total or 0))
        return chosen, {
            "samples": [],
            "baseline_samples": baseline_valid,
            "sample_count": 0,
            "spread": 0,
            "tolerance": int(tolerance),
            "reason": "no_visible_samples",
            "chosen_total": chosen,
            "collected_total": int(collected_total or 0),
        }
    minimum = min(valid)
    maximum = max(valid)
    latest = valid[-1]
    spread = maximum - minimum
    baseline_median = None
    if baseline_valid:
        ordered = sorted(baseline_valid)
        baseline_median = ordered[len(ordered) // 2]
    if spread <= int(tolerance):
        chosen = max(int(collected_total or 0), latest)
        reason = "stable_visible_total"
    else:
        chosen = max(int(collected_total or 0), minimum)
        reason = "noisy_visible_total_using_minimum"
    if baseline_median is not None and abs(chosen - baseline_median) > max(25, int(baseline_median * 0.05)):
        chosen = max(int(collected_total or 0), min(chosen, baseline_median))
        reason = f"{reason}_adjusted_by_recent_complete_baseline"
    return chosen, {
        "samples": valid,
        "baseline_samples": baseline_valid,
        "baseline_median": baseline_median,
        "sample_count": len(valid),
        "spread": spread,
        "tolerance": int(tolerance),
        "reason": reason,
        "chosen_total": chosen,
        "collected_total": int(collected_total or 0),
        "minimum_visible_total": minimum,
        "maximum_visible_total": maximum,
        "latest_visible_total": latest,
    }


def _graphql_headers(page, target_username: str) -> dict:
    cookies = page.context.cookies("https://www.instagram.com/")
    csrf = ""
    for cookie in cookies:
        if cookie.get("name") == "csrftoken":
            csrf = str(cookie.get("value") or "")
            break
    headers = {
        "x-csrftoken": csrf,
        "x-ig-app-id": INSTAGRAM_WEB_APP_ID,
        "x-instagram-ajax": INSTAGRAM_AJAX_VERSION,
        "x-requested-with": "XMLHttpRequest",
        "referer": f"https://www.instagram.com/{target_username}/",
        "accept": "*/*",
    }
    try:
        claim = page.evaluate("() => localStorage.getItem('www-claim-v2') || ''")
    except Exception:
        claim = ""
    if claim:
        headers["x-ig-www-claim"] = str(claim)
    return headers


def _friendship_users_payload_from_page(payload: dict, kind: str) -> tuple[list[dict], str | None]:
    if not isinstance(payload, dict):
        raise BrowserTrackerError("parse_error", f"invalid friendship payload while fetching {kind}")
    visibility_message = _visibility_limited_message(payload, kind)
    if visibility_message:
        raise BrowserTrackerError("visibility_limited", visibility_message)
    users = payload.get("users")
    if not isinstance(users, list):
        raise BrowserTrackerError("parse_error", f"missing friendship users payload while fetching {kind}")
    next_max_id = (
        payload.get("next_max_id")
        or payload.get("max_id")
        or payload.get("next_maxid")
        or payload.get("end_cursor")
    )
    next_token = str(next_max_id).strip() if next_max_id not in (None, "") else None
    return users, next_token


def _relation_dialog_matches(page, kind: str) -> bool:
    try:
        return bool(
            page.evaluate(
                """
(kind) => {
  const dialog = document.querySelector("div[role='dialog']");
  if (!dialog) return false;
  const wanted = String(kind || "").toLowerCase();
  const text = (dialog.innerText || "").toLowerCase();
  if (text.includes(wanted)) return true;
  const links = [...dialog.querySelectorAll("a[href]")].map((a) => (a.getAttribute("href") || "").toLowerCase());
  return links.some((href) => href.includes(`/${wanted}/`));
}
""",
                kind,
            )
        )
    except Exception:
        return False


def _close_relation_dialog(page) -> None:
    try:
        if page.locator("div[role='dialog']").count() <= 0:
            return
    except Exception:
        return
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
    except Exception:
        pass
    try:
        close_buttons = page.locator("div[role='dialog'] svg[aria-label='Close']")
        if close_buttons.count() > 0:
            close_buttons.first.click(timeout=2000)
            page.wait_for_timeout(250)
    except Exception:
        pass


def _collect_usernames_via_friendships_page(
    page,
    *,
    target_username: str,
    target_user_id: str,
    kind: str,
    expected_total: int,
    progress_phase: str,
    progress=None,
    artifact_dir: str | None = None,
    seed_usernames: list[str] | set[str] | None = None,
    delay_min: float = 0.2,
    delay_max: float = 0.6,
    pause_every_min: int = 0,
    pause_every_max: int = 0,
    pause_seconds_min: float = 0.0,
    pause_seconds_max: float = 0.0,
) -> tuple[list[str], float]:
    started = time.time()
    usernames: set[str] = {str(name) for name in (seed_usernames or []) if USERNAME_RE.match(str(name or ""))}
    seeded_count = len(usernames)
    checkpoint_every = 25
    chunk_delay_min = max(0.0, float(delay_min or 0.0))
    chunk_delay_max = max(chunk_delay_min, float(delay_max or 0.0))
    pause_every_low = max(0, int(pause_every_min or 0))
    pause_every_high = max(pause_every_low, int(pause_every_max or 0))
    pause_seconds_low = max(0.0, float(pause_seconds_min or 0.0))
    pause_seconds_high = max(pause_seconds_low, float(pause_seconds_max or 0.0))

    def _next_pause_after() -> int | None:
        if pause_every_low <= 0 or pause_seconds_high <= 0:
            return None
        return random.randint(pause_every_low, pause_every_high)

    next_pause_at = _next_pause_after()
    next_max_id: str | None = None
    empty_pages = 0
    low_gain_streak = 0
    max_pages = max(60, min(3000, (expected_total // GRAPHQL_PAGE_SIZE) + 80)) if expected_total > 0 else 200
    endpoint = f"https://www.instagram.com/api/v1/friendships/{target_user_id}/{kind}/"
    if progress:
        try:
            progress(progress_phase, len(usernames))
        except Exception:
            pass

    for page_index in range(max_pages):
        params = {"count": str(GRAPHQL_PAGE_SIZE)}
        if next_max_id:
            params["max_id"] = next_max_id
        response = page.request.get(endpoint, params=params, headers=_graphql_headers(page, target_username), timeout=60000)
        if response.status in {401, 403}:
            raise BrowserTrackerError("auth_required", f"instagram friendships blocked while fetching {kind}")
        if response.status == 429:
            raise BrowserTrackerError("rate_limited", f"instagram friendships rate limit while fetching {kind}")
        if response.status >= 400:
            body_snippet = ""
            try:
                body_snippet = " ".join((response.text() or "").split())[:240]
            except Exception:
                body_snippet = ""
            message = f"instagram friendships error {response.status} while fetching {kind}"
            if body_snippet:
                message = f"{message}: {body_snippet}"
            raise BrowserTrackerError("api_error", message)
        try:
            payload = response.json()
        except Exception as exc:
            raise BrowserTrackerError("api_error", f"invalid friendships response while fetching {kind}: {exc}") from exc

        users, candidate_next = _friendship_users_payload_from_page(payload, kind)
        before = len(usernames)
        for user in users:
            username = str((user or {}).get("username") or "").strip()
            if USERNAME_RE.match(username):
                usernames.add(username)
        newly_added = max(0, len(usernames) - before)
        if seeded_count > 0 and users:
            if newly_added <= 1:
                low_gain_streak += 1
            else:
                low_gain_streak = 0
        _append_browser_debug_jsonl(
            artifact_dir,
            f"{kind}-friendships-trace.jsonl",
            {
                "page_index": page_index,
                "status": int(response.status or 0),
                "requested_count": GRAPHQL_PAGE_SIZE,
                "received_users": len(users),
                "collected_before": before,
                "collected_after": len(usernames),
                "newly_added": newly_added,
                "candidate_next": bool(candidate_next),
                "empty_pages": empty_pages,
                "low_gain_streak": low_gain_streak,
                "seeded_count": seeded_count,
                "max_id_supplied": bool(next_max_id),
            },
        )
        if progress and len(usernames) != before:
            try:
                progress(progress_phase, len(usernames))
            except Exception:
                pass

        if expected_total > 0 and len(usernames) >= expected_total:
            break
        if not users:
            empty_pages += 1
        else:
            empty_pages = 0
        if seeded_count > 0 and low_gain_streak >= 4:
            break
        if empty_pages >= 3:
            break
        next_max_id = candidate_next
        if not next_max_id:
            break
        if len(usernames) % checkpoint_every == 0 and chunk_delay_max > 0:
            page.wait_for_timeout(int(random.uniform(chunk_delay_min, chunk_delay_max) * 1000))
        if next_pause_at and len(usernames) >= next_pause_at:
            page.wait_for_timeout(int(random.uniform(pause_seconds_low, pause_seconds_high) * 1000))
            next_interval = _next_pause_after()
            next_pause_at = len(usernames) + next_interval if next_interval else None

    if progress:
        try:
            progress(progress_phase, len(usernames))
        except Exception:
            pass
    return sorted(usernames), max(0.0, time.time() - started)


def _collect_usernames_via_dialog(
    page,
    *,
    kind: str,
    expected_total: int,
    progress_phase: str,
    progress=None,
    seed_usernames: list[str] | set[str] | None = None,
    delay_min: float = 0.2,
    delay_max: float = 0.7,
) -> tuple[list[str], float]:
    started = time.time()
    _open_relation_dialog(page, kind)
    names = _collect_usernames_from_open_dialog(
        page,
        expected_total=expected_total,
        progress_phase=progress_phase,
        progress=progress,
        seed_usernames=seed_usernames,
        delay_min=delay_min,
        delay_max=delay_max,
    )
    return names, max(0.0, time.time() - started)


def _write_browser_debug_artifacts(page, artifact_dir: str | None, prefix: str) -> list[str]:
    target_dir = str(artifact_dir or "").strip()
    if not target_dir:
        return []
    try:
        root = Path(target_dir)
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        return []

    written: list[str] = []
    screenshot_path = root / f"{prefix}.png"
    html_path = root / f"{prefix}.html"
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
        written.append(str(screenshot_path))
    except Exception:
        pass
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        written.append(str(html_path))
    except Exception:
        pass
    return written


def _append_browser_debug_jsonl(artifact_dir: str | None, filename: str, payload: dict) -> str | None:
    target_dir = str(artifact_dir or "").strip()
    if not target_dir:
        return None
    try:
        root = Path(target_dir)
        root.mkdir(parents=True, exist_ok=True)
        path = root / filename
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
        return str(path)
    except Exception:
        return None


def _should_accept_dialog_fallback(dialog_count: int, expected_total: int) -> bool:
    expected = max(0, int(expected_total or 0))
    count = max(0, int(dialog_count or 0))
    if expected <= 0:
        return True
    gap = max(0, expected - count)
    if gap <= 25:
        return True
    if expected < 100:
        return count >= max(1, int(expected * 0.6))
    return count >= max(100, int(expected * 0.5))


def _write_username_debug_dump(
    artifact_dir: str | None,
    *,
    kind: str,
    expected_total: int,
    usernames: list[str],
) -> list[str]:
    target_dir = str(artifact_dir or "").strip()
    if not target_dir:
        return []
    try:
        root = Path(target_dir)
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        return []

    payload = {
        "kind": kind,
        "expected_total": int(expected_total or 0),
        "collected_total": len(usernames),
        "missing_total": max(0, int(expected_total or 0) - len(usernames)),
        "usernames": list(usernames),
    }
    json_path = root / f"{kind}-usernames.json"
    txt_path = root / f"{kind}-usernames.txt"
    written: list[str] = []
    try:
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        written.append(str(json_path))
    except Exception:
        pass
    try:
        txt_path.write_text("\n".join(usernames) + ("\n" if usernames else ""), encoding="utf-8")
        written.append(str(txt_path))
    except Exception:
        pass
    return written


def _collect_usernames_browser_native(
    page,
    *,
    target_username: str,
    target_user_id: str,
    kind: str,
    expected_total: int,
    progress_phase: str,
    progress=None,
    artifact_dir: str | None = None,
    seed_usernames: list[str] | set[str] | None = None,
    delay_min: float = 0.2,
    delay_max: float = 0.6,
    pause_every_min: int = 0,
    pause_every_max: int = 0,
    pause_seconds_min: float = 0.0,
    pause_seconds_max: float = 0.0,
) -> tuple[list[str], float]:
    seeded = sorted({str(name).strip() for name in (seed_usernames or []) if USERNAME_RE.match(str(name or "").strip())})
    if progress and seeded:
        try:
            progress(progress_phase, len(seeded))
        except Exception:
            pass
    try:
        dialog_names, dialog_seconds = _collect_usernames_via_dialog(
            page,
            kind=kind,
            expected_total=expected_total,
            progress_phase=progress_phase,
            progress=progress,
            seed_usernames=seeded,
            delay_min=delay_min,
            delay_max=max(delay_min, delay_max),
        )
        if seeded:
            dialog_names = sorted(set(seeded) | set(dialog_names))
            if progress:
                try:
                    progress(progress_phase, len(dialog_names))
                except Exception:
                    pass
        gap_remaining = max(0, int(expected_total or 0) - len(dialog_names))
        if expected_total <= 0 or gap_remaining <= 0:
            return dialog_names, dialog_seconds
        if gap_remaining <= 25:
            return dialog_names, dialog_seconds
        try:
            topoff_names, topoff_seconds = _collect_usernames_via_friendships_page(
                page,
                target_username=target_username,
                target_user_id=target_user_id,
                kind=kind,
                expected_total=expected_total,
                progress_phase=progress_phase,
                progress=progress,
                artifact_dir=artifact_dir,
                seed_usernames=dialog_names,
                delay_min=delay_min,
                delay_max=delay_max,
                pause_every_min=pause_every_min,
                pause_every_max=pause_every_max,
                pause_seconds_min=pause_seconds_min,
                pause_seconds_max=pause_seconds_max,
            )
            return topoff_names, dialog_seconds + topoff_seconds
        except BrowserTrackerError as exc:
            if (
                exc.code == "api_error"
                and "friendships error 400" in str(exc)
                and len(dialog_names) == len(seeded)
            ):
                _open_profile_page(page, target_username, timeout_ms=30000)
                page.wait_for_timeout(1000)
                topoff_names, topoff_seconds = _collect_usernames_via_friendships_page(
                    page,
                    target_username=target_username,
                    target_user_id=target_user_id,
                    kind=kind,
                    expected_total=expected_total,
                    progress_phase=progress_phase,
                    progress=progress,
                    artifact_dir=artifact_dir,
                    seed_usernames=dialog_names,
                    delay_min=delay_min,
                    delay_max=delay_max,
                    pause_every_min=pause_every_min,
                    pause_every_max=pause_every_max,
                    pause_seconds_min=pause_seconds_min,
                    pause_seconds_max=pause_seconds_max,
                )
                return topoff_names, dialog_seconds + topoff_seconds
            _append_browser_debug_jsonl(
                artifact_dir,
                f"{kind}-topoff-failure.jsonl",
                {
                    "error_code": exc.code,
                    "error_message": str(exc),
                    "dialog_count": len(dialog_names),
                    "expected_total": int(expected_total or 0),
                    "gap_remaining": gap_remaining,
                },
            )
            if artifact_dir:
                _write_browser_debug_artifacts(page, artifact_dir, f"{kind}-topoff-failure")
            if not _should_accept_dialog_fallback(len(dialog_names), expected_total):
                raise
            return dialog_names, dialog_seconds
    except BrowserTrackerError as exc:
        if artifact_dir:
            _write_browser_debug_artifacts(page, artifact_dir, f"{kind}-dialog-failure")
        if exc.code not in {"ui_error", "parse_error"}:
            raise
        try:
            return _collect_usernames_via_friendships_page(
                page,
                target_username=target_username,
                target_user_id=target_user_id,
                kind=kind,
                expected_total=expected_total,
                progress_phase=progress_phase,
                progress=progress,
                artifact_dir=artifact_dir,
                delay_min=delay_min,
                delay_max=delay_max,
                pause_every_min=pause_every_min,
                pause_every_max=pause_every_max,
                pause_seconds_min=pause_seconds_min,
                pause_seconds_max=pause_seconds_max,
            )
        except BrowserTrackerError:
            if artifact_dir:
                _write_browser_debug_artifacts(page, artifact_dir, f"{kind}-friendships-failure")
            raise


def _retry_incomplete_relation_collection(
    page,
    *,
    target_username: str,
    target_user_id: str,
    kind: str,
    expected_total: int,
    existing_usernames: list[str],
    progress_phase: str,
    progress=None,
    artifact_dir: str | None = None,
    delay_min: float = 0.2,
    delay_max: float = 0.6,
    pause_every_min: int = 0,
    pause_every_max: int = 0,
    pause_seconds_min: float = 0.0,
    pause_seconds_max: float = 0.0,
    timeout_ms: int = 60000,
) -> tuple[list[str], float]:
    seeded = sorted(set(existing_usernames))
    if expected_total <= 0 or len(seeded) >= expected_total:
        return seeded, 0.0
    _open_profile_page(page, target_username, timeout_ms=timeout_ms)
    try:
        page.wait_for_timeout(1000)
    except Exception:
        pass
    names, elapsed_seconds = _collect_usernames_browser_native(
        page,
        target_username=target_username,
        target_user_id=target_user_id,
        kind=kind,
        expected_total=expected_total,
        progress_phase=progress_phase,
        progress=progress,
        artifact_dir=artifact_dir,
        seed_usernames=seeded,
        delay_min=delay_min,
        delay_max=delay_max,
        pause_every_min=pause_every_min,
        pause_every_max=pause_every_max,
        pause_seconds_min=pause_seconds_min,
        pause_seconds_max=pause_seconds_max,
    )
    _append_browser_debug_jsonl(
        artifact_dir,
        f"{kind}-retry-summary.jsonl",
        {
            "existing_count": len(seeded),
            "merged_count": len(names),
            "newly_added": max(0, len(names) - len(seeded)),
            "expected_total": int(expected_total or 0),
            "missing_after_retry": max(0, int(expected_total or 0) - len(names)),
            "elapsed_seconds": round(elapsed_seconds, 3),
        },
    )
    return names, elapsed_seconds


def _graphql_edge_payload(payload: dict, kind: str) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        raise BrowserTrackerError("parse_error", f"missing GraphQL user payload while fetching {kind}")
    edge_key = "edge_followed_by" if kind == "followers" else "edge_follow"
    edge = user.get(edge_key)
    if not isinstance(edge, dict):
        raise BrowserTrackerError("parse_error", f"missing GraphQL edge payload while fetching {kind}")
    return edge


def _collect_usernames_via_graphql(
    page,
    *,
    target_username: str,
    target_user_id: str,
    kind: str,
    expected_total: int,
    progress_phase: str,
    progress=None,
    delay_min: float = 0.2,
    delay_max: float = 0.6,
) -> list[str]:
    usernames: set[str] = set()
    end_cursor: str | None = None
    empty_pages = 0
    max_pages = max(60, min(3000, (expected_total // GRAPHQL_PAGE_SIZE) + 80)) if expected_total > 0 else 200
    query_hash = FOLLOWERS_QUERY_HASH if kind == "followers" else FOLLOWING_QUERY_HASH

    for _ in range(max_pages):
        variables = {"id": str(target_user_id), "first": GRAPHQL_PAGE_SIZE}
        if end_cursor:
            variables["after"] = str(end_cursor)
        query = [
            ("query_hash", query_hash),
            ("variables", json.dumps(variables, separators=(",", ":"))),
        ]
        url = f"https://www.instagram.com/graphql/query/?{urlencode(query)}"
        resp = page.request.get(url, headers=_graphql_headers(page, target_username), timeout=60000)
        if resp.status in {401, 403}:
            raise BrowserTrackerError("auth_required", "browser session expired; interactive login required")
        if resp.status == 429:
            raise BrowserTrackerError("rate_limited", f"instagram GraphQL rate limit while fetching {kind}")
        if resp.status >= 400:
            raise BrowserTrackerError("api_error", f"instagram GraphQL error {resp.status} while fetching {kind}")

        try:
            payload = resp.json()
        except Exception:
            body = ""
            try:
                body = (resp.text() or "")[:200]
            except Exception:
                body = ""
            lowered = body.lower()
            if "<!doctype html" in lowered or "<html" in lowered:
                if "login" in lowered:
                    raise BrowserTrackerError("auth_required", "browser session expired; interactive login required")
                raise BrowserTrackerError("rate_limited", f"instagram returned HTML instead of GraphQL JSON while fetching {kind}")
            raise BrowserTrackerError("api_error", f"unexpected non-JSON GraphQL response while fetching {kind}")

        edge_payload = _graphql_edge_payload(payload, kind)
        visibility_message = _visibility_limited_message(edge_payload, kind)
        if visibility_message:
            raise BrowserTrackerError("visibility_limited", visibility_message)

        edges = edge_payload.get("edges") or []
        before = len(usernames)
        for edge in edges:
            node = edge.get("node") if isinstance(edge, dict) else None
            username = str((node or {}).get("username") or "").strip()
            if USERNAME_RE.match(username):
                usernames.add(username)
        if progress and len(usernames) != before:
            try:
                progress(progress_phase, len(usernames))
            except Exception:
                pass

        if expected_total > 0 and len(usernames) >= expected_total:
            break

        page_info = edge_payload.get("page_info") if isinstance(edge_payload, dict) else None
        if not edges:
            empty_pages += 1
        else:
            empty_pages = 0

        if empty_pages >= 3:
            break

        if not isinstance(page_info, dict) or not page_info.get("has_next_page"):
            break
        end_cursor = str(page_info.get("end_cursor") or "").strip() or None
        if not end_cursor:
            break

        wait_seconds = random.uniform(max(0.0, delay_min), max(delay_min, delay_max))
        page.wait_for_timeout(int(wait_seconds * 1000))

    return sorted(usernames)


def _open_relation_dialog(page, kind: str) -> None:
    current_target = ""
    try:
        current_target = str(page.evaluate("() => window.location.pathname.split('/').filter(Boolean)[0] || ''") or "").strip()
    except Exception:
        current_target = ""
    selector_variants = []
    if current_target:
        selector_variants.extend(
            [
                f"a[href='/{current_target}/{kind}/']",
                f"a[href='/{current_target}/{kind}/?next=%2F']",
                f"section a[href='/{current_target}/{kind}/']",
                f"main a[href='/{current_target}/{kind}/']",
            ]
        )
    selector_variants.extend(
        [
            f"a[href$='/{kind}/']",
            f"a[href*='/{kind}/']",
            f"section a:has-text('{kind.capitalize()}')",
            f"main a:has-text('{kind.capitalize()}')",
        ]
    )
    _close_relation_dialog(page)
    clicked = False
    for selector in selector_variants:
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                try:
                    loc.first.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                try:
                    loc.first.click(timeout=7000)
                    page.wait_for_selector("div[role='dialog']", timeout=15000)
                    page.wait_for_timeout(500)
                    if _relation_dialog_matches(page, kind):
                        clicked = True
                        break
                    _close_relation_dialog(page)
                except Exception:
                    try:
                        clicked = bool(
                            page.evaluate(
                                """
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  el.click();
  return true;
}
""",
                                selector,
                            )
                        )
                    except Exception:
                        clicked = False
                    if clicked:
                        try:
                            page.wait_for_selector("div[role='dialog']", timeout=15000)
                            page.wait_for_timeout(500)
                        except Exception:
                            clicked = False
                        if clicked and _relation_dialog_matches(page, kind):
                            break
                        _close_relation_dialog(page)
                        clicked = False
        except Exception:
            continue
    if not clicked:
        try:
            role_link = page.get_by_role("link", name=re.compile(kind, re.IGNORECASE))
            if role_link.count() > 0:
                role_link.first.scroll_into_view_if_needed(timeout=2000)
                role_link.first.click(timeout=7000)
                page.wait_for_selector("div[role='dialog']", timeout=15000)
                page.wait_for_timeout(500)
                clicked = _relation_dialog_matches(page, kind)
                if not clicked:
                    _close_relation_dialog(page)
        except Exception:
            clicked = False
    if not clicked:
        raise BrowserTrackerError("ui_error", f"could not open {kind} dialog")


def _collect_usernames_from_open_dialog(
    page,
    *,
    expected_total: int,
    progress_phase: str,
    progress=None,
    seed_usernames: list[str] | set[str] | None = None,
    delay_min: float = 0.2,
    delay_max: float = 0.7,
) -> list[str]:
    usernames: set[str] = {str(name).strip() for name in (seed_usernames or []) if USERNAME_RE.match(str(name or "").strip())}
    stable_passes = 0
    max_loops = max(80, min(1200, (expected_total * 2) if expected_total > 0 else 260))
    if progress and usernames:
        try:
            progress(progress_phase, len(usernames))
        except Exception:
            pass
    for _ in range(max_loops):
        snapshot = page.evaluate(
            """
() => {
  const dialog = document.querySelector("div[role='dialog']");
  if (!dialog) return { usernames: [], canScroll: false, atBottom: true };
  const items = [];
  const anchors = [...dialog.querySelectorAll("a[href^='/']")];
  for (const a of anchors) {
    const href = (a.getAttribute("href") || "").split("?")[0];
    const m = href.match(/^\\/([A-Za-z0-9._]+)\\/$/);
    if (!m) continue;
    const u = m[1];
    if ([
      "accounts","about","developer","directory","explore",
      "legal","press","privacy","reels","stories","terms"
    ].includes(u)) continue;
    items.push(u);
  }
  const divs = [...dialog.querySelectorAll("div")];
  let scroller = null;
  for (const d of divs) {
    if (d.scrollHeight > d.clientHeight + 24) {
      if (!scroller || d.scrollHeight > scroller.scrollHeight) scroller = d;
    }
  }
  if (!scroller) return { usernames: [...new Set(items)], canScroll: false, atBottom: true };
  const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
  const before = scroller.scrollTop;
  scroller.scrollTop = Math.min(maxTop, before + Math.max(500, scroller.clientHeight * 0.9));
  const after = scroller.scrollTop;
  return {
    usernames: [...new Set(items)],
    canScroll: maxTop > 2,
    atBottom: (maxTop - after) <= 4
  };
}
"""
        )

        before = len(usernames)
        for name in (snapshot or {}).get("usernames") or []:
            if USERNAME_RE.match(str(name or "")):
                usernames.add(str(name))
        if progress and len(usernames) != before:
            try:
                progress(progress_phase, len(usernames))
            except Exception:
                pass

        if expected_total > 0 and len(usernames) >= expected_total:
            break

        if len(usernames) == before:
            stable_passes += 1
        else:
            stable_passes = 0

        can_scroll = bool((snapshot or {}).get("canScroll"))
        at_bottom = bool((snapshot or {}).get("atBottom"))
        if not can_scroll or (at_bottom and stable_passes >= 6) or stable_passes >= 12:
            break

        wait_seconds = random.uniform(max(0.0, delay_min), max(delay_min, delay_max))
        page.wait_for_timeout(int(wait_seconds * 1000))

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
    except Exception:
        pass
    return sorted(usernames)


def _with_browser_context(login_username: str, login_mode: str, fn, *, timeout_ms: int | None = None):
    storage_path = _storage_path_for_login(login_username)
    profile_dir = Path(browser_profile_dir_for_login(login_username))
    anonymous_mode = _is_anonymous_login_mode(login_mode)
    storage_ready = has_session(login_username)
    if not anonymous_mode and not storage_ready:
        raise BrowserTrackerError(
            "auth_required",
            (
                "missing browser session state; run /api/collector/auth/init to sign in first "
                f"(expected {storage_path})"
            ),
        )

    headless = str(os.getenv("INSTALAB_BROWSER_HEADLESS", "true")).strip().lower() in {"1", "true", "yes", "on"}
    launch_proxy = str(os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or "").strip()
    browser_proxy = None
    if launch_proxy:
        parsed = urlparse(launch_proxy)
        if parsed.hostname and parsed.port:
            proxy_payload = {"server": f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port}"}
            if parsed.username:
                proxy_payload["username"] = parsed.username
            if parsed.password:
                proxy_payload["password"] = parsed.password
            browser_proxy = proxy_payload
    user_agent = str(os.getenv("INSTALAB_BROWSER_USER_AGENT", "") or "").strip() or DEFAULT_UA

    with sync_playwright() as p:
        launch_args = {
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled"],
            "user_agent": user_agent,
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "viewport": {"width": 1366, "height": 768},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
        }
        executable_path = browser_executable_path()
        if executable_path:
            launch_args["executable_path"] = executable_path
        if browser_proxy:
            launch_args["proxy"] = browser_proxy
        profile_dir.mkdir(parents=True, exist_ok=True)
        context = p.chromium.launch_persistent_context(str(profile_dir), **launch_args)
        if storage_ready and not browser_profile_has_state(str(profile_dir)):
            seed_context_from_storage_state(context, str(storage_path))
        if storage_ready:
            persist_storage_state_if_authenticated(
                context,
                str(storage_path),
                expected_username=login_username,
            )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4]});"
        )
        page = context.pages[0] if context.pages else context.new_page()
        effective_timeout = int(timeout_ms) if timeout_ms and timeout_ms > 0 else 60000
        page.set_default_timeout(effective_timeout)
        page.set_default_navigation_timeout(effective_timeout)
        try:
            result = fn(page)
            try:
                persist_storage_state_if_authenticated(
                    context,
                    str(storage_path),
                    expected_username=login_username,
                )
            except Exception:
                pass
            return result
        finally:
            context.close()


def fetch_counts(
    *,
    login_username: str,
    login_password: str,
    target_username: str,
    cookie_file=None,
    http_timeout_seconds: float = 120.0,
    request_sleep_seconds: float = 0.0,
    login_mode: str = "auto",
    two_factor_code=None,
    challenge_code=None,
    totp_seed=None,
    delay_min: float = 0.0,
    delay_max: float = 0.0,
    device_settings_json=None,
    user_agent=None,
):
    del login_password, cookie_file, request_sleep_seconds
    del two_factor_code, challenge_code, totp_seed, device_settings_json, delay_min, delay_max
    timeout_ms = max(15000, int(float(http_timeout_seconds or 120.0) * 1000))

    def _runner(page):
        profile = _resolve_profile_metadata(page, target_username, timeout_ms=timeout_ms)
        if _login_required(page) and not _is_anonymous_login_mode(login_mode):
            raise BrowserTrackerError("auth_required", "browser session expired; interactive login required")
        if not _is_anonymous_login_mode(login_mode) and not _on_expected_profile(page, target_username):
            raise BrowserTrackerError(
                "auth_required",
                "browser session did not land on the requested profile; interactive login required",
            )
        return {
            "timestamp": _now_snapshot_ts(),
            "followers_count": int(profile["followers_count"]),
            "followees_count": int(profile["followees_count"]),
        }

    try:
        return _with_browser_context(login_username, login_mode, _runner, timeout_ms=timeout_ms)
    except BrowserTrackerError:
        raise
    except PlaywrightTimeoutError as exc:
        raise BrowserTrackerError("timeout", f"browser timeout while fetching counts: {exc}") from exc
    except Exception as exc:
        raise BrowserTrackerError("browser_error", str(exc)) from exc


def snapshot_profile(
    *,
    login_username: str,
    login_password: str,
    target_username: str,
    cookie_file=None,
    http_timeout_seconds: float = 600.0,
    request_sleep_seconds: float = 0.0,
    db_path: str = "tracker_data.db",
    profile_only: bool = False,
    login_mode: str = "auto",
    item_delay_min: float = 0.25,
    item_delay_max: float = 0.75,
    fetch_order: str = "followers_first",
    initial_fetch_delay_seconds: float = 0.0,
    pause_every_min: int = 0,
    pause_every_max: int = 0,
    pause_seconds_min: float = 0.0,
    pause_seconds_max: float = 0.0,
    trace_enabled: bool = False,
    trace_path: str = "",
    two_factor_code=None,
    challenge_code=None,
    totp_seed=None,
    device_settings_json=None,
    user_agent=None,
    progress=None,
    artifact_dir: str | None = None,
):
    del login_password, cookie_file, request_sleep_seconds
    del trace_enabled, trace_path, two_factor_code, challenge_code, totp_seed, device_settings_json
    if progress:
        progress("bootstrap", 0)

    timestamp = _now_snapshot_ts()
    timeout_ms = max(15000, int(float(http_timeout_seconds or 600.0) * 1000))

    def _runner(page):
        profile = _resolve_profile_metadata(page, target_username, timeout_ms=timeout_ms)
        recent_complete_runs = get_recent_complete_run_totals(target_username=target_username, limit=5)
        baseline_followers = [int(row.get("followers_count") or 0) for row in recent_complete_runs]
        baseline_following = [int(row.get("followees_count") or 0) for row in recent_complete_runs]
        if _login_required(page) and not _is_anonymous_login_mode(login_mode):
            raise BrowserTrackerError("auth_required", "browser session expired; interactive login required")
        if not _is_anonymous_login_mode(login_mode) and not _on_expected_profile(page, target_username):
            raise BrowserTrackerError(
                "auth_required",
                "browser session did not land on the requested profile; interactive login required",
            )
        followers_total = int(profile["followers_count"])
        following_total = int(profile["followees_count"])
        followers_total_samples = [followers_total]
        following_total_samples = [following_total]
        if progress:
            progress("totals", {"followers_total": followers_total, "following_total": following_total})

        if profile_only:
            changes, run_id = write_run_profile_counts(
                db_path=db_path,
                target_username=target_username,
                login_username=login_username,
                timestamp=timestamp,
                followers_count=followers_total,
                followees_count=following_total,
            )
            return {
                "timestamp": timestamp,
                "followers_count": followers_total,
                "followees_count": following_total,
                "followers_collected_count": followers_total,
                "followees_collected_count": following_total,
                "followers": [],
                "followees": [],
                "non_followbacks_count": 0,
                "followers_added": 0,
                "followers_removed": 0,
                "followees_added": 0,
                "followees_removed": 0,
                "changes": changes,
                "run_id": run_id,
                "followers_fetch_seconds": 0,
                "followees_fetch_seconds": 0,
                "followers_rate": None,
                "followees_rate": None,
                "profile_only": True,
            }

        if initial_fetch_delay_seconds > 0:
            page.wait_for_timeout(int(float(initial_fetch_delay_seconds) * 1000))

        def _collect(kind: str, total_hint: int) -> tuple[list[str], float]:
            phase_name = "followers" if kind == "followers" else "following"
            return _collect_usernames_browser_native(
                page,
                target_username=target_username,
                target_user_id=str(profile["id"]),
                kind=kind,
                expected_total=max(0, int(total_hint or 0)),
                progress_phase=phase_name,
                progress=progress,
                artifact_dir=artifact_dir,
                delay_min=item_delay_min,
                delay_max=item_delay_max,
                pause_every_min=pause_every_min,
                pause_every_max=pause_every_max,
                pause_seconds_min=pause_seconds_min,
                pause_seconds_max=pause_seconds_max,
            )

        order = str(fetch_order or "followers_first").strip().lower()
        if order not in {"followers_first", "following_first"}:
            order = "followers_first"

        if order == "following_first":
            followees, followees_fetch_seconds = _collect("following", following_total)
            followers, followers_fetch_seconds = _collect("followers", followers_total)
        else:
            followers, followers_fetch_seconds = _collect("followers", followers_total)
            followees, followees_fetch_seconds = _collect("following", following_total)

        refreshed_followers_total, refreshed_following_total = _refresh_visible_profile_counts(
            page,
            target_username,
            timeout_ms=min(timeout_ms, 30000),
        )
        if refreshed_followers_total > 0:
            followers_total_samples.append(int(refreshed_followers_total))
            followers_total = refreshed_followers_total
        if refreshed_following_total > 0:
            following_total_samples.append(int(refreshed_following_total))
            following_total = refreshed_following_total
        if progress:
            progress("totals", {"followers_total": followers_total, "following_total": following_total})

        retry_plan = []
        if len(followers) < followers_total:
            retry_plan.append(("followers", followers_total - len(followers)))
        if len(followees) < following_total:
            retry_plan.append(("following", following_total - len(followees)))
        retry_plan.sort(key=lambda item: item[1], reverse=True)

        for kind, _gap in retry_plan:
            if kind == "followers":
                refreshed, retry_seconds = _retry_incomplete_relation_collection(
                    page,
                    target_username=target_username,
                    target_user_id=str(profile["id"]),
                    kind="followers",
                    expected_total=followers_total,
                    existing_usernames=followers,
                    progress_phase="followers",
                    progress=progress,
                    artifact_dir=artifact_dir,
                    delay_min=item_delay_min,
                    delay_max=item_delay_max,
                    pause_every_min=pause_every_min,
                    pause_every_max=pause_every_max,
                    pause_seconds_min=pause_seconds_min,
                    pause_seconds_max=pause_seconds_max,
                    timeout_ms=timeout_ms,
                )
                followers = refreshed
                followers_fetch_seconds += retry_seconds
            else:
                refreshed, retry_seconds = _retry_incomplete_relation_collection(
                    page,
                    target_username=target_username,
                    target_user_id=str(profile["id"]),
                    kind="following",
                    expected_total=following_total,
                    existing_usernames=followees,
                    progress_phase="following",
                    progress=progress,
                    artifact_dir=artifact_dir,
                    delay_min=item_delay_min,
                    delay_max=item_delay_max,
                    pause_every_min=pause_every_min,
                    pause_every_max=pause_every_max,
                    pause_seconds_min=pause_seconds_min,
                    pause_seconds_max=pause_seconds_max,
                    timeout_ms=timeout_ms,
                )
                followees = refreshed
                followees_fetch_seconds += retry_seconds

        refreshed_followers_total, refreshed_following_total = _refresh_visible_profile_counts(
            page,
            target_username,
            timeout_ms=min(timeout_ms, 30000),
        )
        if refreshed_followers_total > 0:
            followers_total_samples.append(int(refreshed_followers_total))
            followers_total = refreshed_followers_total
        if refreshed_following_total > 0:
            following_total_samples.append(int(refreshed_following_total))
            following_total = refreshed_following_total

        followers_total, followers_total_meta = _choose_expected_total(
            followers_total_samples,
            collected_total=len(followers),
            baseline_samples=baseline_followers,
        )
        following_total, following_total_meta = _choose_expected_total(
            following_total_samples,
            collected_total=len(followees),
            baseline_samples=baseline_following,
        )
        _append_browser_debug_jsonl(
            artifact_dir,
            "count-decision-summary.jsonl",
            {
                "followers": followers_total_meta,
                "following": following_total_meta,
            },
        )
        if progress:
            progress("totals", {"followers_total": followers_total, "following_total": following_total})

        non_followbacks = sorted(set(followees) - set(followers))
        followers_rate = round(len(followers) / followers_fetch_seconds, 3) if followers_fetch_seconds else None
        followees_rate = round(len(followees) / followees_fetch_seconds, 3) if followees_fetch_seconds else None
        partial_collection = (len(followers) < int(followers_total)) or (len(followees) < int(following_total))
        if partial_collection:
            if artifact_dir:
                _write_browser_debug_artifacts(page, artifact_dir, "partial-collection")
                _write_username_debug_dump(
                    artifact_dir,
                    kind="followers",
                    expected_total=followers_total,
                    usernames=followers,
                )
                _write_username_debug_dump(
                    artifact_dir,
                    kind="following",
                    expected_total=following_total,
                    usernames=followees,
                )
            raise BrowserTrackerError(
                "incomplete_collection",
                (
                    "incomplete collection "
                    f"(followers {len(followers)}/{int(followers_total)}, "
                    f"following {len(followees)}/{int(following_total)})"
                ),
            )

        changes, run_id = write_run_metadata(
            db_path=db_path,
            target_username=target_username,
            login_username=login_username,
            timestamp=timestamp,
            followers=followers,
            followees=followees,
            non_followbacks_count=len(non_followbacks),
            followers_fetch_seconds=followers_fetch_seconds,
            followees_fetch_seconds=followees_fetch_seconds,
            followers_rate=followers_rate,
            followees_rate=followees_rate,
            followers_total_hint=followers_total,
            followees_total_hint=following_total,
        )
        return {
            "timestamp": timestamp,
            "followers_count": followers_total,
            "followees_count": following_total,
            "followers": followers,
            "followees": followees,
            "followers_collected_count": len(followers),
            "followees_collected_count": len(followees),
            "followers_missing_count": max(0, int(followers_total) - len(followers)),
            "followees_missing_count": max(0, int(following_total) - len(followees)),
            "partial_collection": partial_collection,
            "non_followbacks_count": len(non_followbacks),
            "followers_added": len((changes.get("followers") or {}).get("added") or []),
            "followers_removed": len((changes.get("followers") or {}).get("removed") or []),
            "followees_added": len((changes.get("followees") or {}).get("added") or []),
            "followees_removed": len((changes.get("followees") or {}).get("removed") or []),
            "run_id": run_id,
            "followers_fetch_seconds": followers_fetch_seconds,
            "followees_fetch_seconds": followees_fetch_seconds,
            "followers_rate": followers_rate,
            "followees_rate": followees_rate,
        }

    try:
        return _with_browser_context(login_username, login_mode, _runner, timeout_ms=timeout_ms)
    except BrowserTrackerError:
        raise
    except PlaywrightTimeoutError as exc:
        raise BrowserTrackerError("timeout", f"browser timeout during snapshot: {exc}") from exc
    except Exception as exc:
        raise BrowserTrackerError("browser_error", str(exc)) from exc
    try:
        loader = _load_logged_in_instaloader(
            login_username=login_username,
            login_mode=login_mode,
            http_timeout_seconds=float(http_timeout_seconds or 600.0),
            user_agent=user_agent,
        )
        target = Profile.from_username(loader.context, target_username)
        followers_total = int(getattr(target, "followers", 0) or 0)
        following_total = int(getattr(target, "followees", 0) or 0)
        if progress:
            progress("totals", {"followers_total": followers_total, "following_total": following_total})

        if profile_only:
            changes, run_id = write_run_profile_counts(
                db_path=db_path,
                target_username=target_username,
                login_username=login_username,
                timestamp=timestamp,
                followers_count=followers_total,
                followees_count=following_total,
            )
            return {
                "timestamp": timestamp,
                "followers_count": followers_total,
                "followees_count": following_total,
                "followers_collected_count": followers_total,
                "followees_collected_count": following_total,
                "followers": [],
                "followees": [],
                "non_followbacks_count": 0,
                "followers_added": 0,
                "followers_removed": 0,
                "followees_added": 0,
                "followees_removed": 0,
                "changes": changes,
                "run_id": run_id,
                "followers_fetch_seconds": 0,
                "followees_fetch_seconds": 0,
                "followers_rate": None,
                "followees_rate": None,
                "profile_only": True,
            }

        settle = float(initial_fetch_delay_seconds or 0.0)
        if settle > 0:
            settle_low = max(0.0, settle * 0.8)
            settle_high = max(settle_low, settle * 1.2)
            time.sleep(min(random.uniform(settle_low, settle_high), 120.0))

        def _collect(kind: str) -> tuple[list[str], float]:
            if kind == "followers":
                return _collect_usernames_from_iterator(
                    target.get_followers(),
                    phase="followers",
                    progress=progress,
                    delay_min=item_delay_min,
                    delay_max=item_delay_max,
                    pause_every_min=pause_every_min,
                    pause_every_max=pause_every_max,
                    pause_seconds_min=pause_seconds_min,
                    pause_seconds_max=pause_seconds_max,
                )
            return _collect_usernames_from_iterator(
                target.get_followees(),
                phase="following",
                progress=progress,
                delay_min=item_delay_min,
                delay_max=item_delay_max,
                pause_every_min=pause_every_min,
                pause_every_max=pause_every_max,
                pause_seconds_min=pause_seconds_min,
                pause_seconds_max=pause_seconds_max,
            )

        order = str(fetch_order or "followers_first").strip().lower()
        if order not in {"followers_first", "following_first"}:
            order = "followers_first"

        if order == "following_first":
            followees, followees_fetch_seconds = _collect("following")
            followers, followers_fetch_seconds = _collect("followers")
        else:
            followers, followers_fetch_seconds = _collect("followers")
            followees, followees_fetch_seconds = _collect("following")

        non_followbacks = sorted(set(followees) - set(followers))
        followers_rate = round(len(followers) / followers_fetch_seconds, 3) if followers_fetch_seconds else None
        followees_rate = round(len(followees) / followees_fetch_seconds, 3) if followees_fetch_seconds else None

        changes, run_id = write_run_metadata(
            db_path=db_path,
            target_username=target_username,
            login_username=login_username,
            timestamp=timestamp,
            followers=followers,
            followees=followees,
            non_followbacks_count=len(non_followbacks),
            followers_fetch_seconds=followers_fetch_seconds,
            followees_fetch_seconds=followees_fetch_seconds,
            followers_rate=followers_rate,
            followees_rate=followees_rate,
            followers_total_hint=followers_total,
            followees_total_hint=following_total,
        )
        return {
            "timestamp": timestamp,
            "followers_count": followers_total,
            "followees_count": following_total,
            "followers": followers,
            "followees": followees,
            "followers_collected_count": len(followers),
            "followees_collected_count": len(followees),
            "followers_missing_count": max(0, int(followers_total) - len(followers)),
            "followees_missing_count": max(0, int(following_total) - len(followees)),
            "partial_collection": (len(followers) < int(followers_total)) or (len(followees) < int(following_total)),
            "non_followbacks_count": len(non_followbacks),
            "followers_added": len((changes.get("followers") or {}).get("added") or []),
            "followers_removed": len((changes.get("followers") or {}).get("removed") or []),
            "followees_added": len((changes.get("followees") or {}).get("added") or []),
            "followees_removed": len((changes.get("followees") or {}).get("removed") or []),
            "run_id": run_id,
            "followers_fetch_seconds": followers_fetch_seconds,
            "followees_fetch_seconds": followees_fetch_seconds,
            "followers_rate": followers_rate,
            "followees_rate": followees_rate,
        }
    except Exception as exc:
        raise _map_instaloader_exception(exc) from exc
