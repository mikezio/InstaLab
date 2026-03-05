"""Browser-driven Instagram tracker backend using Playwright session state."""

from __future__ import annotations

import os
import random
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from tracker_db import write_run_metadata, write_run_profile_counts


ANONYMOUS_LOGIN_MODES = {"anonymous", "public", "no_login", "no-login", "anon"}
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


class BrowserTrackerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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
    return _storage_path_for_login(login_username).exists()


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


def _api_headers(page, target_username: str, kind: str) -> dict:
    cookies = page.context.cookies("https://www.instagram.com/")
    csrf = ""
    for cookie in cookies:
        if cookie.get("name") == "csrftoken":
            csrf = str(cookie.get("value") or "")
            break
    headers = {
        "x-csrftoken": csrf,
        "x-ig-app-id": "936619743392459",
        "x-requested-with": "XMLHttpRequest",
        "referer": f"https://www.instagram.com/{target_username}/{kind}/",
    }
    try:
        claim = page.evaluate("() => localStorage.getItem('www-claim-v2') || ''")
    except Exception:
        claim = ""
    if claim:
        headers["x-ig-www-claim"] = str(claim)
    return headers


def _collect_usernames_via_api(
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
    next_max_id: str | None = None
    empty_pages = 0
    max_pages = max(60, min(3000, (expected_total // 10) + 80)) if expected_total > 0 else 200

    for _ in range(max_pages):
        query = [("count", "50"), ("search_surface", "follow_list_page")]
        if next_max_id:
            query.append(("max_id", str(next_max_id)))
        url = (
            f"https://www.instagram.com/api/v1/friendships/{target_user_id}/{kind}/?"
            + urlencode(query)
        )
        resp = page.request.get(url, headers=_api_headers(page, target_username, kind), timeout=60000)
        if resp.status in {401, 403}:
            raise BrowserTrackerError("auth_required", "browser session expired; interactive login required")
        if resp.status == 429:
            raise BrowserTrackerError("rate_limited", f"instagram rate limit while fetching {kind}")
        if resp.status >= 400:
            raise BrowserTrackerError("api_error", f"instagram API error {resp.status} while fetching {kind}")

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
                raise BrowserTrackerError("rate_limited", f"instagram returned HTML instead of JSON while fetching {kind}")
            raise BrowserTrackerError("api_error", f"unexpected non-JSON response while fetching {kind}")
        users = payload.get("users") or []
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

        next_max_id = payload.get("next_max_id")
        if not users:
            empty_pages += 1
        else:
            empty_pages = 0

        if empty_pages >= 3:
            break
        if not next_max_id:
            break

        wait_seconds = random.uniform(max(0.0, delay_min), max(delay_min, delay_max))
        page.wait_for_timeout(int(wait_seconds * 1000))

    return sorted(usernames)


def _open_relation_dialog(page, kind: str) -> None:
    selector_variants = [
        f"a[href$='/{kind}/']",
        f"a[href*='/{kind}/']",
    ]
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
                    clicked = True
                    break
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
                        break
        except Exception:
            continue
    if not clicked:
        raise BrowserTrackerError("ui_error", f"could not open {kind} dialog")
    page.wait_for_selector("div[role='dialog']", timeout=15000)
    page.wait_for_timeout(500)


def _collect_usernames_from_open_dialog(
    page,
    *,
    expected_total: int,
    progress_phase: str,
    progress=None,
    delay_min: float = 0.2,
    delay_max: float = 0.7,
) -> list[str]:
    usernames: set[str] = set()
    stable_passes = 0
    max_loops = max(80, min(1200, (expected_total * 2) if expected_total > 0 else 260))
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
    anonymous_mode = _is_anonymous_login_mode(login_mode)
    storage_exists = storage_path.exists()
    if not anonymous_mode and not storage_exists:
        raise BrowserTrackerError(
            "auth_required",
            (
                "missing browser session state; run /api/unfollow/init to sign in first "
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
        }
        if browser_proxy:
            launch_args["proxy"] = browser_proxy
        browser = p.chromium.launch(**launch_args)
        context_kwargs = {
            "user_agent": user_agent,
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "viewport": {"width": 1366, "height": 768},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
        }
        if storage_exists:
            context_kwargs["storage_state"] = str(storage_path)
        context = browser.new_context(**context_kwargs)
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4]});"
        )
        page = context.new_page()
        effective_timeout = int(timeout_ms) if timeout_ms and timeout_ms > 0 else 60000
        page.set_default_timeout(effective_timeout)
        page.set_default_navigation_timeout(effective_timeout)
        try:
            return fn(page)
        finally:
            context.close()
            browser.close()


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
    del two_factor_code, challenge_code, totp_seed, device_settings_json, user_agent
    del delay_min, delay_max
    timeout_ms = max(15000, int(float(http_timeout_seconds or 120.0) * 1000))
    if progress:
        progress("bootstrap", 0)

    def _runner(page):
        if progress:
            progress("starting", 0)
            progress("profile_page", 0)
        _open_profile_page(page, target_username, timeout_ms=timeout_ms)
        if _login_required(page) and not _is_anonymous_login_mode(login_mode):
            raise BrowserTrackerError("auth_required", "browser session expired; interactive login required")
        followers_count, followees_count = _extract_counts(page)
        return {
            "timestamp": _now_snapshot_ts(),
            "followers_count": int(followers_count),
            "followees_count": int(followees_count),
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
):
    del login_password, cookie_file, request_sleep_seconds
    del pause_every_min, pause_every_max, pause_seconds_min, pause_seconds_max
    del trace_enabled, trace_path, two_factor_code, challenge_code, totp_seed, device_settings_json, user_agent
    timeout_ms = max(15000, int(float(http_timeout_seconds or 600.0) * 1000))
    if progress:
        progress("bootstrap", 0)

    timestamp = _now_snapshot_ts()

    def _runner(page):
        _open_profile_page(page, target_username, timeout_ms=timeout_ms)
        if _login_required(page) and not _is_anonymous_login_mode(login_mode):
            raise BrowserTrackerError("auth_required", "browser session expired; interactive login required")
        followers_total, following_total = _extract_counts(page)
        if progress:
            progress("totals", {"followers_total": followers_total, "following_total": following_total})

        if profile_only:
            run_id = write_run_profile_counts(
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
                "run_id": run_id,
                "followers_fetch_seconds": 0,
                "followees_fetch_seconds": 0,
                "followers_rate": None,
                "followees_rate": None,
            }

        if initial_fetch_delay_seconds > 0:
            page.wait_for_timeout(int(initial_fetch_delay_seconds * 1000))

        target_user_id = _extract_target_user_id(page)
        if not target_user_id:
            raise BrowserTrackerError("parse_error", "could not resolve target user id from profile page")

        def _collect(kind: str, total_hint: int) -> tuple[list[str], float]:
            phase_name = "followers" if kind == "followers" else "following"
            started = datetime.now().timestamp()
            if progress:
                progress(phase_name, 0)
            names = _collect_usernames_via_api(
                page,
                target_username=target_username,
                target_user_id=target_user_id,
                kind=kind,
                expected_total=max(0, int(total_hint or 0)),
                progress_phase=phase_name,
                progress=progress,
                delay_min=item_delay_min,
                delay_max=item_delay_max,
            )
            elapsed = max(0.0, datetime.now().timestamp() - started)
            return names, elapsed

        order = str(fetch_order or "followers_first").strip().lower()
        if order not in {"followers_first", "following_first"}:
            order = "followers_first"

        if order == "following_first":
            followees, followees_fetch_seconds = _collect("following", following_total)
            followers, followers_fetch_seconds = _collect("followers", followers_total)
        else:
            followers, followers_fetch_seconds = _collect("followers", followers_total)
            followees, followees_fetch_seconds = _collect("following", following_total)

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

    try:
        return _with_browser_context(login_username, login_mode, _runner, timeout_ms=timeout_ms)
    except BrowserTrackerError:
        raise
    except PlaywrightTimeoutError as exc:
        raise BrowserTrackerError("timeout", f"browser timeout during snapshot: {exc}") from exc
    except Exception as exc:
        raise BrowserTrackerError("browser_error", str(exc)) from exc
