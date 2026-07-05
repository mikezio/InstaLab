from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from db import ddl, get_columns, get_db, is_postgres
from crypto_utils import decrypt_value, encrypt_value


LOGIN_TABLE = "login_accounts"
CHALLENGE_TTL_MINUTES = 10


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


def _json_value(value: Any):
    if value is None:
        return None
    if is_postgres():
        try:
            import psycopg2.extras

            return psycopg2.extras.Json(value)
        except Exception:
            return json.dumps(value)
    return json.dumps(value)


def _parse_json(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


def init_login_table() -> None:
    conn = get_db()
    try:
        conn.execute(
            ddl(
                """
                CREATE TABLE IF NOT EXISTS login_accounts (
                    login_username TEXT PRIMARY KEY,
                    login_password_enc TEXT,
                    totp_seed_enc TEXT,
                    challenge_code_enc TEXT,
                    challenge_code_at TEXT,
                    new_password_enc TEXT,
                    challenge_email_host TEXT,
                    challenge_email_port INTEGER,
                    challenge_email_ssl INTEGER NOT NULL DEFAULT 1,
                    challenge_email_username_enc TEXT,
                    challenge_email_password_enc TEXT,
                    challenge_email_mailbox TEXT,
                    session_fail_streak INTEGER NOT NULL DEFAULT 0,
                    session_last_fail_at TEXT,
                    cookie_file TEXT,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    source TEXT,
                    session_settings JSONB,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT,
                    last_error TEXT
                )
                """
            )
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_login_accounts_disabled ON login_accounts(disabled)")
        cols = get_columns(conn, LOGIN_TABLE)
        if "session_fail_streak" not in cols:
            conn.execute("ALTER TABLE login_accounts ADD COLUMN session_fail_streak INTEGER NOT NULL DEFAULT 0")
        if "session_last_fail_at" not in cols:
            conn.execute("ALTER TABLE login_accounts ADD COLUMN session_last_fail_at TEXT")
        if "challenge_email_host" not in cols:
            conn.execute("ALTER TABLE login_accounts ADD COLUMN challenge_email_host TEXT")
        if "challenge_email_port" not in cols:
            conn.execute("ALTER TABLE login_accounts ADD COLUMN challenge_email_port INTEGER")
        if "challenge_email_ssl" not in cols:
            conn.execute("ALTER TABLE login_accounts ADD COLUMN challenge_email_ssl INTEGER NOT NULL DEFAULT 1")
        if "challenge_email_username_enc" not in cols:
            conn.execute("ALTER TABLE login_accounts ADD COLUMN challenge_email_username_enc TEXT")
        if "challenge_email_password_enc" not in cols:
            conn.execute("ALTER TABLE login_accounts ADD COLUMN challenge_email_password_enc TEXT")
        if "challenge_email_mailbox" not in cols:
            conn.execute("ALTER TABLE login_accounts ADD COLUMN challenge_email_mailbox TEXT")
        conn.commit()
    finally:
        conn.close()


def list_logins(include_secrets: bool = False) -> list[dict]:
    conn = get_db()
    try:
        cur = conn.execute(
            """
            SELECT login_username, login_password_enc, totp_seed_enc,
                   challenge_email_host, challenge_email_port, challenge_email_ssl,
                   challenge_email_username_enc, challenge_email_password_enc, challenge_email_mailbox,
                   session_fail_streak, session_last_fail_at, cookie_file, disabled,
                   source, session_settings, created_at, updated_at, last_login_at, last_error
            FROM login_accounts
            ORDER BY login_username
            """
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        data = dict(row)
        data["session_settings"] = _parse_json(data.get("session_settings"))
        data["has_password"] = bool(data.get("login_password_enc"))
        data["has_totp_seed"] = bool(data.get("totp_seed_enc"))
        data["challenge_email_configured"] = bool(
            data.get("challenge_email_host")
            and data.get("challenge_email_username_enc")
            and data.get("challenge_email_password_enc")
        )
        if not include_secrets:
            data["challenge_email_username"] = decrypt_value(data.get("challenge_email_username_enc"))
            data.pop("login_password_enc", None)
            data.pop("totp_seed_enc", None)
            data.pop("challenge_email_username_enc", None)
            data.pop("challenge_email_password_enc", None)
        else:
            data["login_password"] = decrypt_value(data.pop("login_password_enc", None))
            data["totp_seed"] = decrypt_value(data.pop("totp_seed_enc", None))
            data["challenge_email_username"] = decrypt_value(data.pop("challenge_email_username_enc", None))
            data["challenge_email_password"] = decrypt_value(data.pop("challenge_email_password_enc", None))
        out.append(data)
    return out


def get_login(login_username: str, include_secrets: bool = False) -> dict | None:
    conn = get_db()
    try:
        cur = conn.execute(
            """
            SELECT login_username, login_password_enc, totp_seed_enc, challenge_code_enc, challenge_code_at,
                   new_password_enc, challenge_email_host, challenge_email_port, challenge_email_ssl,
                   challenge_email_username_enc, challenge_email_password_enc, challenge_email_mailbox,
                   session_fail_streak, session_last_fail_at, cookie_file, disabled, source, session_settings,
                   created_at, updated_at, last_login_at, last_error
            FROM login_accounts
            WHERE login_username = ?
            """,
            (login_username,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    data = dict(row)
    data["session_settings"] = _parse_json(data.get("session_settings"))
    data["has_password"] = bool(data.get("login_password_enc"))
    data["has_totp_seed"] = bool(data.get("totp_seed_enc"))
    data["challenge_email_configured"] = bool(
        data.get("challenge_email_host")
        and data.get("challenge_email_username_enc")
        and data.get("challenge_email_password_enc")
    )
    if include_secrets:
        data["login_password"] = decrypt_value(data.pop("login_password_enc", None))
        data["totp_seed"] = decrypt_value(data.pop("totp_seed_enc", None))
        data["challenge_code"] = decrypt_value(data.pop("challenge_code_enc", None))
        data["new_password"] = decrypt_value(data.pop("new_password_enc", None))
        data["challenge_email_username"] = decrypt_value(data.pop("challenge_email_username_enc", None))
        data["challenge_email_password"] = decrypt_value(data.pop("challenge_email_password_enc", None))
    else:
        data["challenge_email_username"] = decrypt_value(data.get("challenge_email_username_enc"))
        data.pop("login_password_enc", None)
        data.pop("totp_seed_enc", None)
        data.pop("challenge_code_enc", None)
        data.pop("new_password_enc", None)
        data.pop("challenge_email_username_enc", None)
        data.pop("challenge_email_password_enc", None)
    return data


def upsert_login(
    *,
    login_username: str,
    login_password: str | None = None,
    totp_seed: str | None = None,
    cookie_file: str | None = None,
    disabled: bool | None = None,
    source: str | None = None,
    session_settings: dict | None = None,
    challenge_code: str | None = None,
    new_password: str | None = None,
    last_error: str | None = None,
    last_login_at: str | None = None,
) -> None:
    existing = get_login(login_username, include_secrets=True)
    created_at = existing.get("created_at") if existing else _utc_now()
    updated_at = _utc_now()
    login_password = login_password if login_password is not None else (existing.get("login_password") if existing else None)
    totp_seed = totp_seed if totp_seed is not None else (existing.get("totp_seed") if existing else None)
    cookie_file = cookie_file if cookie_file is not None else (existing.get("cookie_file") if existing else None)
    disabled = int(disabled) if disabled is not None else int(existing.get("disabled") if existing else 0)
    source = source if source is not None else (existing.get("source") if existing else "db")
    session_settings = session_settings if session_settings is not None else (existing.get("session_settings") if existing else None)
    challenge_code = challenge_code if challenge_code is not None else (existing.get("challenge_code") if existing else None)
    new_password = new_password if new_password is not None else (existing.get("new_password") if existing else None)
    last_error = last_error if last_error is not None else (existing.get("last_error") if existing else None)
    last_login_at = last_login_at if last_login_at is not None else (existing.get("last_login_at") if existing else None)

    enc_password = encrypt_value(login_password)
    enc_totp = encrypt_value(totp_seed)
    enc_challenge = encrypt_value(challenge_code)
    enc_new_password = encrypt_value(new_password)
    existing_challenge = existing.get("challenge_code") if existing else None
    existing_challenge_at = existing.get("challenge_code_at") if existing else None
    if challenge_code:
        if challenge_code != existing_challenge or not existing_challenge_at:
            challenge_code_at = _utc_now()
        else:
            challenge_code_at = existing_challenge_at
    else:
        challenge_code_at = None

    conn = get_db()
    try:
        if is_postgres():
            conn.execute(
                """
                INSERT INTO login_accounts (
                    login_username, login_password_enc, totp_seed_enc, challenge_code_enc, challenge_code_at,
                    new_password_enc, cookie_file, disabled, source, session_settings,
                    created_at, updated_at, last_login_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (login_username) DO UPDATE SET
                    login_password_enc = EXCLUDED.login_password_enc,
                    totp_seed_enc = EXCLUDED.totp_seed_enc,
                    challenge_code_enc = EXCLUDED.challenge_code_enc,
                    challenge_code_at = EXCLUDED.challenge_code_at,
                    new_password_enc = EXCLUDED.new_password_enc,
                    cookie_file = EXCLUDED.cookie_file,
                    disabled = EXCLUDED.disabled,
                    source = EXCLUDED.source,
                    session_settings = EXCLUDED.session_settings,
                    updated_at = EXCLUDED.updated_at,
                    last_login_at = EXCLUDED.last_login_at,
                    last_error = EXCLUDED.last_error
                """,
                (
                    login_username,
                    enc_password,
                    enc_totp,
                    enc_challenge,
                    challenge_code_at,
                    enc_new_password,
                    cookie_file,
                    disabled,
                    source,
                    _json_value(session_settings),
                    created_at,
                    updated_at,
                    last_login_at,
                    last_error,
                ),
            )
        else:
            conn.execute(
                """
                INSERT OR REPLACE INTO login_accounts (
                    login_username, login_password_enc, totp_seed_enc, challenge_code_enc, challenge_code_at,
                    new_password_enc, cookie_file, disabled, source, session_settings,
                    created_at, updated_at, last_login_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    login_username,
                    enc_password,
                    enc_totp,
                    enc_challenge,
                    challenge_code_at,
                    enc_new_password,
                    cookie_file,
                    disabled,
                    source,
                    _json_value(session_settings),
                    created_at,
                    updated_at,
                    last_login_at,
                    last_error,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def set_session_settings(login_username: str, settings: dict) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE login_accounts
            SET session_settings = ?, updated_at = ?
            WHERE login_username = ?
            """,
            (
                _json_value(settings),
                _utc_now(),
                login_username,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def clear_session_settings(login_username: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE login_accounts
            SET session_settings = NULL, updated_at = ?
            WHERE login_username = ?
            """,
            (_utc_now(), login_username),
        )
        conn.commit()
    finally:
        conn.close()


def consume_challenge_code(login_username: str) -> str | None:
    entry = get_login(login_username, include_secrets=True)
    if not entry:
        return None
    code = entry.get("challenge_code")
    if not code:
        return None
    code_at = entry.get("challenge_code_at")
    if code_at:
        try:
            from datetime import datetime, timezone

            parsed = datetime.fromisoformat(code_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - parsed).total_seconds()
            if age_seconds > (CHALLENGE_TTL_MINUTES * 60):
                conn = get_db()
                try:
                    conn.execute(
                        """
                        UPDATE login_accounts
                        SET challenge_code_enc = NULL, challenge_code_at = NULL, updated_at = ?
                        WHERE login_username = ?
                        """,
                        (_utc_now(), login_username),
                    )
                    conn.commit()
                finally:
                    conn.close()
                return None
        except Exception:
            pass
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE login_accounts
            SET challenge_code_enc = NULL, challenge_code_at = NULL, updated_at = ?
            WHERE login_username = ?
            """,
            (_utc_now(), login_username),
        )
        conn.commit()
    finally:
        conn.close()
    return code


def set_challenge_code(login_username: str, code: str) -> None:
    upsert_login(login_username=login_username, challenge_code=code)


def clear_challenge_code(login_username: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE login_accounts
            SET challenge_code_enc = NULL, challenge_code_at = NULL, updated_at = ?
            WHERE login_username = ?
            """,
            (_utc_now(), login_username),
        )
        conn.commit()
    finally:
        conn.close()


def set_new_password(login_username: str, new_password: str) -> None:
    upsert_login(login_username=login_username, new_password=new_password)


def consume_new_password(login_username: str) -> str | None:
    entry = get_login(login_username, include_secrets=True)
    if not entry:
        return None
    pwd = entry.get("new_password")
    if not pwd:
        return None
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE login_accounts
            SET new_password_enc = NULL, updated_at = ?
            WHERE login_username = ?
            """,
            (_utc_now(), login_username),
        )
        conn.commit()
    finally:
        conn.close()
    return pwd


def set_totp_seed(login_username: str, seed: str | None) -> None:
    upsert_login(login_username=login_username, totp_seed=seed)


def set_login_password(login_username: str, password: str | None) -> None:
    upsert_login(login_username=login_username, login_password=password)


def set_challenge_email_settings(
    login_username: str,
    *,
    host: str | None,
    port: int | None = None,
    use_ssl: bool = True,
    username: str | None,
    password: str | None = None,
    mailbox: str | None = "INBOX",
) -> None:
    existing = get_login(login_username, include_secrets=True) or {}
    existing_password = existing.get("challenge_email_password")
    enc_username = encrypt_value(username)
    enc_password = encrypt_value(password if password is not None else existing_password)
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE login_accounts
            SET challenge_email_host = ?,
                challenge_email_port = ?,
                challenge_email_ssl = ?,
                challenge_email_username_enc = ?,
                challenge_email_password_enc = ?,
                challenge_email_mailbox = ?,
                updated_at = ?
            WHERE login_username = ?
            """,
            (
                (host or "").strip() or None,
                int(port) if port else None,
                1 if use_ssl else 0,
                enc_username,
                enc_password,
                (mailbox or "INBOX").strip() or "INBOX",
                _utc_now(),
                login_username,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def clear_challenge_email_settings(login_username: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE login_accounts
            SET challenge_email_host = NULL,
                challenge_email_port = NULL,
                challenge_email_ssl = 1,
                challenge_email_username_enc = NULL,
                challenge_email_password_enc = NULL,
                challenge_email_mailbox = NULL,
                updated_at = ?
            WHERE login_username = ?
            """,
            (_utc_now(), login_username),
        )
        conn.commit()
    finally:
        conn.close()


def set_last_login(login_username: str, error: str | None = None) -> None:
    upsert_login(login_username=login_username, last_login_at=_utc_now(), last_error=error)


def set_last_error(login_username: str, error: str | None) -> None:
    upsert_login(login_username=login_username, last_error=error)


def delete_login(login_username: str) -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM login_accounts WHERE login_username = ?", (login_username,))
        conn.commit()
    finally:
        conn.close()


def disable_login(login_username: str, disabled: bool = True) -> None:
    upsert_login(login_username=login_username, disabled=disabled)


def record_session_validation_failure(login_username: str) -> int:
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE login_accounts
            SET session_fail_streak = COALESCE(session_fail_streak, 0) + 1,
                session_last_fail_at = ?,
                updated_at = ?
            WHERE login_username = ?
            """,
            (_utc_now(), _utc_now(), login_username),
        )
        conn.commit()
    finally:
        conn.close()
    entry = get_login(login_username, include_secrets=False) or {}
    try:
        return int(entry.get("session_fail_streak") or 0)
    except Exception:
        return 0


def reset_session_validation_failures(login_username: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE login_accounts
            SET session_fail_streak = 0,
                session_last_fail_at = NULL,
                updated_at = ?
            WHERE login_username = ?
            """,
            (_utc_now(), login_username),
        )
        conn.commit()
    finally:
        conn.close()
