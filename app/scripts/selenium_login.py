#!/usr/bin/env python3
import argparse
import os
import sys
import time
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", required=True, help="Instagram login username")
    parser.add_argument("--cookie-out", required=True, help="Output cookie file path")
    parser.add_argument("--display", default=":1")
    args = parser.parse_args()

    os.environ.setdefault("DISPLAY", args.display)
    home_dir = Path(os.getenv("CHROME_HOME", "/tmp/chrome-home"))
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
    options.binary_location = os.getenv("CHROME_BIN") or "/usr/bin/chromium"
    service_path = os.getenv("CHROMEDRIVER_BIN") or "/usr/bin/chromedriver"

    service = Service(service_path, log_output="/tmp/chromedriver.log", env=os.environ.copy())
    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.get("https://www.instagram.com/accounts/login/")
        try:
            WebDriverWait(driver, 10).until(lambda d: d.find_elements(By.NAME, "username"))
            user_input = driver.find_element(By.NAME, "username")
            user_input.clear()
            user_input.send_keys(args.login)
            user_input.send_keys(Keys.TAB)
        except Exception:
            pass
        print("Login page opened. Complete login in VNC (and any prompts), then press ENTER here to export cookies.")
        input()
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
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
