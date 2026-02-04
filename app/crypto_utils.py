import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    key = os.getenv("INSTALAB_ENCRYPTION_KEY") or os.getenv("INSTALAB_FERNET_KEY")
    if not key:
        raise RuntimeError("missing INSTALAB_ENCRYPTION_KEY")
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def encrypt_value(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value)
    if value == "":
        return None
    fernet = _get_fernet()
    return fernet.encrypt(value.encode()).decode()


def decrypt_value(value: str | None) -> str | None:
    if not value:
        return None
    fernet = _get_fernet()
    try:
        return fernet.decrypt(str(value).encode()).decode()
    except InvalidToken:
        raise RuntimeError("invalid INSTALAB_ENCRYPTION_KEY for stored secrets")
