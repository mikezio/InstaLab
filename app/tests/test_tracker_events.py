from uuid import uuid4

from db import get_db
from tracker_db import write_run_metadata


def _cleanup_target(conn, target_username: str) -> None:
    run_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM runs WHERE target_username = ?",
            (target_username,),
        ).fetchall()
    ]
    if run_ids:
        placeholders = ",".join(["?"] * len(run_ids))
        conn.execute(f"DELETE FROM run_followers WHERE run_id IN ({placeholders})", tuple(run_ids))
        conn.execute(f"DELETE FROM run_followees WHERE run_id IN ({placeholders})", tuple(run_ids))
        conn.execute(f"DELETE FROM relationship_events WHERE run_id IN ({placeholders})", tuple(run_ids))
    conn.execute("DELETE FROM followers_history WHERE target_username = ?", (target_username,))
    conn.execute("DELETE FROM followees_history WHERE target_username = ?", (target_username,))
    conn.execute("DELETE FROM runs WHERE target_username = ?", (target_username,))
    conn.commit()


def test_write_run_metadata_records_bidirectional_relationship_events_and_history():
    suffix = uuid4().hex[:10]
    target = f"pytest_target_{suffix}"
    login = f"pytest_login_{suffix}"
    t1 = "2026-02-11_10-00-00"
    t2 = "2026-02-11_11-00-00"

    conn = get_db()
    try:
        _cleanup_target(conn, target)
    finally:
        conn.close()

    try:
        first_changes, first_run_id = write_run_metadata(
            db_path="/tmp/instalab_runs.db",
            login_username=login,
            target_username=target,
            timestamp=t1,
            followers=["alice", "bob"],
            followees=["xavier", "yuki"],
            non_followbacks_count=2,
        )
        assert first_changes["relationship_events_recorded"] == 0

        second_changes, second_run_id = write_run_metadata(
            db_path="/tmp/instalab_runs.db",
            login_username=login,
            target_username=target,
            timestamp=t2,
            followers=["bob", "charlie"],
            followees=["xavier", "zara"],
            non_followbacks_count=2,
        )
        assert second_changes["relationship_events_recorded"] == 4

        conn = get_db()
        try:
            baseline_events = conn.execute(
                "SELECT COUNT(*) FROM relationship_events WHERE run_id = ?",
                (first_run_id,),
            ).fetchone()[0]
            assert baseline_events == 0

            events = {
                (row["relation_type"], row["event_type"], row["username"], row["observed_at"])
                for row in conn.execute(
                    """
                    SELECT relation_type, event_type, username, observed_at
                    FROM relationship_events
                    WHERE run_id = ?
                    """,
                    (second_run_id,),
                ).fetchall()
            }
            assert events == {
                ("followers", "added", "charlie", t2),
                ("followers", "removed", "alice", t2),
                ("following", "added", "zara", t2),
                ("following", "removed", "yuki", t2),
            }

            alice = conn.execute(
                """
                SELECT first_seen, unfollowed_at, active, first_seen_known
                FROM followers_history
                WHERE target_username = ? AND username = 'alice'
                """,
                (target,),
            ).fetchone()
            assert alice["first_seen"] == t1
            assert alice["unfollowed_at"] == t2
            assert alice["active"] == 0
            assert alice["first_seen_known"] == 0

            charlie = conn.execute(
                """
                SELECT first_seen, unfollowed_at, active, first_seen_known
                FROM followers_history
                WHERE target_username = ? AND username = 'charlie'
                """,
                (target,),
            ).fetchone()
            assert charlie["first_seen"] == t2
            assert charlie["unfollowed_at"] is None
            assert charlie["active"] == 1
            assert charlie["first_seen_known"] == 1

            yuki = conn.execute(
                """
                SELECT first_seen, unfollowed_at, active
                FROM followees_history
                WHERE target_username = ? AND username = 'yuki'
                """,
                (target,),
            ).fetchone()
            assert yuki["first_seen"] == t1
            assert yuki["unfollowed_at"] == t2
            assert yuki["active"] == 0

            zara = conn.execute(
                """
                SELECT first_seen, unfollowed_at, active, first_seen_known
                FROM followees_history
                WHERE target_username = ? AND username = 'zara'
                """,
                (target,),
            ).fetchone()
            assert zara["first_seen"] == t2
            assert zara["unfollowed_at"] is None
            assert zara["active"] == 1
            assert zara["first_seen_known"] == 1
        finally:
            _cleanup_target(conn, target)
            conn.close()
    except Exception:
        conn = get_db()
        try:
            _cleanup_target(conn, target)
        finally:
            conn.close()
        raise
