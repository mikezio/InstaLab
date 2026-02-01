#!/usr/bin/env python3
import argparse
import json
import os
import time


def _select_backend():
    name = (os.getenv("INSTALAB_SCRAPER_BACKEND") or os.getenv("SCRAPER_BACKEND") or "selenium").strip().lower()
    if name in {"instaloader", "insta", "iloader"}:
        from instaloader_tracker import has_session, snapshot_profile  # type: ignore
    else:
        from selenium_tracker import has_session, snapshot_profile  # type: ignore
    return has_session, snapshot_profile


def _write_json(path, payload):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def _progress_writer(progress_path):
    def _cb(phase, count):
        payload = {
            "phase": phase,
            "updated_at": time.time(),
        }
        if isinstance(count, dict):
            payload.update(count)
        else:
            payload["count"] = int(count) if count is not None else None
        try:
            _write_json(progress_path, payload)
        except Exception:
            pass

    return _cb


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--cookie-file", default="")
    parser.add_argument("--progress", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--request-timeout", type=float, default=600.0)
    args = parser.parse_args()

    has_session, snapshot_profile = _select_backend()
    password = os.environ.get("RUN_LOGIN_PASSWORD")
    cookie_file = args.cookie_file or ""
    cookie_ok = bool(cookie_file and os.path.exists(cookie_file))
    if not password and not has_session(args.login) and not cookie_ok:
        _write_json(args.result, {"status": "error", "error": "missing RUN_LOGIN_PASSWORD"})
        return 2
    password = password or ""

    delay_min = float(os.environ.get("RUN_ITEM_DELAY_MIN", "0") or 0)
    delay_max = float(os.environ.get("RUN_ITEM_DELAY_MAX", "0") or 0)
    try:
        res = snapshot_profile(
            login_username=args.login,
            login_password=password,
            target_username=args.target,
            cookie_file=cookie_file or None,
            request_timeout=args.request_timeout,
            db_path=args.db_path,
            item_delay_min=delay_min,
            item_delay_max=delay_max,
            progress=_progress_writer(args.progress),
        )
        slim = {
            "timestamp": res.get("timestamp"),
            "followers_count": res.get("followers_count"),
            "followees_count": res.get("followees_count"),
            "non_followbacks_count": res.get("non_followbacks_count"),
            "run_id": res.get("run_id"),
            "followers_fetch_seconds": res.get("followers_fetch_seconds"),
            "followees_fetch_seconds": res.get("followees_fetch_seconds"),
            "followers_rate": res.get("followers_rate"),
            "followees_rate": res.get("followees_rate"),
        }
        _write_json(args.result, {"status": "success", "result": slim})
        return 0
    except Exception as exc:  # noqa: BLE001
        _write_json(args.result, {"status": "error", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
