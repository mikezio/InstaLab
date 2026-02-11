import os
from pathlib import Path
from threading import Lock

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

DB_TYPE = os.getenv("INSTALAB_DB_TYPE", "postgres").strip().lower()

# Simple connection pool for PostgreSQL
_pg_pool = None
_pg_pool_lock = Lock()


def is_postgres() -> bool:
    return DB_TYPE in {"postgres", "postgresql"}


def _adapt_sql(sql: str) -> str:
    if is_postgres():
        # psycopg2 uses %s placeholders
        return sql.replace("?", "%s")
    return sql


class DBConn:
    def __init__(self, conn, kind: str, pooled: bool = False):
        self._conn = conn
        self._kind = kind
        self._pooled = pooled

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
        # If using pooled connection, return it to the pool instead of closing
        global _pg_pool
        if self._pooled and _pg_pool and self._kind == "postgres":
            try:
                _pg_pool.putconn(self._conn)
            except Exception:
                # If pool is closed or error, just close the connection
                self._conn.close()
        else:
            return self._conn.close()

    def cursor(self):
        if self._kind == "postgres":
            return self._conn.cursor()
        return self._conn.cursor()


def get_db():
    if not is_postgres():
        raise RuntimeError("Unsupported DB type. Set INSTALAB_DB_TYPE=postgres.")

    import psycopg2
    import psycopg2.extras
    from psycopg2 import pool

    global _pg_pool, _pg_pool_lock

    # Initialize pool on first use (lazy initialization)
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:  # Double-check pattern
                host = os.getenv("INSTALAB_DB_HOST", "127.0.0.1")
                port = int(os.getenv("INSTALAB_DB_PORT", "5432"))
                name = os.getenv("INSTALAB_DB_NAME", "instalab")
                user = os.getenv("INSTALAB_DB_USER", "instalab")
                password = os.getenv("INSTALAB_DB_PASS", "")

                # Create a threaded connection pool (min 2, max 10 connections)
                # ThreadedConnectionPool is thread-safe for Flask multi-threaded apps
                try:
                    _pg_pool = pool.ThreadedConnectionPool(
                        minconn=2,
                        maxconn=10,
                        host=host,
                        port=port,
                        dbname=name,
                        user=user,
                        password=password,
                        cursor_factory=psycopg2.extras.DictCursor,
                    )
                except Exception:
                    # If pool creation fails, fall back to direct connection
                    _pg_pool = None

    # Try to get connection from pool
    if _pg_pool:
        try:
            conn = _pg_pool.getconn()
            return DBConn(conn, "postgres", pooled=True)
        except Exception:
            pass

    # Fallback to direct connection if pool unavailable
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


def get_columns(conn: DBConn, table: str):
    # Whitelist valid table names to prevent SQL injection
    VALID_TABLES = {
        "config",
        "runs",
        "run_followers",
        "run_followees",
        "relationship_events",
        "followers_history",
        "followees_history",
        "schedules",
        "unfollow_actions",
        "count_checks",
        "login_accounts",
    }
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
    # Fallback for non-Postgres adapters used in local tooling/tests.
    # Some adapters don't support parameterized table names in PRAGMA-style introspection,
    # so we validate first.
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def ddl(pg_sql: str) -> str:
    return pg_sql


def insert_ignore_sql(table: str, columns: list):
    cols = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    if is_postgres():
        return f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    return f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
