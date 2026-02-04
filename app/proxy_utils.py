import os
from typing import Optional
from urllib.parse import quote


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_host(host: str) -> str:
    host = (host or "").strip().rstrip("/")
    if host.startswith("http://"):
        host = host[7:]
    elif host.startswith("https://"):
        host = host[8:]
    return host


def build_proxy_server(host: str, port: int) -> str:
    return f"http://{_normalize_host(host)}:{int(port)}"


def build_proxy_url(host: str, port: int, username: str | None = None, password: str | None = None) -> str:
    host = _normalize_host(host)
    if username and password:
        user_enc = quote(str(username), safe="")
        pass_enc = quote(str(password), safe="")
        return f"http://{user_enc}:{pass_enc}@{host}:{int(port)}"
    return f"http://{host}:{int(port)}"


def load_proxy_from_env() -> Optional[dict]:
    enabled = _truthy(os.getenv("INSTALAB_PROXY_ENABLED"))
    if not enabled:
        return None
    access_mode = (os.getenv("INSTALAB_PROXY_ACCESS_MODE") or "native").strip().lower()
    if access_mode != "native":
        access_mode = "native"
    host = _normalize_host(os.getenv("INSTALAB_PROXY_HOST", ""))
    port_raw = os.getenv("INSTALAB_PROXY_PORT", "7000")
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 7000
    if not host:
        return None
    username = (os.getenv("INSTALAB_PROXY_USERNAME") or "").strip()
    password = (os.getenv("INSTALAB_PROXY_PASSWORD") or "").strip()
    provider = (os.getenv("INSTALAB_PROXY_PROVIDER") or "decodo").strip().lower()
    return {
        "enabled": True,
        "access_mode": access_mode,
        "provider": provider,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "server": build_proxy_server(host, port),
        "url": build_proxy_url(host, port, username, password),
    }
