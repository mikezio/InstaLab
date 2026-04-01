import os
from datetime import datetime
from zoneinfo import ZoneInfo

from db import get_db, get_columns, ddl, insert_ignore_sql, is_postgres


def _init_db(conn):
    conn.execute(
        ddl(
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
                followers_collected_count INTEGER,
                followees_collected_count INTEGER,
                snapshot_complete INTEGER NOT NULL DEFAULT 1,
                snapshot_note TEXT
            )
            """
        )
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS followers_history (
            target_username TEXT NOT NULL,
            username TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            first_seen_run_id INTEGER,
            last_seen_run_id INTEGER,
            first_seen_known INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            unfollowed_at TEXT,
            UNIQUE(target_username, username)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS followees_history (
            target_username TEXT NOT NULL,
            username TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            first_seen_run_id INTEGER,
            last_seen_run_id INTEGER,
            first_seen_known INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            unfollowed_at TEXT,
            UNIQUE(target_username, username)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relationship_events (
            id SERIAL PRIMARY KEY,
            target_username TEXT NOT NULL,
            login_username TEXT NOT NULL,
            username TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            event_type TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            run_id INTEGER NOT NULL,
            prev_run_id INTEGER,
            UNIQUE(run_id, relation_type, event_type, username)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS count_watch_samples (
            id SERIAL PRIMARY KEY,
            target_username TEXT NOT NULL,
            login_username TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            followers_count INTEGER NOT NULL,
            followees_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            triggered_full_run INTEGER NOT NULL DEFAULT 0,
            triggered_run_id INTEGER,
            schedule_id INTEGER,
            trigger_delta INTEGER
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_target_time ON runs(target_username, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_followers_run ON run_followers(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_followees_run ON run_followees(run_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_count_watch_target_time ON count_watch_samples(target_username, timestamp)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_count_watch_schedule_time ON count_watch_samples(schedule_id, timestamp)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_followers_hist_target_active ON followers_history(target_username, active)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_followers_hist_target_first ON followers_history(target_username, first_seen)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_followees_hist_target_active ON followees_history(target_username, active)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_followees_hist_target_first ON followees_history(target_username, first_seen)"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_relationship_events_target_time
        ON relationship_events(target_username, relation_type, observed_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_relationship_events_user_time
        ON relationship_events(target_username, relation_type, username, observed_at)
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relationship_events_run ON relationship_events(run_id)")
    cols = get_columns(conn, "followers_history")
    if "first_seen_known" not in cols:
        conn.execute("ALTER TABLE followers_history ADD COLUMN first_seen_known INTEGER DEFAULT 1")
    cols = get_columns(conn, "followees_history")
    if "first_seen_known" not in cols:
        conn.execute("ALTER TABLE followees_history ADD COLUMN first_seen_known INTEGER DEFAULT 1")
    cols = get_columns(conn, "runs")
    if "duration_seconds" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN duration_seconds INTEGER")
    if "followers_fetch_seconds" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN followers_fetch_seconds INTEGER")
    if "followees_fetch_seconds" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN followees_fetch_seconds INTEGER")
    if "followers_rate" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN followers_rate REAL")
    if "followees_rate" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN followees_rate REAL")
    if "confidence_score" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN confidence_score INTEGER")
    if "confidence_flag" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN confidence_flag TEXT")
    if "followers_collected_count" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN followers_collected_count INTEGER")
    if "followees_collected_count" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN followees_collected_count INTEGER")
    if "snapshot_complete" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN snapshot_complete INTEGER NOT NULL DEFAULT 1")
    if "snapshot_note" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN snapshot_note TEXT")


def _update_history_table(
    conn,
    *,
    table,
    target_username,
    run_id,
    timestamp,
    added,
    removed,
    first_seen_known=1,
):
    add_rows = []
    for username in added:
        cur = conn.execute(
            f"""
            UPDATE {table}
            SET active = 1,
                last_seen = ?,
                last_seen_run_id = ?,
                unfollowed_at = NULL,
                first_seen = CASE WHEN active = 0 THEN ? ELSE first_seen END,
                first_seen_run_id = CASE WHEN active = 0 THEN ? ELSE first_seen_run_id END,
                first_seen_known = CASE WHEN active = 0 THEN ? ELSE first_seen_known END
            WHERE target_username = ? AND username = ?
            """,
            (timestamp, run_id, timestamp, run_id, first_seen_known, target_username, username),
        )
        if cur.rowcount == 0:
            add_rows.append(
                (
                    target_username,
                    username,
                    timestamp,
                    timestamp,
                    run_id,
                    run_id,
                    1,
                    None,
                    first_seen_known,
                )
            )
    if add_rows:
        conn.executemany(
            insert_ignore_sql(
                table,
                [
                    "target_username",
                    "username",
                    "first_seen",
                    "last_seen",
                    "first_seen_run_id",
                    "last_seen_run_id",
                    "active",
                    "unfollowed_at",
                    "first_seen_known",
                ],
            ),
            add_rows,
        )
    for username in removed:
        conn.execute(
            f"""
            UPDATE {table}
            SET active = 0,
                last_seen = ?,
                last_seen_run_id = ?,
                unfollowed_at = ?
            WHERE target_username = ? AND username = ?
            """,
            (timestamp, run_id, timestamp, target_username, username),
        )


def _get_previous_run(conn, target_username):
    cur = conn.execute(
        """
        SELECT id, timestamp
        FROM runs
        WHERE target_username = ?
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
        """,
        (target_username,),
    )
    row = cur.fetchone()
    if not row:
        return None
    run_id, timestamp = row
    prev_followers = {
        r[0] for r in conn.execute("SELECT username FROM run_followers WHERE run_id = ?", (run_id,))
    }
    prev_followees = {
        r[0] for r in conn.execute("SELECT username FROM run_followees WHERE run_id = ?", (run_id,))
    }
    return run_id, timestamp, prev_followers, prev_followees


def _get_run_members(conn, run_id):
    followers = {
        r[0] for r in conn.execute("SELECT username FROM run_followers WHERE run_id = ?", (run_id,))
    }
    followees = {
        r[0] for r in conn.execute("SELECT username FROM run_followees WHERE run_id = ?", (run_id,))
    }
    return followers, followees


def _insert_relationship_events(
    conn,
    *,
    target_username,
    login_username,
    run_id,
    prev_run_id,
    timestamp,
    followers_added,
    followers_removed,
    followees_added,
    followees_removed,
):
    rows = []
    rows.extend(
        (
            target_username,
            login_username,
            username,
            "followers",
            "added",
            timestamp,
            run_id,
            prev_run_id,
        )
        for username in followers_added
    )
    rows.extend(
        (
            target_username,
            login_username,
            username,
            "followers",
            "removed",
            timestamp,
            run_id,
            prev_run_id,
        )
        for username in followers_removed
    )
    rows.extend(
        (
            target_username,
            login_username,
            username,
            "following",
            "added",
            timestamp,
            run_id,
            prev_run_id,
        )
        for username in followees_added
    )
    rows.extend(
        (
            target_username,
            login_username,
            username,
            "following",
            "removed",
            timestamp,
            run_id,
            prev_run_id,
        )
        for username in followees_removed
    )
    if not rows:
        return 0
    conn.executemany(
        insert_ignore_sql(
            "relationship_events",
            [
                "target_username",
                "login_username",
                "username",
                "relation_type",
                "event_type",
                "observed_at",
                "run_id",
                "prev_run_id",
            ],
        ),
        rows,
    )
    return len(rows)


def backfill_relationship_events(conn):
    _init_db(conn)
    existing_count = conn.execute("SELECT COUNT(*) FROM relationship_events").fetchone()[0]
    if int(existing_count or 0) > 0:
        return 0
    runs = conn.execute(
        """
        SELECT id, target_username, login_username, timestamp
        FROM runs
        ORDER BY target_username ASC, timestamp ASC, id ASC
        """
    ).fetchall()
    if not runs:
        return 0
    created = 0
    prev_by_target = {}
    for run in runs:
        run_id = run["id"]
        target_username = run["target_username"]
        login_username = run["login_username"]
        timestamp = run["timestamp"]
        followers, followees = _get_run_members(conn, run_id)
        prev = prev_by_target.get(target_username)
        if prev is None:
            prev_followers = followers
            prev_followees = followees
            prev_run_id = None
        else:
            prev_followers = prev["followers"]
            prev_followees = prev["followees"]
            prev_run_id = prev["run_id"]
        created += _insert_relationship_events(
            conn,
            target_username=target_username,
            login_username=login_username,
            run_id=run_id,
            prev_run_id=prev_run_id,
            timestamp=timestamp,
            followers_added=sorted(followers - prev_followers),
            followers_removed=sorted(prev_followers - followers),
            followees_added=sorted(followees - prev_followees),
            followees_removed=sorted(prev_followees - followees),
        )
        prev_by_target[target_username] = {
            "run_id": run_id,
            "followers": followers,
            "followees": followees,
        }
    return created


def rebuild_target_relationship_state(conn, *, target_username):
    _init_db(conn)
    target_username = str(target_username or "").strip()
    if not target_username:
        return {
            "target_username": "",
            "runs_seen": 0,
            "runs_replayed": 0,
            "events_rebuilt": 0,
        }

    conn.execute("DELETE FROM relationship_events WHERE target_username = ?", (target_username,))
    conn.execute("DELETE FROM followers_history WHERE target_username = ?", (target_username,))
    conn.execute("DELETE FROM followees_history WHERE target_username = ?", (target_username,))

    runs = conn.execute(
        """
        SELECT id, target_username, login_username, timestamp,
               followers_count, followees_count, non_followbacks_count, snapshot_note
        FROM runs
        WHERE target_username = ?
        ORDER BY timestamp ASC, id ASC
        """,
        (target_username,),
    ).fetchall()
    if not runs:
        return {
            "target_username": target_username,
            "runs_seen": 0,
            "runs_replayed": 0,
            "events_rebuilt": 0,
        }

    prev = None
    events_rebuilt = 0
    runs_replayed = 0
    for run in runs:
        run_id = run["id"]
        followers, followees = _get_run_members(conn, run_id)
        snapshot_note = str(run["snapshot_note"] or "").strip().lower()
        has_member_snapshot = bool(followers or followees)
        if not has_member_snapshot:
            # Count/profile-only snapshots do not have member lists, so they cannot
            # safely drive relationship history or delta recalculation.
            if snapshot_note:
                continue
            if int(run["followers_count"] or 0) > 0 or int(run["followees_count"] or 0) > 0:
                continue

        if prev is None:
            prev_run_id = None
            prev_followers = followers
            prev_followees = followees
            followers_added = []
            followers_removed = []
            followees_added = []
            followees_removed = []
            first_seen_known = 0
        else:
            prev_run_id = prev["run_id"]
            prev_followers = prev["followers"]
            prev_followees = prev["followees"]
            followers_added = sorted(followers - prev_followers)
            followers_removed = sorted(prev_followers - followers)
            followees_added = sorted(followees - prev_followees)
            followees_removed = sorted(prev_followees - followees)
            first_seen_known = 1

        conn.execute(
            """
            UPDATE runs
            SET prev_run_id = ?,
                followers_added = ?,
                followers_removed = ?,
                followees_added = ?,
                followees_removed = ?,
                non_followbacks_count = ?
            WHERE id = ?
            """,
            (
                prev_run_id,
                len(followers_added),
                len(followers_removed),
                len(followees_added),
                len(followees_removed),
                len(followees - followers),
                run_id,
            ),
        )
        events_rebuilt += _insert_relationship_events(
            conn,
            target_username=target_username,
            login_username=run["login_username"],
            run_id=run_id,
            prev_run_id=prev_run_id,
            timestamp=run["timestamp"],
            followers_added=followers_added,
            followers_removed=followers_removed,
            followees_added=followees_added,
            followees_removed=followees_removed,
        )
        _update_history_table(
            conn,
            table="followers_history",
            target_username=target_username,
            run_id=run_id,
            timestamp=run["timestamp"],
            added=sorted(followers if prev is None else followers_added),
            removed=[] if prev is None else followers_removed,
            first_seen_known=first_seen_known,
        )
        _update_history_table(
            conn,
            table="followees_history",
            target_username=target_username,
            run_id=run_id,
            timestamp=run["timestamp"],
            added=sorted(followees if prev is None else followees_added),
            removed=[] if prev is None else followees_removed,
            first_seen_known=first_seen_known,
        )
        prev = {
            "run_id": run_id,
            "followers": followers,
            "followees": followees,
        }
        runs_replayed += 1

    return {
        "target_username": target_username,
        "runs_seen": len(runs),
        "runs_replayed": runs_replayed,
        "events_rebuilt": events_rebuilt,
    }


def write_run_metadata(
    *,
    db_path,
    login_username,
    target_username,
    timestamp,
    followers,
    followees,
    non_followbacks_count,
    followers_fetch_seconds=None,
    followees_fetch_seconds=None,
    followers_rate=None,
    followees_rate=None,
    followers_total_hint=None,
    followees_total_hint=None,
    snapshot_note=None,
):
    tz = ZoneInfo("America/New_York")
    if not is_postgres():
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = get_db()
    try:
        # Improve concurrent access behavior for parallel runs.
        if not is_postgres():
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        _init_db(conn)
        prev = _get_previous_run(conn, target_username)
        prev_run_id = prev[0] if prev else None
        prev_timestamp = prev[1] if prev else None
        prev_followers = prev[2] if prev else set()
        prev_followees = prev[3] if prev else set()

        raw_followers = [str(u or "").strip().lower() for u in (followers or []) if str(u or "").strip()]
        raw_followees = [str(u or "").strip().lower() for u in (followees or []) if str(u or "").strip()]
        current_followers = set(raw_followers)
        current_followees = set(raw_followees)
        guardrail_notes = []
        if len(current_followers) != len(raw_followers):
            guardrail_notes.append("followers_deduped")
        if len(current_followees) != len(raw_followees):
            guardrail_notes.append("followees_deduped")
        if prev_timestamp and str(timestamp) <= str(prev_timestamp):
            guardrail_notes.append("non_monotonic_timestamp")
        if followers_total_hint is not None and int(followers_total_hint) > len(current_followers):
            guardrail_notes.append("followers_partial_collection")
        if followees_total_hint is not None and int(followees_total_hint) > len(current_followees):
            guardrail_notes.append("followees_partial_collection")
        if snapshot_note:
            guardrail_notes.append(str(snapshot_note))

        # If this is the first run for this target, treat it as baseline only.
        if prev_run_id is None:
            prev_followers = current_followers
            prev_followees = current_followees

        followers_added = sorted(current_followers - prev_followers)
        followers_removed = sorted(prev_followers - current_followers)
        followees_added = sorted(current_followees - prev_followees)
        followees_removed = sorted(prev_followees - current_followees)

        created_at = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        insert_sql = """
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
                created_at,
                followers_fetch_seconds,
                followees_fetch_seconds,
                followers_rate,
                followees_rate,
                followers_collected_count,
                followees_collected_count,
                snapshot_complete,
                snapshot_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        snapshot_complete = 0 if guardrail_notes else 1
        params = (
            target_username,
            login_username,
            timestamp,
            len(current_followers),
            len(current_followees),
            non_followbacks_count,
            len(followers_added),
            len(followers_removed),
            len(followees_added),
            len(followees_removed),
            prev_run_id,
            created_at,
            followers_fetch_seconds,
            followees_fetch_seconds,
            followers_rate,
            followees_rate,
            len(current_followers),
            len(current_followees),
            snapshot_complete,
            ";".join(guardrail_notes) if guardrail_notes else None,
        )
        if is_postgres():
            cur = conn.execute(insert_sql + " RETURNING id", params)
            run_id = cur.fetchone()[0]
        else:
            conn.execute(insert_sql, params)
            run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.executemany(
            insert_ignore_sql("run_followers", ["run_id", "username"]),
            [(run_id, username) for username in sorted(current_followers)],
        )
        conn.executemany(
            insert_ignore_sql("run_followees", ["run_id", "username"]),
            [(run_id, username) for username in sorted(current_followees)],
        )
        relationship_events_recorded = _insert_relationship_events(
            conn,
            target_username=target_username,
            login_username=login_username,
            run_id=run_id,
            prev_run_id=prev_run_id,
            timestamp=timestamp,
            followers_added=followers_added,
            followers_removed=followers_removed,
            followees_added=followees_added,
            followees_removed=followees_removed,
        )
        if prev_run_id is None:
            _update_history_table(
                conn,
                table="followers_history",
                target_username=target_username,
                run_id=run_id,
                timestamp=timestamp,
                added=sorted(current_followers),
                removed=[],
                first_seen_known=0,
            )
            _update_history_table(
                conn,
                table="followees_history",
                target_username=target_username,
                run_id=run_id,
                timestamp=timestamp,
                added=sorted(current_followees),
                removed=[],
                first_seen_known=0,
            )
        else:
            _update_history_table(
                conn,
                table="followers_history",
                target_username=target_username,
                run_id=run_id,
                timestamp=timestamp,
                added=followers_added,
                removed=followers_removed,
                first_seen_known=1,
            )
            _update_history_table(
                conn,
                table="followees_history",
                target_username=target_username,
                run_id=run_id,
                timestamp=timestamp,
                added=followees_added,
                removed=followees_removed,
                first_seen_known=1,
            )
        conn.commit()
    finally:
        conn.close()

    return (
        {
            "previous_timestamp": prev_timestamp,
            "followers": {"added": followers_added, "removed": followers_removed},
            "followees": {"added": followees_added, "removed": followees_removed},
            "relationship_events_recorded": relationship_events_recorded,
            "snapshot_complete": bool(snapshot_complete),
            "snapshot_note": ";".join(guardrail_notes) if guardrail_notes else None,
        },
        run_id,
    )


def write_run_profile_counts(
    *,
    db_path,
    login_username,
    target_username,
    timestamp,
    followers_count,
    followees_count,
):
    tz = ZoneInfo("America/New_York")
    if not is_postgres():
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = get_db()
    try:
        if not is_postgres():
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        _init_db(conn)
        prev = _get_previous_run(conn, target_username)
        prev_run_id = prev[0] if prev else None

        created_at = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        insert_sql = """
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
                created_at,
                followers_fetch_seconds,
                followees_fetch_seconds,
                followers_rate,
                followees_rate,
                followers_collected_count,
                followees_collected_count,
                snapshot_complete,
                snapshot_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            target_username,
            login_username,
            timestamp,
            int(followers_count),
            int(followees_count),
            0,
            0,
            0,
            0,
            0,
            prev_run_id,
            created_at,
            0,
            0,
            None,
            None,
            int(followers_count),
            int(followees_count),
            0,
            "profile_only",
        )
        if is_postgres():
            cur = conn.execute(insert_sql + " RETURNING id", params)
            run_id = cur.fetchone()[0]
        else:
            conn.execute(insert_sql, params)
            run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    return (
        {
            "previous_timestamp": None,
            "followers": {"added": [], "removed": []},
            "followees": {"added": [], "removed": []},
            "snapshot_complete": False,
            "snapshot_note": "profile_only",
        },
        run_id,
    )


def get_latest_count_watch_sample(*, target_username):
    samples = get_recent_count_watch_samples(target_username=target_username, limit=1)
    return samples[0] if samples else None


def get_recent_count_watch_samples(*, target_username, limit=3):
    conn = get_db()
    try:
        if not is_postgres():
            conn.execute("PRAGMA busy_timeout=30000")
        _init_db(conn)
        rows = conn.execute(
            """
            SELECT id, timestamp, followers_count, followees_count,
                   triggered_full_run, triggered_run_id, schedule_id, trigger_delta
            FROM count_watch_samples
            WHERE target_username = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (target_username, max(1, int(limit or 1))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_recent_complete_run_totals(*, target_username, limit=5):
    conn = get_db()
    try:
        if not is_postgres():
            conn.execute("PRAGMA busy_timeout=30000")
        _init_db(conn)
        rows = conn.execute(
            """
            SELECT id, timestamp, followers_count, followees_count,
                   followers_collected_count, followees_collected_count,
                   snapshot_complete, snapshot_note
            FROM runs
            WHERE target_username = ?
              AND COALESCE(snapshot_complete, 1) = 1
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (target_username, max(1, int(limit or 1))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def write_count_watch_sample(
    *,
    login_username,
    target_username,
    timestamp,
    followers_count,
    followees_count,
    triggered_full_run=False,
    triggered_run_id=None,
    schedule_id=None,
    trigger_delta=None,
):
    tz = ZoneInfo("America/New_York")
    conn = get_db()
    try:
        if not is_postgres():
            conn.execute("PRAGMA busy_timeout=30000")
        _init_db(conn)
        created_at = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        insert_sql = """
            INSERT INTO count_watch_samples (
                target_username,
                login_username,
                timestamp,
                followers_count,
                followees_count,
                created_at,
                triggered_full_run,
                triggered_run_id,
                schedule_id,
                trigger_delta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            target_username,
            login_username,
            timestamp,
            int(followers_count),
            int(followees_count),
            created_at,
            1 if triggered_full_run else 0,
            triggered_run_id,
            schedule_id,
            int(trigger_delta) if trigger_delta is not None else None,
        )
        if is_postgres():
            cur = conn.execute(insert_sql + " RETURNING id", params)
            sample_id = cur.fetchone()[0]
        else:
            conn.execute(insert_sql, params)
            sample_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return int(sample_id)
    finally:
        conn.close()


def update_run_duration(db_path, run_id, duration_seconds):
    if not run_id:
        return
    if not is_postgres():
        db_path = os.path.abspath(db_path)
    conn = get_db()
    try:
        if not is_postgres():
            conn.execute("PRAGMA busy_timeout=30000")
        _init_db(conn)
        conn.execute(
            "UPDATE runs SET duration_seconds = ? WHERE id = ?",
            (int(duration_seconds), int(run_id)),
        )
        conn.commit()
    finally:
        conn.close()
