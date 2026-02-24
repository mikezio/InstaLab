"""Browser-driven Instagram tracker backend using Playwright session state."""

from __future__ import annotations

import os
import random
import re
from datetime import datetime
from pathlib import Path

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


def _open_profile_page(page, target_username: str) -> None:
    page.goto(f"https://www.instagram.com/{target_username}/", wait_until="domcontentloaded")
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
        raise BrowserTrackerError("parse_error", "could not parse followers/following counts from profile page")
    return int(followers), int(following)


def _open_relation_dialog(page, kind: str) -> None:
    selector_variants = [
        f"a[href$='/{kind}/']",
        f"a[href*='/{kind}/']",
    ]
    clicked = False
    for selector in selector_variants:
        try:
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=7000)
                clicked = True
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


def _with_browser_context(login_username: str, login_mode: str, fn):
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
    browser_proxy = {"server": launch_proxy} if launch_proxy else None
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
        page.set_default_timeout(20000)
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
    del login_password, cookie_file, http_timeout_seconds, request_sleep_seconds
    del two_factor_code, challenge_code, totp_seed, device_settings_json, user_agent
    del delay_min, delay_max

    def _runner(page):
        _open_profile_page(page, target_username)
        if _login_required(page) and not _is_anonymous_login_mode(login_mode):
            raise BrowserTrackerError("auth_required", "browser session expired; interactive login required")
        followers_count, followees_count = _extract_counts(page)
        return {
            "timestamp": _now_snapshot_ts(),
            "followers_count": int(followers_count),
            "followees_count": int(followees_count),
        }

    try:
        return _with_browser_context(login_username, login_mode, _runner)
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
    del login_password, cookie_file, http_timeout_seconds, request_sleep_seconds
    del pause_every_min, pause_every_max, pause_seconds_min, pause_seconds_max
    del trace_enabled, trace_path, two_factor_code, challenge_code, totp_seed, device_settings_json, user_agent

    timestamp = _now_snapshot_ts()

    def _runner(page):
        _open_profile_page(page, target_username)
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

        def _collect(kind: str, total_hint: int) -> tuple[list[str], float]:
            phase_name = "followers" if kind == "followers" else "following"
            started = datetime.now().timestamp()
            _open_relation_dialog(page, kind)
            names = _collect_usernames_from_open_dialog(
                page,
                expected_total=total_hint,
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

        run_id, changes = write_run_metadata(
            db_path=db_path,
            target_username=target_username,
            login_username=login_username,
            timestamp=timestamp,
            followers=followers,
            followees=followees,
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
            "non_followbacks_count": len(non_followbacks),
            "followers_added": changes["followers_added"],
            "followers_removed": changes["followers_removed"],
            "followees_added": changes["followees_added"],
            "followees_removed": changes["followees_removed"],
            "run_id": run_id,
            "followers_fetch_seconds": followers_fetch_seconds,
            "followees_fetch_seconds": followees_fetch_seconds,
            "followers_rate": followers_rate,
            "followees_rate": followees_rate,
        }

    try:
        return _with_browser_context(login_username, login_mode, _runner)
    except BrowserTrackerError:
        raise
    except PlaywrightTimeoutError as exc:
        raise BrowserTrackerError("timeout", f"browser timeout during snapshot: {exc}") from exc
    except Exception as exc:
        raise BrowserTrackerError("browser_error", str(exc)) from exc
