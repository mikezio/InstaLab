#!/usr/bin/env python3
import argparse
import json
import os
from contextlib import contextmanager


def _select_backend():
    name = (os.getenv("INSTALAB_SCRAPER_BACKEND") or os.getenv("SCRAPER_BACKEND") or "private").strip().lower()
    if name not in {"private", "private_api", "private-api", "osintgram"}:
        raise RuntimeError("private API backend only; set INSTALAB_SCRAPER_BACKEND=private")
    from private_api_tracker import fetch_counts, has_session  # type: ignore
    return fetch_counts, has_session


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--cookie-file", default="")
    parser.add_argument("--result", required=True)
    # Keep `--request-timeout` as a deprecated alias for older callers.
    parser.add_argument("--http-timeout", type=float, default=None)
    parser.add_argument("--request-timeout", type=float, default=None)
    parser.add_argument("--request-sleep", type=float, default=None)
    args = parser.parse_args()
    if args.http_timeout is None and args.request_timeout is not None:
        print("Warning: --request-timeout is deprecated; use --http-timeout", flush=True)
    http_timeout_seconds = float(args.http_timeout if args.http_timeout is not None else (args.request_timeout if args.request_timeout is not None else 120.0))
    request_sleep_seconds = float(args.request_sleep if args.request_sleep is not None else (os.environ.get("RUN_PRIVATE_REQUEST_SLEEP_SECONDS") or 0) or 0)

    fetch_counts, has_session = _select_backend()
    password = os.environ.get("RUN_LOGIN_PASSWORD")
    two_factor_code = os.environ.get("RUN_2FA_CODE")
    challenge_code = os.environ.get("RUN_CHALLENGE_CODE")
    totp_seed = os.environ.get("RUN_TOTP_SEED")
    device_settings_json = os.environ.get("RUN_DEVICE_SETTINGS_JSON")
    user_agent = os.environ.get("RUN_USER_AGENT")
    delay_min = float(os.environ.get("RUN_ITEM_DELAY_MIN", "0") or 0)
    delay_max = float(os.environ.get("RUN_ITEM_DELAY_MAX", "0") or 0)
    cookie_file = args.cookie_file or ""
    if not password and not has_session(args.login):
        with _trace_span(
            "instalab.count_check",
            backend=(os.getenv("INSTALAB_SCRAPER_BACKEND") or os.getenv("SCRAPER_BACKEND") or "private").strip().lower(),
            proxy_enabled=bool(os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")),
            login_mode=(os.environ.get("RUN_LOGIN_MODE") or "auto").strip().lower(),
        ) as span:
            if span:
                span.set_tag("instalab.status", "error")
                span.set_tag("error.msg", "missing RUN_LOGIN_PASSWORD")
        _write_json(args.result, {"status": "error", "error": "missing RUN_LOGIN_PASSWORD"})
        return 2
    password = password or ""

    backend_name = (os.getenv("INSTALAB_SCRAPER_BACKEND") or os.getenv("SCRAPER_BACKEND") or "private").strip().lower()
    login_mode = (os.environ.get("RUN_LOGIN_MODE") or "auto").strip().lower()
    proxy_enabled = bool(os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"))
    with _trace_span(
        "instalab.count_check",
        backend=backend_name,
        proxy_enabled=proxy_enabled,
        login_mode=login_mode,
    ) as root_span:
        try:
            with _trace_span("instalab.fetch_counts", backend=backend_name):
                res = fetch_counts(
                    login_username=args.login,
                    login_password=password,
                    target_username=args.target,
                    cookie_file=cookie_file or None,
                    http_timeout_seconds=http_timeout_seconds,
                    request_sleep_seconds=request_sleep_seconds,
                    login_mode=login_mode,
                    two_factor_code=two_factor_code,
                    challenge_code=challenge_code,
                    totp_seed=totp_seed,
                    delay_min=delay_min,
                    delay_max=delay_max,
                    device_settings_json=device_settings_json,
                    user_agent=user_agent,
                )
            _write_json(args.result, {"status": "success", "result": res})
            if root_span:
                root_span.set_tag("instalab.status", "success")
            return 0
        except Exception as exc:  # noqa: BLE001
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
