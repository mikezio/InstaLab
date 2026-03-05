import os
import random
import time
from typing import Callable, Iterable, Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
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
        except (TimeoutError, AttributeError):
            # Button not found or not clickable, try next label
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
    except (AttributeError, RuntimeError):
        return False
    return False


def _profile_action_scope(page):
    candidates = [
        page.locator("main header"),
        page.locator("main [role='main'] header"),
        page.locator("header"),
    ]
    for loc in candidates:
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return page


def _normalize_label(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _find_button_by_text(scope, labels):
    labels = {l.lower() for l in labels}
    for locator in (scope.locator("button"), scope.locator('[role="button"]')):
        try:
            for btn in locator.all():
                try:
                    if not btn.is_visible():
                        continue
                    txt = _normalize_label(btn.inner_text())
                    if txt and txt.lower() in labels:
                        return btn
                except Exception:
                    continue
        except Exception:
            continue
    return None


def _find_following_button(scope):
    return _find_button_by_text(scope, ("Following", "Requested"))


def _find_follow_button(scope):
    return _find_button_by_text(scope, ("Follow", "Follow back", "Follow Back"))


def _wait_for_follow_state(scope, should_follow: bool, timeout_ms: int = 8000) -> bool:
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        if should_follow:
            if _find_following_button(scope):
                return True
        else:
            if _find_follow_button(scope):
                return True
        try:
            page.wait_for_timeout(400)
        except Exception:
            time.sleep(0.4)
    return False


def _confirm_unfollow(page) -> None:
    """Click the confirm unfollow button if a dialog is shown."""
    try:
        dialog_btn = page.locator('div[role="dialog"] button:has-text("Unfollow")')
        if dialog_btn.count() > 0 and dialog_btn.first.is_visible():
            dialog_btn.first.click(timeout=4000)
            return
    except Exception:
        pass
    try:
        dialog_btn = page.locator('div[role="dialog"] [role="button"]:has-text("Unfollow")')
        if dialog_btn.count() > 0 and dialog_btn.first.is_visible():
            dialog_btn.first.click(timeout=4000)
            return
    except Exception:
        pass


def unfollow_users(
    usernames: Iterable[str],
    storage_path: str,
    headless: bool = True,
    delay_min: int = 25,
    delay_max: int = 45,
    max_actions: Optional[int] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    log: Optional[Callable[[str], None]] = None,
    progress: Optional[Callable[[dict], None]] = None,
    record: Optional[Callable[[str, str, Optional[str]], None]] = None,
    proxy_server: Optional[str] = None,
    proxy_username: Optional[str] = None,
    proxy_password: Optional[str] = None,
) -> dict:
    if not ensure_auth_state(storage_path):
        raise AuthRequiredError("missing Instagram login storage_state")

    log = log or (lambda *_: None)
    cancel_check = cancel_check or (lambda: False)

    actions = 0
    skipped = 0
    errors = 0
    last_user = None
    cancelled = False
    consecutive_errors = 0
    fatal_error = None

    with sync_playwright() as p:
        launch_args = {
            "headless": headless,
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
            storage_state=storage_path,
            user_agent=DEFAULT_UA,
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1366, "height": 768},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4]});"
        )
        page = context.new_page()
        page.set_default_timeout(15000)
        last_unfollow_error = None
        last_unfollow_error_msg = None

        def _capture_unfollow_error(response):
            nonlocal last_unfollow_error, last_unfollow_error_msg
            try:
                req = response.request
                if req.method != "POST":
                    return
                if "graphql" not in response.url:
                    return
                data = (req.post_data or "").lower()
                if "destroy_friendship" not in data and "unfollow" not in data:
                    return
                body = response.text()
                if "feedback_required" in body or "Try Again Later" in body:
                    last_unfollow_error = body[:300]
                    last_unfollow_error_msg = "feedback_required"
                    return
                if "\"errors\"" in body:
                    last_unfollow_error = body[:300]
                    try:
                        import json as _json  # local import to avoid overhead

                        data = _json.loads(body)
                        err = (data.get("errors") or [{}])[0]
                        last_unfollow_error_msg = str(err.get("message") or "unfollow error")
                    except Exception:
                        last_unfollow_error_msg = "unfollow error"
            except Exception:
                return

        page.on("response", _capture_unfollow_error)
        # Warm-up to confirm auth is still valid.
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        _maybe_accept_cookies(page)
        page.wait_for_timeout(1200)
        if _login_required(page):
            raise AuthRequiredError("login required; storage_state invalid or expired")

        for username in usernames:
            if max_actions is not None and actions >= max_actions:
                break
            if cancel_check():
                log("cancel requested; stopping")
                cancelled = True
                break
            last_user = username
            try:
                last_unfollow_error = None
                last_unfollow_error_msg = None
                page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")
                _maybe_accept_cookies(page)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(600)
                if _login_required(page):
                    raise AuthRequiredError("login required; storage_state invalid or expired")
                scope = _profile_action_scope(page)
                # Try to find Following/Requested button first within profile header scope
                btn = _find_following_button(scope)
                if not btn and _find_follow_button(scope):
                    skipped += 1
                    log(f"skip: {username} (already not following)")
                    if record:
                        record(username, "skipped", "already_not_following")
                    if progress:
                        progress({"processed": actions + skipped + errors, "actions": actions, "skipped": skipped, "errors": errors, "last_user": username})
                    continue
                # If we still don't see a Following button, assume not-following or UI changed.
                if not btn:
                    skipped += 1
                    log(f"skip: {username} (not following, private, or UI changed)")
                    if record:
                        record(username, "skipped", "not_following_or_unavailable")
                    if progress:
                        progress({"processed": actions + skipped + errors, "actions": actions, "skipped": skipped, "errors": errors, "last_user": username})
                    continue
                btn.click()
                try:
                    page.get_by_role("button", name="Unfollow").click(timeout=8000)
                except PlaywrightTimeoutError:
                    # sometimes the confirm dialog is a menu item
                    try:
                        page.get_by_text("Unfollow").click(timeout=4000)
                    except Exception:
                        skipped += 1
                        log(f"skip: {username} (unfollow confirm not found)")
                        if record:
                            record(username, "skipped", "confirm_not_found")
                        if progress:
                            progress({"processed": actions + skipped + errors, "actions": actions, "skipped": skipped, "errors": errors, "last_user": username})
                        continue
                # Some layouts require a second confirm in a dialog.
                _confirm_unfollow(page)
                # Verify unfollow took effect within header scope.
                if not _wait_for_follow_state(scope, should_follow=False, timeout_ms=8000):
                    if last_unfollow_error:
                        errors += 1
                        consecutive_errors += 1
                        msg = last_unfollow_error_msg or "unfollow rejected"
                        log(f"error: {username} ({msg})")
                        if record:
                            record(username, "error", "unfollow_rejected")
                        if msg == "feedback_required":
                            fatal_error = msg
                            log("error: Instagram rate limit (feedback_required)")
                            break
                    else:
                        skipped += 1
                        log(f"skip: {username} (unfollow not confirmed)")
                        if record:
                            record(username, "skipped", "unfollow_not_confirmed")
                    if progress:
                        progress({"processed": actions + skipped + errors, "actions": actions, "skipped": skipped, "errors": errors, "last_user": username})
                    if consecutive_errors >= 3:
                        fatal_error = last_unfollow_error_msg or "unfollow_failed"
                        log("error: stopping after repeated unfollow failures")
                        break
                    continue
                actions += 1
                consecutive_errors = 0
                log(f"unfollowed: {username}")
                if record:
                    record(username, "success", None)
                if progress:
                    progress({"processed": actions + skipped + errors, "actions": actions, "skipped": skipped, "errors": errors, "last_user": username})
            except Exception as exc:  # noqa: BLE001
                errors += 1
                consecutive_errors += 1
                log(f"error: {username} ({exc})")
                if record:
                    record(username, "error", str(exc))
                if progress:
                    progress({"processed": actions + skipped + errors, "actions": actions, "skipped": skipped, "errors": errors, "last_user": username})
                if consecutive_errors >= 3:
                    fatal_error = str(exc)
                    log("error: stopping after repeated unfollow failures")
                    break

            if delay_max > 0:
                wait = random.randint(delay_min, max(delay_min, delay_max))
                time.sleep(wait)

        context.close()
        browser.close()

    return {
        "actions": actions,
        "skipped": skipped,
        "errors": errors,
        "last_user": last_user,
        "cancelled": cancelled,
        "fatal_error": fatal_error,
        "last_error": last_unfollow_error_msg,
    }


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
        # Keep the window open for manual login/challenge and persist as soon as session is valid.
        deadline = time.time() + max_wait_seconds
        while time.time() < deadline:
            try:
                cookies = context.cookies("https://www.instagram.com/")
            except Exception:
                cookies = []
            has_session = any((c.get("name") == "sessionid") and (".instagram.com" in str(c.get("domain") or "") or "instagram.com" in str(c.get("domain") or "")) for c in cookies)
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
    """Open Instagram signup, auto-fill form fields, then wait for manual verification steps."""
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

        try:
            page.goto("https://www.instagram.com/accounts/emailsignup/", wait_until="domcontentloaded")
            _maybe_accept_cookies(page)
            page.wait_for_timeout(800)

            page.locator('input[name="emailOrPhone"]').first.fill(email)
            page.locator('input[name="fullName"]').first.fill(full_name)
            page.locator('input[name="username"]').first.fill(username)
            page.locator('input[name="password"]').first.fill(password)
            log("filled signup form")

            submit_clicked = False
            for label in ("Sign up", "Sign Up"):
                try:
                    btn = page.get_by_role("button", name=label)
                    if btn.count() > 0:
                        btn.first.click(timeout=3000)
                        submit_clicked = True
                        break
                except Exception:
                    continue
            if not submit_clicked:
                try:
                    page.keyboard.press("Enter")
                    submit_clicked = True
                except Exception:
                    pass
            if submit_clicked:
                log("submitted signup form; waiting for verification/account completion")
            else:
                log("could not auto-submit form; continue manually in the open browser")

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
