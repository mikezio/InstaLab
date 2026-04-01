import os
import random
import time
import json
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import sync_playwright


class AuthRequiredError(RuntimeError):
    pass


def _instagram_cookie_names(payload: dict) -> set[str]:
    cookies = payload.get("cookies") if isinstance(payload, dict) else None
    if not isinstance(cookies, list):
        return set()
    names: set[str] = set()
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain") or "")
        if "instagram.com" not in domain and not domain.endswith(".instagram.com"):
            continue
        name = str(cookie.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def load_storage_state_payload(storage_path: str) -> dict | None:
    try:
        with open(storage_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def persist_storage_state_if_authenticated(context, storage_path: str, *, expected_username: str | None = None) -> bool:
    tmp_path = f"{storage_path}.tmp"
    try:
        context.storage_state(path=tmp_path)
        if not storage_state_has_session(tmp_path, expected_username=expected_username):
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass
            return False
        os.replace(tmp_path, storage_path)
        return True
    except Exception:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass
        return False


def storage_state_has_session(storage_path: str, expected_username: str | None = None) -> bool:
    payload = load_storage_state_payload(storage_path)
    if not payload:
        return False

    cookie_names = _instagram_cookie_names(payload)
    if not cookie_names:
        return False

    required_cookie_names = {"sessionid", "csrftoken", "ds_user_id"}
    if not required_cookie_names.issubset(cookie_names):
        return False

    normalized_expected = str(expected_username or "").strip().lstrip("@").lower()
    if not normalized_expected:
        return True

    origins = payload.get("origins") if isinstance(payload, dict) else None
    if not isinstance(origins, list):
        return True
    saw_instagram_origin = False
    saw_one_tap_marker = False
    for origin_entry in origins:
        if not isinstance(origin_entry, dict):
            continue
        if str(origin_entry.get("origin") or "") != "https://www.instagram.com":
            continue
        saw_instagram_origin = True
        local_storage = origin_entry.get("localStorage")
        if not isinstance(local_storage, list):
            continue
        for item in local_storage:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "") != "one_tap_storage_version":
                continue
            saw_one_tap_marker = True
            try:
                one_tap = json.loads(str(item.get("value") or "{}"))
            except Exception:
                continue
            if normalized_expected in {str(key).strip().lower() for key in one_tap.keys()}:
                return True
            if isinstance(one_tap, dict):
                for value in one_tap.values():
                    if not isinstance(value, dict):
                        continue
                    nested_username = str(value.get("username") or "").strip().lower()
                    if nested_username == normalized_expected:
                        return True
    return not (saw_instagram_origin and saw_one_tap_marker)


def ensure_auth_state(storage_path: str) -> bool:
    return storage_state_has_session(storage_path)


DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)


def _safe_username(value: str) -> str:
    raw = str(value or "").strip()
    if raw:
        return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)
    return "login"


def browser_profile_dir_for_storage_path(storage_path: str, login_username: str | None = None) -> str:
    configured = str(os.getenv("INSTALAB_BROWSER_PROFILE_DIR", "") or "").strip()
    if configured:
        root = configured
    else:
        root = str((os.path.dirname(storage_path) and os.path.join(os.path.dirname(storage_path), "profiles")) or "")
    leaf = _safe_username(login_username or os.path.splitext(os.path.basename(storage_path))[0])
    return os.path.join(root, leaf)


def browser_profile_dir_for_login(login_username: str) -> str:
    configured = str(os.getenv("INSTALAB_BROWSER_PROFILE_DIR", "") or "").strip()
    if configured:
        return os.path.join(configured, _safe_username(login_username))
    storage_dir = str(os.getenv("INSTALAB_BROWSER_STORAGE_DIR", "") or "").strip()
    if storage_dir:
        return os.path.join(storage_dir, "profiles", _safe_username(login_username))
    return os.path.join(os.path.dirname(__file__), ".playwright_profiles", _safe_username(login_username))


def browser_executable_path() -> str | None:
    configured = str(os.getenv("INSTALAB_BROWSER_EXECUTABLE_PATH", "") or "").strip()
    if configured:
        return configured
    for candidate in ("/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if os.path.exists(candidate):
            return candidate
    return None


def browser_profile_has_state(profile_dir: str) -> bool:
    try:
        root = Path(profile_dir)
    except Exception:
        return False
    if not root.exists() or not root.is_dir():
        return False
    for child in root.iterdir():
        name = child.name
        if name in {"SingletonCookie", "SingletonLock", "SingletonSocket"}:
            continue
        return True
    return False


def seed_context_from_storage_state(context, storage_path: str) -> bool:
    payload = load_storage_state_payload(storage_path)
    if not payload:
        return False
    cookies = payload.get("cookies")
    if isinstance(cookies, list) and cookies:
        try:
            context.add_cookies(cookies)
        except Exception:
            pass

    seeded_local_storage = False
    for origin_entry in payload.get("origins") or []:
        if not isinstance(origin_entry, dict):
            continue
        origin = str(origin_entry.get("origin") or "").strip()
        local_storage = origin_entry.get("localStorage")
        if not origin or not isinstance(local_storage, list) or not local_storage:
            continue
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(origin, wait_until="domcontentloaded", timeout=30000)
            page.evaluate(
                """items => {
                    for (const item of items) {
                      if (!item || typeof item.name !== "string") continue;
                      localStorage.setItem(item.name, String(item.value ?? ""));
                    }
                }""",
                local_storage,
            )
            seeded_local_storage = True
        except Exception:
            continue
    return bool(cookies) or seeded_local_storage


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
        if "accounts/login" in page.url:
            return True
    except AttributeError:
        pass
    try:
        if page.locator('input[name="username"]').count() > 0:
            return True
    except Exception:
        return False
    return False


def init_login(
    storage_path: str,
    proxy_server: Optional[str] = None,
    proxy_username: Optional[str] = None,
    proxy_password: Optional[str] = None,
    max_wait_seconds: int = 300,
):
    os.makedirs(os.path.dirname(storage_path), exist_ok=True)
    max_wait_seconds = max(30, min(int(max_wait_seconds or 300), 900))
    profile_dir = browser_profile_dir_for_storage_path(storage_path)
    os.makedirs(profile_dir, exist_ok=True)
    with sync_playwright() as p:
        launch_args = {
            "headless": False,
            "args": ["--disable-blink-features=AutomationControlled"],
            "user_agent": DEFAULT_UA,
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
        if proxy_server:
            launch_args["proxy"] = {
                "server": proxy_server,
                "username": proxy_username or "",
                "password": proxy_password or "",
            }
        context = p.chromium.launch_persistent_context(profile_dir, **launch_args)
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4]});"
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        _maybe_accept_cookies(page)
        if _login_required(page):
            page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
        deadline = time.time() + max_wait_seconds
        while time.time() < deadline:
            try:
                cookies = context.cookies("https://www.instagram.com/")
            except Exception:
                cookies = []
            has_session = any(
                (c.get("name") == "sessionid")
                and (".instagram.com" in str(c.get("domain") or "") or "instagram.com" in str(c.get("domain") or ""))
                for c in cookies
            )
            persist_storage_state_if_authenticated(context, storage_path)
            if has_session and not _login_required(page):
                page.wait_for_timeout(1200)
                persist_storage_state_if_authenticated(context, storage_path)
                break
            page.wait_for_timeout(1000)
        persist_storage_state_if_authenticated(context, storage_path)
        context.close()


def create_account_guided(
    *,
    email: str,
    full_name: str,
    username: str,
    password: str,
    max_wait_seconds: int = 300,
    cancel_check: Optional[Callable[[], bool]] = None,
    log: Optional[Callable[[str], None]] = None,
    proxy_server: Optional[str] = None,
    proxy_username: Optional[str] = None,
    proxy_password: Optional[str] = None,
) -> dict:
    log = log or (lambda *_: None)
    cancel_check = cancel_check or (lambda: False)
    max_wait_seconds = max(60, min(int(max_wait_seconds or 300), 900))
    started = time.time()
    last_url = ""

    with sync_playwright() as p:
        launch_args = {
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if proxy_server:
            launch_args["proxy"] = {
                "server": proxy_server,
                "username": proxy_username or "",
                "password": proxy_password or "",
            }
        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(
            user_agent=DEFAULT_UA,
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1366, "height": 768},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
        )
        page = context.new_page()
        page.set_default_timeout(20000)

        def _visible_input(selectors: list[str]):
            for selector in selectors:
                try:
                    node = page.locator(selector).first
                    if node.count() > 0 and node.is_visible():
                        return node
                except Exception:
                    continue
            return None

        def _fill_required(field_name: str, selectors: list[str], value: str) -> bool:
            node = _visible_input(selectors)
            if not node:
                log(f"missing required field: {field_name}")
                return False
            try:
                node.click(timeout=3000)
                node.fill("")
                node.type(value, delay=random.randint(25, 65))
                return True
            except Exception as exc:  # noqa: BLE001
                log(f"failed to fill {field_name}: {exc}")
                return False

        def _click_submit() -> bool:
            for label in ("Sign up", "Sign Up", "Next"):
                try:
                    btn = page.get_by_role("button", name=label)
                    if btn.count() > 0 and btn.first.is_visible() and btn.first.is_enabled():
                        btn.first.click(timeout=3000)
                        return True
                except Exception:
                    continue
            for selector in ('button[type="submit"]', "form button"):
                try:
                    btn = page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible() and btn.is_enabled():
                        btn.click(timeout=3000)
                        return True
                except Exception:
                    continue
            return False

        try:
            page.goto("https://www.instagram.com/accounts/emailsignup/", wait_until="domcontentloaded")
            _maybe_accept_cookies(page)
            page.wait_for_timeout(800)
            required_ok = [
                _fill_required("email_or_phone", ['input[name="emailOrPhone"]', 'input[name="email"]'], email),
                _fill_required("full_name", ['input[name="fullName"]'], full_name),
                _fill_required("username", ['input[name="username"]'], username),
                _fill_required("password", ['input[name="password"]'], password),
            ]
            if not all(required_ok):
                return {
                    "completed": False,
                    "cancelled": False,
                    "last_url": page.url or "",
                    "reason": "missing_required_signup_fields",
                }
            log("filled signup form")

            if _click_submit():
                log("submitted signup form; waiting for verification/account completion")
            else:
                log("could not auto-submit form; no matching submit button found")
                return {
                    "completed": False,
                    "cancelled": False,
                    "last_url": page.url or "",
                    "reason": "signup_submit_not_found",
                }

            detected_login = False
            while (time.time() - started) < max_wait_seconds:
                if cancel_check():
                    return {
                        "completed": False,
                        "cancelled": True,
                        "last_url": last_url,
                        "reason": "cancelled",
                    }
                try:
                    last_url = page.url or ""
                except Exception:
                    last_url = ""
                if not _login_required(page) and "emailsignup" not in last_url:
                    detected_login = True
                    break
                page.wait_for_timeout(1000)

            return {
                "completed": bool(detected_login),
                "cancelled": False,
                "last_url": last_url,
                "reason": "completed" if detected_login else "timeout",
            }
        finally:
            context.close()
            browser.close()
