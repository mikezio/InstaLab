import os
from typing import Optional


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_host(host: str) -> str:
    return (host or "").strip().rstrip("/")


def build_proxy_server(host: str, port: int) -> str:
    return f"http://{_normalize_host(host)}:{int(port)}"


def build_proxy_url(host: str, port: int, username: str | None = None, password: str | None = None) -> str:
    host = _normalize_host(host)
    if username and password:
        return f"http://{username}:{password}@{host}:{int(port)}"
    return f"http://{host}:{int(port)}"


def load_proxy_from_env() -> Optional[dict]:
    enabled = _truthy(os.getenv("INSTALAB_PROXY_ENABLED"))
    if not enabled:
        return None
    host = _normalize_host(os.getenv("INSTALAB_PROXY_HOST", ""))
    port_raw = os.getenv("INSTALAB_PROXY_PORT", "33335")
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 33335
    if not host:
        return None
    username = (os.getenv("INSTALAB_PROXY_USERNAME") or "").strip()
    password = (os.getenv("INSTALAB_PROXY_PASSWORD") or "").strip()
    provider = (os.getenv("INSTALAB_PROXY_PROVIDER") or "brightdata").strip().lower()
    return {
        "enabled": True,
        "provider": provider,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "server": build_proxy_server(host, port),
        "url": build_proxy_url(host, port, username, password),
    }
