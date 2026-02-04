import os
import random
import re
import shutil
import time
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from proxy_utils import load_proxy_from_env

from instaloader_tracker import write_run_metadata, write_run_profile_counts


BASE_URL = "https://www.instagram.com"
PROFILE_URL = f"{BASE_URL}/{{username}}/"
LOGIN_URL = f"{BASE_URL}/accounts/login/"

USERNAME_BLACKLIST = {
    "accounts",
    "about",
    "developer",
    "explore",
    "p",
    "reel",
    "reels",
    "stories",
    "tv",
    "direct",
    "privacy",
    "policy",
    "press",
    "login",
    "challenge",
    "graphql",
}


def has_session(_login_username):
    return False


def _parse_count(value: str | None):
    if not value:
        return None
    raw = value.strip().lower().replace(",", "")
    match = re.match(r"^([0-9]*\.?[0-9]+)([kmb]?)$", raw)
    if not match:
        return None
    num = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    elif suffix == "b":
        num *= 1_000_000_000
    return int(num)


def _parse_counts_from_desc(desc: str | None):
    if not desc:
        return None, None
    followers_match = re.search(r"([0-9.,]+[kmbKMB]?)\s+Followers", desc)
    following_match = re.search(r"([0-9.,]+[kmbKMB]?)\s+Following", desc)
    followers = _parse_count(followers_match.group(1)) if followers_match else None
    following = _parse_count(following_match.group(1)) if following_match else None
    return followers, following


def _username_from_href(href: str | None):
    if not href:
        return None
    try:
        path = urlparse(href).path or ""
    except Exception:
        return None
    parts = [p for p in path.split("/") if p]
    if len(parts) != 1:
        return None
    username = parts[0]
    if not re.match(r"^[A-Za-z0-9._]{1,30}$", username):
        return None
    # Skip numeric-only tokens (often IDs or non-user links).
    if username.isdigit():
        return None
    if username.lower() in USERNAME_BLACKLIST:
        return None
    return username


def _username_from_text(text: str | None):
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:2]:
        token = line.lstrip("@").strip()
        if not token:
            continue
        if not re.match(r"^[A-Za-z0-9._]{1,30}$", token):
            continue
        if token.isdigit():
            continue
        if token.lower() in USERNAME_BLACKLIST:
            continue
        return token
    return None


def _load_netscape_cookies(path: str):
    cookies = []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\\t")
            if len(parts) < 7:
                continue
            domain, _, cookie_path, secure, expires, name, value = parts[:7]
            if domain.startswith("#HttpOnly_"):
                domain = domain[len("#HttpOnly_") :]
            cookie = {
                "name": name,
                "value": value,
                "path": cookie_path or "/",
                "secure": secure.lower() == "true",
                "domain": domain,
            }
            if expires.isdigit():
                exp = int(expires)
                if exp > 0:
                    cookie["expiry"] = exp
            cookies.append(cookie)
    return cookies


def _apply_cookies(driver, cookies):
    driver.get(BASE_URL + "/")
    host = urlparse(driver.current_url).hostname or ""
    for cookie in cookies:
        cookie = cookie.copy()
        domain = cookie.get("domain") or ""
        if domain and not domain.startswith(".") and host and host.endswith(domain):
            # Preserve subdomain coverage when cookie domain is root.
            cookie["domain"] = f".{domain}"
        elif domain and host and domain not in host:
            # Fall back to current host to avoid invalid domain errors.
            cookie["domain"] = host
        try:
            driver.add_cookie(cookie)
        except WebDriverException:
            continue
    driver.get(BASE_URL + "/")


def _is_logged_in(driver):
    url = (driver.current_url or "").lower()
    if "login" in url:
        return False
    if "challenge" in url or "checkpoint" in url:
        return False
    try:
        page = driver.page_source or ""
    except Exception:
        page = ""
    if "/accounts/login" in page or "/accounts/emailsignup" in page:
        return False
    if "Log in" in page and "Sign up" in page:
        return False
    if driver.find_elements(By.CSS_SELECTOR, "input[name='username']"):
        return False
    return True


def _login(driver, username, password, two_factor_code=None):
    driver.get(LOGIN_URL)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.NAME, "username")))
    user_input = driver.find_element(By.NAME, "username")
    pass_input = driver.find_element(By.NAME, "password")
    user_input.clear()
    user_input.send_keys(username)
    pass_input.clear()
    pass_input.send_keys(password)
    pass_input.send_keys(Keys.ENTER)

    try:
        WebDriverWait(driver, 10).until(lambda d: _is_logged_in(d) or "challenge" in d.current_url)
    except TimeoutException:
        pass

    if _is_logged_in(driver):
        return

    if "challenge" in driver.current_url:
        if not two_factor_code:
            raise RuntimeError("2FA required; provide RUN_2FA_CODE and retry.")
        try:
            code_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.NAME, "verificationCode"))
            )
        except TimeoutException:
            code_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.NAME, "security_code"))
            )
        code_input.clear()
        code_input.send_keys(two_factor_code)
        code_input.send_keys(Keys.ENTER)
        WebDriverWait(driver, 15).until(lambda d: _is_logged_in(d))
        return

    if not _is_logged_in(driver):
        raise RuntimeError("login failed; check credentials or complete verification")


def _ensure_logged_in(driver, login_username, login_password, cookie_file=None, two_factor_code=None):
    if cookie_file and os.path.exists(cookie_file):
        cookies = _load_netscape_cookies(cookie_file)
        if cookies:
            _apply_cookies(driver, cookies)
            try:
                driver.get(BASE_URL + "/accounts/edit/")
                WebDriverWait(driver, 10).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )
            except Exception:
                pass
    if _is_logged_in(driver):
        return
    if "challenge" in (driver.current_url or "").lower():
        raise RuntimeError("login challenge detected; refresh cookies with a real browser session")
    if not login_password:
        raise RuntimeError("missing RUN_LOGIN_PASSWORD (cookies not authenticated)")
    _login(driver, login_username, login_password, two_factor_code=two_factor_code)


def _find_scroll_container(driver, dialog):
    return driver.execute_script(
        """
        const dialog = arguments[0];
        if (!dialog) return null;
        const candidates = dialog.querySelectorAll('*');
        let best = null;
        let bestScroll = 0;
        for (const el of candidates) {
            const sh = el.scrollHeight || 0;
            const ch = el.clientHeight || 0;
            if (ch > 0 && sh > ch + 20) {
                if (sh > bestScroll) {
                    best = el;
                    bestScroll = sh;
                }
            }
        }
        return best;
        """,
        dialog,
    )


def _find_list_trigger(driver, kind):
    kind = (kind or "").strip().lower()
    if not kind:
        return None
    xpath = (
        "//*[contains(translate(normalize-space(.),"
        " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),"
        f" '{kind}')]"
    )
    for el in driver.find_elements(By.XPATH, xpath):
        try:
            trigger = el.find_element(
                By.XPATH,
                "./ancestor-or-self::a|./ancestor-or-self::button|./ancestor-or-self::div[@role='button']",
            )
            if trigger and trigger.is_displayed():
                return trigger
        except Exception:
            pass
        try:
            if el.is_displayed():
                return el
        except Exception:
            continue
    return None


def _open_list(driver, kind):
    try:
        link = WebDriverWait(driver, 12).until(
            EC.element_to_be_clickable((By.XPATH, f"//a[contains(@href,'/{kind}/')]"))
        )
        link.click()
    except TimeoutException:
        trigger = WebDriverWait(driver, 12).until(lambda d: _find_list_trigger(d, kind))
        if not trigger:
            _dump_debug(driver, f"list_{kind}_trigger_missing")
            raise
        driver.execute_script("arguments[0].click();", trigger)
    except Exception:
        _dump_debug(driver, f"list_{kind}_click_failed")
        raise
    dialog = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='dialog'], div[aria-modal='true']"))
    )
    return dialog


def _dump_debug(driver, label):
    base = Path("/tmp")
    try:
        (base / f"instalab_{label}.png").write_bytes(driver.get_screenshot_as_png())
    except Exception:
        pass
    try:
        (base / f"instalab_{label}.html").write_text(driver.page_source, encoding="utf-8")
    except Exception:
        pass


def _collect_usernames(
    driver,
    kind,
    progress=None,
    item_delay_min=0.0,
    item_delay_max=0.0,
    cancel_check=None,
    expected_min=None,
):
    dialog = _open_list(driver, kind)
    try:
        if dialog.find_elements(By.CSS_SELECTOR, "input[name='username'], input[name='password']"):
            _dump_debug(driver, f"list_{kind}_login_prompt")
            raise RuntimeError("login required; refresh cookies with a real browser session")
        dialog_text = dialog.text or ""
        if "Log in" in dialog_text and "Sign up" in dialog_text:
            _dump_debug(driver, f"list_{kind}_login_prompt")
            raise RuntimeError("login required; refresh cookies with a real browser session")
    except RuntimeError:
        raise
    except Exception:
        pass
    scroll_box = _find_scroll_container(driver, dialog) or dialog
    seen = set()
    idle_rounds = 0
    last_count = -1
    delay_min = max(0.0, float(item_delay_min or 0))
    delay_max = max(0.0, float(item_delay_max or 0))
    if delay_max and delay_min > delay_max:
        delay_min, delay_max = delay_max, delay_min

    while idle_rounds < 6:
        if cancel_check and cancel_check():
            raise RuntimeError("cancelled")
        anchors = dialog.find_elements(By.CSS_SELECTOR, "a")
        for anchor in anchors:
            username = _username_from_href(anchor.get_attribute("href"))
            if not username:
                username = _username_from_text(anchor.text)
            if not username:
                username = _username_from_text(anchor.get_attribute("title"))
            if not username:
                username = _username_from_text(anchor.get_attribute("aria-label"))
            if username:
                seen.add(username)
        items = dialog.find_elements(By.CSS_SELECTOR, "li")
        for item in items:
            username = _username_from_text(item.text)
            if username:
                seen.add(username)
        if progress:
            try:
                progress(len(seen))
            except Exception:
                pass
        if len(seen) == 0 or len(seen) == last_count:
            idle_rounds += 1
        else:
            idle_rounds = 0
        last_count = len(seen)
        driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", scroll_box)
        wait = random.uniform(delay_min, max(delay_min, delay_max)) if (delay_min or delay_max) else 0.4
        time.sleep(wait)

    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    except Exception:
        pass
    if not seen:
        _dump_debug(driver, f"list_{kind}_empty")
        if expected_min and expected_min > 0:
            raise RuntimeError(
                f"no {kind} collected despite profile count {expected_min}; likely not logged in"
            )
    return sorted(seen)


def _get_counts(driver):
    try:
        meta = driver.find_element(By.CSS_SELECTOR, "meta[property='og:description']")
        desc = meta.get_attribute("content")
    except Exception:
        desc = None
    followers, following = _parse_counts_from_desc(desc)
    if followers is not None and following is not None:
        return followers, following

    def _try_count(selector):
        try:
            el = driver.find_element(By.XPATH, selector)
        except Exception:
            return None
        title = el.get_attribute("title") or ""
        return _parse_count(title) or _parse_count(el.text)

    followers = _try_count("//a[contains(@href,'/followers')]/span")
    following = _try_count("//a[contains(@href,'/following')]/span")
    return followers, following


@contextmanager
def _driver(request_timeout=60.0):
    headless = os.getenv("SELENIUM_HEADLESS", "1").lower() not in {"0", "false", "no"}
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1200,900")
    proxy = load_proxy_from_env()
    if proxy:
        options.add_argument(f"--proxy-server={proxy['url']}")
    
    # Track temp directory for cleanup if we create one
    temp_home = None
    temp_xdg = None
    
    if not headless:
        selenium_home = os.getenv("SELENIUM_HOME")
        if selenium_home:
            home_dir = Path(selenium_home)
        else:
            # Create temp directory and track it for cleanup
            temp_home = tempfile.mkdtemp(prefix="instalab-chrome-")
            home_dir = Path(temp_home)
        home_dir.mkdir(parents=True, exist_ok=True)
        os.environ["HOME"] = str(home_dir)
        
        xdg_dir_env = os.getenv("XDG_RUNTIME_DIR")
        if xdg_dir_env:
            xdg_runtime = Path(xdg_dir_env)
        else:
            # Create temp directory and track it for cleanup
            temp_xdg = tempfile.mkdtemp(prefix="instalab-xdg-")
            xdg_runtime = Path(temp_xdg)
        xdg_runtime.mkdir(parents=True, exist_ok=True)
        try:
            xdg_runtime.chmod(0o700)
        except Exception:
            pass
        os.environ["XDG_RUNTIME_DIR"] = str(xdg_runtime)
        options.add_argument(f"--user-data-dir={home_dir / 'profile'}")
        options.add_argument("--remote-debugging-pipe")
    binary = os.getenv("CHROME_BIN") or os.getenv("CHROMIUM_BIN")
    if not binary:
        for candidate in ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
            if os.path.exists(candidate):
                binary = candidate
                break
    if binary:
        options.binary_location = binary
    service_path = os.getenv("CHROMEDRIVER_BIN")
    if not service_path:
        for candidate in ("/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver", "/usr/lib/chromium-browser/chromedriver"):
            if os.path.exists(candidate):
                service_path = candidate
                break
    service_path = service_path or "chromedriver"
    service = webdriver.chrome.service.Service(service_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(request_timeout)
    driver.implicitly_wait(5)
    try:
        yield driver
    finally:
        driver.quit()
        # Clean up temporary directories if we created them
        if temp_home:
            try:
                shutil.rmtree(temp_home, ignore_errors=True)
            except Exception:
                pass
        if temp_xdg:
            try:
                shutil.rmtree(temp_xdg, ignore_errors=True)
            except Exception:
                pass


def fetch_counts(
    *,
    login_username,
    login_password,
    target_username,
    cookie_file=None,
    request_timeout=120.0,
):
    tz = ZoneInfo("America/New_York")
    with _driver(request_timeout=request_timeout) as driver:
        _ensure_logged_in(
            driver,
            login_username,
            login_password,
            cookie_file=cookie_file,
            two_factor_code=os.getenv("RUN_2FA_CODE") or os.getenv("INSTALAB_2FA_CODE"),
        )
        driver.get(PROFILE_URL.format(username=target_username))
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, "meta[property='og:description']")
                or d.find_elements(By.XPATH, "//a[contains(@href,'/followers')]/span")
            )
        except Exception:
            pass
        if not _is_logged_in(driver):
            raise RuntimeError("login challenge detected after profile load; refresh cookies")
        followers, following = _get_counts(driver)
        timestamp = datetime.now(tz).strftime("%Y-%m-%d_%H-%M-%S")
        if followers is None or following is None:
            raise RuntimeError("failed to read profile counts")
        return {
            "timestamp": timestamp,
            "followers_count": int(followers),
            "followees_count": int(following),
        }


def snapshot_profile(
    *,
    login_username,
    login_password,
    target_username,
    cookie_file=None,
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
):
    _ = (
        login_mode,
        pause_every_min,
        pause_every_max,
        pause_seconds_min,
        pause_seconds_max,
        trace_enabled,
        trace_path,
    )
    tz = ZoneInfo("America/New_York")
    with _driver(request_timeout=request_timeout) as driver:
        _ensure_logged_in(
            driver,
            login_username,
            login_password,
            cookie_file=cookie_file,
            two_factor_code=os.getenv("RUN_2FA_CODE") or os.getenv("INSTALAB_2FA_CODE"),
        )
        driver.get(PROFILE_URL.format(username=target_username))
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, "meta[property='og:description']")
                or d.find_elements(By.XPATH, "//a[contains(@href,'/followers')]/span")
            )
        except Exception:
            pass
        if not _is_logged_in(driver):
            raise RuntimeError("login challenge detected after profile load; refresh cookies")
        followers_total, following_total = _get_counts(driver)
        if progress:
            try:
                progress("totals", {"followers_total": followers_total, "following_total": following_total})
            except Exception:
                pass

        if profile_only:
            timestamp = datetime.now(tz).strftime("%Y-%m-%d_%H-%M-%S")
            changes = None
            run_id = None
            if db_path:
                changes, run_id = write_run_profile_counts(
                    db_path=db_path,
                    login_username=login_username,
                    target_username=target_username,
                    timestamp=timestamp,
                    followers_count=int(followers_total or 0),
                    followees_count=int(following_total or 0),
                )
            return {
                "timestamp": timestamp,
                "followers_count": int(followers_total or 0),
                "followees_count": int(following_total or 0),
                "changes": changes,
                "run_id": run_id,
                "non_followbacks": [],
                "followers_fetch_seconds": 0,
                "followees_fetch_seconds": 0,
                "followers_rate": None,
                "followees_rate": None,
            }

        if cancel_check and cancel_check():
            raise RuntimeError("cancelled")

        t0 = time.time()
        if progress:
            try:
                progress("followers", 0)
            except Exception:
                pass
        followers = _collect_usernames(
            driver,
            "followers",
            progress=(lambda c: progress("followers", c)) if progress else None,
            item_delay_min=item_delay_min,
            item_delay_max=item_delay_max,
            expected_min=followers_total,
        )
        followers_fetch_seconds = int(time.time() - t0)

        if cancel_check and cancel_check():
            raise RuntimeError("cancelled")

        t1 = time.time()
        if progress:
            try:
                progress("following", 0)
            except Exception:
                pass
        followees = _collect_usernames(
            driver,
            "following",
            progress=(lambda c: progress("following", c)) if progress else None,
            item_delay_min=item_delay_min,
            item_delay_max=item_delay_max,
            expected_min=following_total,
        )
        followees_fetch_seconds = int(time.time() - t1)

    timestamp = datetime.now(tz).strftime("%Y-%m-%d_%H-%M-%S")
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
        "followers_count": len(followers),
        "followees_count": len(followees),
        "followers": followers,
        "followees": followees,
        "non_followbacks": non_followbacks,
        "followers_fetch_seconds": followers_fetch_seconds,
        "followees_fetch_seconds": followees_fetch_seconds,
        "followers_rate": followers_rate,
        "followees_rate": followees_rate,
        "changes": changes,
        "run_id": run_id,
    }
