import glob
import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo


def _init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_followers (
            run_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            UNIQUE(run_id, username)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_followees (
            run_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            UNIQUE(run_id, username)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_target_time ON runs(target_username, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_followers_run ON run_followers(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_followees_run ON run_followees(run_id)")


def _parse_target_from_dir(dirname):
    if not dirname.startswith("data_"):
        return dirname
    return dirname.replace("data_", "", 1).replace("_", ".")


def _load_snapshots(data_dir):
    followers_files = glob.glob(os.path.join(data_dir, "followers_*.json"))
    followees_files = glob.glob(os.path.join(data_dir, "followees_*.json"))
    snapshots = {}

    for path in followers_files:
        with open(path, "r") as f:
            data = json.load(f)
        ts = data.get("timestamp") or os.path.basename(path).replace("followers_", "").replace(".json", "")
        snapshots.setdefault(ts, {})["followers"] = data.get("followers", [])

    for path in followees_files:
        with open(path, "r") as f:
            data = json.load(f)
        ts = data.get("timestamp") or os.path.basename(path).replace("followees_", "").replace(".json", "")
        snapshots.setdefault(ts, {})["followees"] = data.get("followees", [])

    return snapshots


def _insert_run(conn, target_username, login_username, timestamp, followers, followees, prev_run_id, prev_followers, prev_followees):
    current_followers = set(followers)
    current_followees = set(followees)

    followers_added = sorted(current_followers - prev_followers)
    followers_removed = sorted(prev_followers - current_followers)
    followees_added = sorted(current_followees - prev_followees)
    followees_removed = sorted(prev_followees - current_followees)

    non_followbacks_count = len(current_followees - current_followers)
    tz = ZoneInfo("America/New_York")
    created_at = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

    conn.execute(
        """
        INSERT INTO runs (
            target_username,
            login_username,
            timestamp,
            followers_count,
            followees_count,
            non_followbacks_count,
            followers_added,
            followers_removed,
            followees_added,
            followees_removed,
            prev_run_id,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_username,
            login_username,
            timestamp,
            len(followers),
            len(followees),
            non_followbacks_count,
            len(followers_added),
            len(followers_removed),
            len(followees_added),
            len(followees_removed),
            prev_run_id,
            created_at,
        ),
    )
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.executemany(
        "INSERT OR IGNORE INTO run_followers (run_id, username) VALUES (?, ?)",
        [(run_id, username) for username in followers],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO run_followees (run_id, username) VALUES (?, ?)",
        [(run_id, username) for username in followees],
    )
    return run_id, current_followers, current_followees


def migrate(base_dir, db_path, default_login="imported"):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        _init_db(conn)
        data_dirs = sorted([d for d in os.listdir(base_dir) if d.startswith("data_")])
        if not data_dirs:
            print("No data_* folders found.")
            return

        for data_dir in data_dirs:
            full_path = os.path.join(base_dir, data_dir)
            if not os.path.isdir(full_path):
                continue
            target_username = _parse_target_from_dir(data_dir)
            snapshots = _load_snapshots(full_path)
            if not snapshots:
                continue

            prev_run_id = None
            prev_followers = set()
            prev_followees = set()
            for timestamp in sorted(snapshots.keys()):
                snap = snapshots[timestamp]
                followers = snap.get("followers") or []
                followees = snap.get("followees") or []
                run_id, prev_followers, prev_followees = _insert_run(
                    conn,
                    target_username,
                    default_login,
                    timestamp,
                    followers,
                    followees,
                    prev_run_id,
                    prev_followers,
                    prev_followees,
                )
                prev_run_id = run_id
            conn.commit()
            print(f"Imported {len(snapshots)} snapshots for @{target_username}")
    finally:
        conn.close()


if __name__ == "__main__":
    base_dir = os.path.abspath(os.path.dirname(__file__))
    db_path = os.path.join(base_dir, "instaloader.db")
    migrate(base_dir, db_path)
    print(f"Migration complete → {db_path}")
