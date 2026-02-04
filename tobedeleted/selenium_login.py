#!/usr/bin/env python3
import argparse
import os
import sys
import time
import select
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait


def _write_netscape_cookies(path: Path, cookies):
    header = "# Netscape HTTP Cookie File\n"
    lines = [header]
    for cookie in cookies:
        domain = cookie.get("domain", "")
        if not domain:
            continue
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        cookie_path = cookie.get("path", "/")
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        expiry = cookie.get("expiry") or 0
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        lines.append("\t".join([domain, include_sub, cookie_path, secure, str(expiry), name, value]))
    path.write_text("\n".join(lines) + "\n")


def _read_netscape_cookies(path: Path):
    cookies = []
    if not path.exists():
        return cookies
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, include_sub, cookie_path, secure, expiry, name, value = parts[:7]
        http_only = False
        if domain.startswith("#HttpOnly_"):
            http_only = True
            domain = domain[len("#HttpOnly_") :]
        cookie = {
            "domain": domain,
            "path": cookie_path or "/",
            "secure": secure.upper() == "TRUE",
            "name": name,
            "value": value,
        }
        if expiry and expiry.isdigit():
            cookie["expiry"] = int(expiry)
        if http_only:
            cookie["httpOnly"] = True
        cookies.append(cookie)
    return cookies


def _load_cookies(driver, cookie_path: Path):
    cookies = _read_netscape_cookies(cookie_path)
    if not cookies:
        return 0
    driver.get("https://www.instagram.com/")
    added = 0
    for cookie in cookies:
        domain = cookie.get("domain") or ""
        if "instagram.com" not in domain:
            continue
        try:
            driver.add_cookie(cookie)
            added += 1
        except Exception:
            continue
    driver.get("https://www.instagram.com/")
    return added


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", required=True, help="Instagram login username")
    parser.add_argument("--cookie-out", required=True, help="Output cookie file path")
    parser.add_argument("--cookie-in", default="", help="Existing cookie file to preload")
    parser.add_argument("--display", default=":1")
    parser.add_argument("--manual", action="store_true", help="Avoid automation signals; user-driven login only")
    args = parser.parse_args()

    os.environ.setdefault("DISPLAY", args.display)
    base_home = Path(os.getenv("CHROME_HOME", "/tmp/chrome-home"))
    home_dir = base_home / args.login
    if home_dir.exists():
        try:
            import shutil
            shutil.rmtree(home_dir, ignore_errors=True)
        except Exception:
            pass
    home_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home_dir)
    xdg_runtime = Path(os.getenv("XDG_RUNTIME_DIR", "/tmp/xdg-runtime"))
    xdg_runtime.mkdir(parents=True, exist_ok=True)
    try:
        xdg_runtime.chmod(0o700)
    except Exception:
        pass
    os.environ["XDG_RUNTIME_DIR"] = str(xdg_runtime)

    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-zygote")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--window-size=1280,800")
    options.add_argument(f"--user-data-dir={home_dir / 'profile'}")
    options.add_argument("--remote-debugging-pipe")
    options.add_argument("--log-file=/tmp/chrome.log")
    if args.manual:
        # Reduce automation fingerprints for human-driven VNC logins.
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-features=AutomationControlled")
        options.add_argument("--disable-infobars")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
    options.binary_location = os.getenv("CHROME_BIN") or "/usr/bin/chromium"
    service_path = os.getenv("CHROMEDRIVER_BIN") or "/usr/bin/chromedriver"

    service = Service(service_path, log_output="/tmp/chromedriver.log", env=os.environ.copy())
    driver = webdriver.Chrome(service=service, options=options)
    try:
        if args.manual:
            try:
                driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {
                        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});",
                    },
                )
            except Exception:
                pass
        if args.cookie_in:
            in_path = Path(args.cookie_in)
            if in_path.exists():
                added = _load_cookies(driver, in_path)
                print(f"Loaded {added} cookies from {in_path}")
        driver.get("https://www.instagram.com/accounts/login/")
        if not args.manual:
            try:
                WebDriverWait(driver, 10).until(lambda d: d.find_elements(By.NAME, "username"))
                user_input = driver.find_element(By.NAME, "username")
                user_input.clear()
                user_input.send_keys(args.login)
                user_input.send_keys(Keys.TAB)
            except Exception:
                pass
        keepalive = int(os.getenv("LOGIN_KEEPALIVE_SECONDS", "1800"))
        deadline = time.time() + keepalive
        print("Login page opened. Complete login in VNC, then press ENTER here to export cookies.")
        print(f"Waiting for finish signal (up to {keepalive}s)...")
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise RuntimeError("Login timed out waiting for finish signal")
            rlist, _, _ = select.select([sys.stdin], [], [], min(1.0, remaining))
            if not rlist:
                continue
            line = sys.stdin.readline()
            if line == "":
                # stdin closed; keep waiting until timeout
                time.sleep(1.0)
                continue
            break
        time.sleep(1)
        cookies = driver.get_cookies()
        out_path = Path(args.cookie_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_netscape_cookies(out_path, cookies)
        try:
            os.chmod(out_path, 0o640)
        except Exception:
            pass
        print(f"Saved cookies to {out_path}")
        profile_dir = home_dir / "profile"
        try:
            if profile_dir.exists():
                for item in profile_dir.rglob("*"):
                    try:
                        item.chmod(0o700)
                    except Exception:
                        pass
                import shutil
                shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception:
            pass
    finally:
        driver.quit()
        if proxy_ext:
            try:
                import shutil
                shutil.rmtree(proxy_ext, ignore_errors=True)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
