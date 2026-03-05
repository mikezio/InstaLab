import os
import random
import time
from typing import Callable, Optional

from playwright.sync_api import sync_playwright


class AuthRequiredError(RuntimeError):
    pass


def ensure_auth_state(storage_path: str) -> bool:
    return os.path.exists(storage_path)


DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


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
    with sync_playwright() as p:
        launch_args = {"headless": False}
        if proxy_server:
            launch_args["proxy"] = {
                "server": proxy_server,
                "username": proxy_username or "",
                "password": proxy_password or "",
            }
        browser = p.chromium.launch(**launch_args)
        context = browser.new_context()
        page = context.new_page()
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
            context.storage_state(path=storage_path)
            if has_session:
                page.wait_for_timeout(1200)
                context.storage_state(path=storage_path)
                break
            page.wait_for_timeout(1000)
        context.storage_state(path=storage_path)
        browser.close()


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
