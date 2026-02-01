#!/usr/bin/env python3
import os
import sqlite3
from pathlib import Path

import psycopg2


def _env(key, default=None):
    return os.getenv(key, default)


def sqlite_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def pg_conn():
    return psycopg2.connect(
        host=_env("INSTALAB_DB_HOST", "127.0.0.1"),
        port=int(_env("INSTALAB_DB_PORT", "5432")),
        dbname=_env("INSTALAB_DB_NAME", "instalab"),
        user=_env("INSTALAB_DB_USER", "instalab"),
        password=_env("INSTALAB_DB_PASS", ""),
    )


DDL = [
    """
    CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        id SERIAL PRIMARY KEY,
        target_username TEXT NOT NULL,
        login_username TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        followers_count INTEGER NOT NULL,
        followees_count INTEGER NOT NULL,
        non_followbacks_count INTEGER NOT NULL,
        followers_added INTEGER NOT NULL,
        followers_removed INTEGER NOT NULL,
        followees_added INTEGER NOT NULL,
        followees_removed INTEGER NOT NULL,
        prev_run_id INTEGER,
        created_at TEXT NOT NULL,
        duration_seconds INTEGER,
        followers_fetch_seconds INTEGER,
        followees_fetch_seconds INTEGER,
        followers_rate REAL,
        followees_rate REAL,
        confidence_score INTEGER,
        confidence_flag TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_followers (
        run_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        UNIQUE(run_id, username)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_followees (
        run_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        UNIQUE(run_id, username)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schedules (
        id SERIAL PRIMARY KEY,
        login_username TEXT NOT NULL,
        target_username TEXT NOT NULL,
        interval_minutes INTEGER,
        interval TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS unfollow_actions (
        id SERIAL PRIMARY KEY,
        login_username TEXT NOT NULL,
        target_username TEXT NOT NULL,
        username TEXT NOT NULL,
        action TEXT NOT NULL,
        status TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_target_time ON runs(target_username, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_run_followers_run ON run_followers(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_run_followees_run ON run_followees(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_unfollow_login_target ON unfollow_actions(login_username, target_username)",
    "CREATE INDEX IF NOT EXISTS idx_unfollow_created ON unfollow_actions(created_at)",
]


def migrate(sqlite_path: str):
    sconn = sqlite_conn(sqlite_path)
    pconn = pg_conn()
    try:
        with pconn:
            with pconn.cursor() as cur:
                for stmt in DDL:
                    cur.execute(stmt)

        tables = ["config", "runs", "run_followers", "run_followees", "schedules", "unfollow_actions"]
        for table in tables:
            rows = sconn.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                continue
            cols = rows[0].keys()
            col_list = ", ".join(cols)
            placeholders = ", ".join(["%s"] * len(cols))
            insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            with pconn:
                with pconn.cursor() as cur:
                    cur.executemany(insert_sql, [tuple(r) for r in rows])

        # Fix sequences for serial IDs
        with pconn:
            with pconn.cursor() as cur:
                for table in ("runs", "schedules", "unfollow_actions"):
                    cur.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table}), 1), true);"
                    )
    finally:
        sconn.close()
        pconn.close()


if __name__ == "__main__":
    src = _env("INSTALAB_SQLITE_PATH", "/home/stremio/instaloader_data/instaloader.db")
    migrate(src)
    print("Migration complete.")
