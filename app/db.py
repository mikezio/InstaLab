import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Load env from shared secrets first, then local .env fallback
_env_paths = [
    Path(os.getenv("INSTALAB_ENV", "/srv/secrets/instalab.env")),
    BASE_DIR / ".env",
]
for _p in _env_paths:
    if _p.exists():
        load_dotenv(_p)

DB_TYPE = os.getenv("INSTALAB_DB_TYPE", "sqlite").strip().lower()


def is_postgres() -> bool:
    return DB_TYPE in {"postgres", "postgresql"}


def _adapt_sql(sql: str) -> str:
    if is_postgres():
        # sqlite uses ?, psycopg2 uses %s
        return sql.replace("?", "%s")
    return sql


class DBConn:
    def __init__(self, conn, kind: str):
        self._conn = conn
        self._kind = kind

    def execute(self, sql: str, params=()):
        if self._kind == "postgres":
            cur = self._conn.cursor()
            cur.execute(_adapt_sql(sql), params)
            return cur
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, seq_of_params):
        if self._kind == "postgres":
            cur = self._conn.cursor()
            cur.executemany(_adapt_sql(sql), seq_of_params)
            return cur
        return self._conn.executemany(sql, seq_of_params)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def cursor(self):
        if self._kind == "postgres":
            return self._conn.cursor()
        return self._conn.cursor()


def get_db():
    if is_postgres():
        import psycopg2
        import psycopg2.extras

        host = os.getenv("INSTALAB_DB_HOST", "127.0.0.1")
        port = int(os.getenv("INSTALAB_DB_PORT", "5432"))
        name = os.getenv("INSTALAB_DB_NAME", "instalab")
        user = os.getenv("INSTALAB_DB_USER", "instalab")
        password = os.getenv("INSTALAB_DB_PASS", "")

        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=name,
            user=user,
            password=password,
            cursor_factory=psycopg2.extras.DictCursor,
        )
        return DBConn(conn, "postgres")

    import sqlite3

    db_path = os.getenv("INSTALAB_SQLITE_PATH", str(BASE_DIR / "instaloader.db"))
    # Ensure parent directory exists for the database file
    db_path_obj = Path(db_path)
    try:
        db_path_obj.parent.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as e:
        # If we can't create the configured directory (e.g., /data/instalab),
        # fall back to using a local directory in the app folder
        import sys
        print(f"[db] Cannot create directory {db_path_obj.parent}: {e}", file=sys.stderr)
        print(f"[db] Falling back to local database in app directory", file=sys.stderr)
        db_path = str(BASE_DIR / "instaloader.db")
        db_path_obj = Path(db_path)
        db_path_obj.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return DBConn(conn, "sqlite")


def get_columns(conn: DBConn, table: str):
    # Whitelist valid table names to prevent SQL injection
    VALID_TABLES = {"config", "runs", "run_followers", "run_followees", "schedules", "unfollow_actions"}
    if table not in VALID_TABLES:
        raise ValueError(f"Invalid table name: {table}")
    
    if is_postgres():
        cur = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        )
        return {row[0] for row in cur.fetchall()}
    # For SQLite, use parameterized query with quote_identifier pattern
    # SQLite doesn't support parameterized table names in PRAGMA, so we validate first
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def ddl(sqlite_sql: str, pg_sql: str) -> str:
    return pg_sql if is_postgres() else sqlite_sql


def insert_ignore_sql(table: str, columns: list):
    cols = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    if is_postgres():
        return f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    return f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
