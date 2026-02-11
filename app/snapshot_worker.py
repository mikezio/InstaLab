#!/usr/bin/env python3
import argparse
import json
import os
import time
import ssl
import urllib.request
import traceback
from contextlib import contextmanager


def _select_backend():
    name = (os.getenv("INSTALAB_SCRAPER_BACKEND") or os.getenv("SCRAPER_BACKEND") or "private").strip().lower()
    if name not in {"private", "private_api", "private-api", "osintgram"}:
        raise RuntimeError("private API backend only; set INSTALAB_SCRAPER_BACKEND=private")
    from private_api_tracker import has_session, snapshot_profile  # type: ignore
    return has_session, snapshot_profile


def _write_json(path, payload):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


@contextmanager
def _trace_span(name, **tags):
    try:
        from ddtrace import tracer
    except Exception:  # pragma: no cover - ddtrace may be missing in local tools
        tracer = None
    if tracer is None:
        yield None
        return
    with tracer.trace(name) as span:
        for key, value in tags.items():
            if value is not None:
                span.set_tag(key, value)
        yield span


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
    # NOTE: instagrapi's `request_timeout` is a per-request sleep, not a network timeout.
    # We keep `--request-timeout` as a deprecated alias for older callers.
    parser.add_argument("--http-timeout", type=float, default=None)
    parser.add_argument("--request-timeout", type=float, default=None)
    parser.add_argument("--request-sleep", type=float, default=None)
    args = parser.parse_args()
    if args.http_timeout is None and args.request_timeout is not None:
        print("Warning: --request-timeout is deprecated; use --http-timeout", flush=True)
    http_timeout_seconds = float(args.http_timeout if args.http_timeout is not None else (args.request_timeout if args.request_timeout is not None else 600.0))
    request_sleep_seconds = float(args.request_sleep if args.request_sleep is not None else (os.environ.get("RUN_PRIVATE_REQUEST_SLEEP_SECONDS") or 0))
    backend_name = (os.getenv("INSTALAB_SCRAPER_BACKEND") or os.getenv("SCRAPER_BACKEND") or "private").strip().lower()
    proxy_set = bool(os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"))
    proxy_enabled_flag = str(os.environ.get("INSTALAB_PROXY_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
    proxy_access_mode = "native"
    print(f"Proxy enabled: {'yes' if proxy_set else 'no'}")
    if proxy_enabled_flag and not proxy_set:
        with _trace_span(
            "instalab.snapshot",
            backend=backend_name,
            proxy_enabled=proxy_set,
            proxy_access_mode=proxy_access_mode,
        ) as span:
            if span:
                span.set_tag("instalab.status", "error")
                span.set_tag("error.msg", "proxy enabled but HTTP_PROXY/HTTPS_PROXY missing")
        _write_json(args.result, {"status": "error", "error": "proxy enabled but HTTP_PROXY/HTTPS_PROXY missing"})
        return 2
    if proxy_set:
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        try:
            with _trace_span("instalab.proxy_test", mode="native"):
                handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
                ctx = ssl._create_unverified_context()
                https_handler = urllib.request.HTTPSHandler(context=ctx)
                opener = urllib.request.build_opener(handler, https_handler)
                req = urllib.request.Request("https://ip.decodo.com/json")
                with opener.open(req, timeout=20) as resp:
                    snippet = resp.read(120).decode("utf-8", errors="ignore").strip()
                print(f"Proxy test OK: {snippet}")
        except Exception as exc:
            print(f"Proxy test FAILED: {exc}")

    has_session, snapshot_profile = _select_backend()
    password = os.environ.get("RUN_LOGIN_PASSWORD")
    cookie_file = args.cookie_file or ""
    if not password and not has_session(args.login):
        with _trace_span(
            "instalab.snapshot",
            backend=backend_name,
            proxy_enabled=proxy_set,
            proxy_access_mode=proxy_access_mode,
        ) as span:
            if span:
                span.set_tag("instalab.status", "error")
                span.set_tag("error.msg", "missing RUN_LOGIN_PASSWORD")
        _write_json(args.result, {"status": "error", "error": "missing RUN_LOGIN_PASSWORD"})
        return 2
    password = password or ""

    delay_min = float(os.environ.get("RUN_ITEM_DELAY_MIN", "0") or 0)
    delay_max = float(os.environ.get("RUN_ITEM_DELAY_MAX", "0") or 0)
    pause_every_min = int(os.environ.get("RUN_PAUSE_EVERY_MIN", "0") or 0)
    pause_every_max = int(os.environ.get("RUN_PAUSE_EVERY_MAX", "0") or 0)
    pause_seconds_min = float(os.environ.get("RUN_PAUSE_SECONDS_MIN", "0") or 0)
    pause_seconds_max = float(os.environ.get("RUN_PAUSE_SECONDS_MAX", "0") or 0)
    trace_enabled = str(os.environ.get("RUN_TRACE_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
    two_factor_code = os.environ.get("RUN_2FA_CODE")
    challenge_code = os.environ.get("RUN_CHALLENGE_CODE")
    totp_seed = os.environ.get("RUN_TOTP_SEED")
    device_settings_json = os.environ.get("RUN_DEVICE_SETTINGS_JSON")
    user_agent = os.environ.get("RUN_USER_AGENT")
    trace_path = os.environ.get("RUN_TRACE_PATH", "")
    profile_only = str(os.environ.get("RUN_PROFILE_ONLY", "")).strip().lower() in {"1", "true", "yes", "on"}
    login_mode = (os.environ.get("RUN_LOGIN_MODE") or "auto").strip().lower()
    with _trace_span(
        "instalab.snapshot",
        backend=backend_name,
        proxy_enabled=proxy_set,
        proxy_access_mode=proxy_access_mode,
        profile_only=profile_only,
        login_mode=login_mode,
    ) as root_span:
        try:
            with _trace_span("instalab.snapshot_profile", backend=backend_name, profile_only=profile_only):
                res = snapshot_profile(
                    login_username=args.login,
                    login_password=password,
                    target_username=args.target,
                    cookie_file=cookie_file or None,
                    http_timeout_seconds=http_timeout_seconds,
                    request_sleep_seconds=request_sleep_seconds,
                    db_path=args.db_path,
                    profile_only=profile_only,
                    login_mode=login_mode,
                    item_delay_min=delay_min,
                    item_delay_max=delay_max,
                    pause_every_min=pause_every_min,
                    pause_every_max=pause_every_max,
                    pause_seconds_min=pause_seconds_min,
                    pause_seconds_max=pause_seconds_max,
                    trace_enabled=trace_enabled,
                    trace_path=trace_path,
                    two_factor_code=two_factor_code,
                    challenge_code=challenge_code,
                    totp_seed=totp_seed,
                    device_settings_json=device_settings_json,
                    user_agent=user_agent,
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
            payload = {"status": "success", "result": slim}
            _write_json(args.result, payload)
            if not os.path.exists(args.result):
                _write_json(args.result, payload)
            if root_span:
                root_span.set_tag("instalab.status", "success")
            return 0
        except Exception as exc:  # noqa: BLE001
            try:
                traceback.print_exc()
            except Exception:
                pass
            if root_span:
                root_span.set_tag("instalab.status", "error")
                root_span.set_tag("error.msg", str(exc))
                root_span.set_tag("error.type", exc.__class__.__name__)
            payload = {"status": "error", "error": str(exc)}
            code = getattr(exc, "code", None)
            if code:
                payload["error_code"] = code
            _write_json(args.result, payload)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
