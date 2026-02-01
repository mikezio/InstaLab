#!/usr/bin/env python3
import argparse
import json
import os


def _select_backend():
    name = (os.getenv("INSTALAB_SCRAPER_BACKEND") or os.getenv("SCRAPER_BACKEND") or "selenium").strip().lower()
    if name in {"instaloader", "insta", "iloader"}:
        from instaloader_tracker import fetch_counts, has_session  # type: ignore
    else:
        from selenium_tracker import fetch_counts, has_session  # type: ignore
    return fetch_counts, has_session


def _write_json(path, payload):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--cookie-file", default="")
    parser.add_argument("--result", required=True)
    parser.add_argument("--request-timeout", type=float, default=120.0)
    args = parser.parse_args()

    fetch_counts, has_session = _select_backend()
    password = os.environ.get("RUN_LOGIN_PASSWORD")
    cookie_file = args.cookie_file or ""
    cookie_ok = bool(cookie_file and os.path.exists(cookie_file))
    if not password and not has_session(args.login) and not cookie_ok:
        _write_json(args.result, {"status": "error", "error": "missing RUN_LOGIN_PASSWORD"})
        return 2
    password = password or ""

    try:
        res = fetch_counts(
            login_username=args.login,
            login_password=password,
            target_username=args.target,
            cookie_file=cookie_file or None,
            request_timeout=args.request_timeout,
        )
        _write_json(args.result, {"status": "success", "result": res})
        return 0
    except Exception as exc:  # noqa: BLE001
        _write_json(args.result, {"status": "error", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
