from uuid import uuid4

from db import get_db
from tracker_db import rebuild_target_relationship_state, write_run_metadata


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


def test_rebuild_target_relationship_state_recomputes_remaining_runs_after_delete():
    suffix = uuid4().hex[:10]
    target = f"pytest_rebuild_{suffix}"
    login = f"pytest_login_{suffix}"
    t1 = "2026-02-11_10-00-00"
    t2 = "2026-02-11_11-00-00"
    t3 = "2026-02-11_12-00-00"

    conn = get_db()
    try:
        _cleanup_target(conn, target)
    finally:
        conn.close()

    try:
        _, first_run_id = write_run_metadata(
            db_path="/tmp/instalab_runs.db",
            login_username=login,
            target_username=target,
            timestamp=t1,
            followers=["alice", "bob"],
            followees=["xavier", "yuki"],
            non_followbacks_count=2,
        )
        _, second_run_id = write_run_metadata(
            db_path="/tmp/instalab_runs.db",
            login_username=login,
            target_username=target,
            timestamp=t2,
            followers=["bob", "charlie"],
            followees=["xavier", "zara"],
            non_followbacks_count=2,
        )
        _, third_run_id = write_run_metadata(
            db_path="/tmp/instalab_runs.db",
            login_username=login,
            target_username=target,
            timestamp=t3,
            followers=["charlie", "dana"],
            followees=["zara", "quinn"],
            non_followbacks_count=2,
        )

        conn = get_db()
        try:
            conn.execute("DELETE FROM run_followers WHERE run_id = ?", (second_run_id,))
            conn.execute("DELETE FROM run_followees WHERE run_id = ?", (second_run_id,))
            conn.execute("DELETE FROM runs WHERE id = ?", (second_run_id,))
            summary = rebuild_target_relationship_state(conn, target_username=target)
            conn.commit()

            assert summary["runs_seen"] == 2
            assert summary["runs_replayed"] == 2

            third_run = conn.execute(
                """
                SELECT prev_run_id, followers_added, followers_removed, followees_added, followees_removed
                FROM runs
                WHERE id = ?
                """,
                (third_run_id,),
            ).fetchone()
            assert third_run["prev_run_id"] == first_run_id
            assert third_run["followers_added"] == 2
            assert third_run["followers_removed"] == 2
            assert third_run["followees_added"] == 2
            assert third_run["followees_removed"] == 2

            events = {
                (row["run_id"], row["relation_type"], row["event_type"], row["username"])
                for row in conn.execute(
                    """
                    SELECT run_id, relation_type, event_type, username
                    FROM relationship_events
                    WHERE target_username = ?
                    ORDER BY run_id, relation_type, event_type, username
                    """,
                    (target,),
                ).fetchall()
            }
            assert all(run_id != second_run_id for run_id, *_ in events)
            assert (third_run_id, "followers", "added", "dana") in events
            assert (third_run_id, "followers", "added", "charlie") in events
            assert (third_run_id, "followers", "removed", "alice") in events
            assert (third_run_id, "followers", "removed", "bob") in events
            assert (third_run_id, "following", "added", "quinn") in events
            assert (third_run_id, "following", "added", "zara") in events
            assert (third_run_id, "following", "removed", "xavier") in events
            assert (third_run_id, "following", "removed", "yuki") in events
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


def test_incomplete_runs_are_audit_only_and_not_delta_baselines():
    suffix = uuid4().hex[:10]
    target = f"pytest_partial_{suffix}"
    login = f"pytest_login_{suffix}"
    t1 = "2026-02-11_10-00-00"
    t2 = "2026-02-11_11-00-00"
    t3 = "2026-02-11_12-00-00"

    conn = get_db()
    try:
        _cleanup_target(conn, target)
    finally:
        conn.close()

    try:
        _, first_run_id = write_run_metadata(
            db_path="/tmp/instalab_runs.db",
            login_username=login,
            target_username=target,
            timestamp=t1,
            followers=["alice", "bob"],
            followees=["xavier", "yuki"],
            non_followbacks_count=2,
        )
        incomplete_changes, incomplete_run_id = write_run_metadata(
            db_path="/tmp/instalab_runs.db",
            login_username=login,
            target_username=target,
            timestamp=t2,
            followers=["alice", "bob"],
            followees=["xavier"],
            non_followbacks_count=1,
            followees_total_hint=10,
        )
        complete_changes, complete_run_id = write_run_metadata(
            db_path="/tmp/instalab_runs.db",
            login_username=login,
            target_username=target,
            timestamp=t3,
            followers=["bob", "charlie"],
            followees=["xavier", "zara"],
            non_followbacks_count=2,
        )

        assert incomplete_changes["snapshot_complete"] is False
        assert incomplete_changes["relationship_events_recorded"] == 0
        assert complete_changes["snapshot_complete"] is True

        conn = get_db()
        try:
            summary = rebuild_target_relationship_state(conn, target_username=target)
            conn.commit()
            assert summary["runs_seen"] == 3
            assert summary["runs_replayed"] == 2

            incomplete_run = conn.execute(
                """
                SELECT prev_run_id, followers_added, followers_removed, followees_added, followees_removed
                FROM runs
                WHERE id = ?
                """,
                (incomplete_run_id,),
            ).fetchone()
            assert incomplete_run["prev_run_id"] is None
            assert incomplete_run["followers_added"] == 0
            assert incomplete_run["followers_removed"] == 0
            assert incomplete_run["followees_added"] == 0
            assert incomplete_run["followees_removed"] == 0

            complete_run = conn.execute(
                """
                SELECT prev_run_id, followers_added, followers_removed, followees_added, followees_removed
                FROM runs
                WHERE id = ?
                """,
                (complete_run_id,),
            ).fetchone()
            assert complete_run["prev_run_id"] == first_run_id
            assert complete_run["followers_added"] == 1
            assert complete_run["followers_removed"] == 1
            assert complete_run["followees_added"] == 1
            assert complete_run["followees_removed"] == 1

            incomplete_event_count = conn.execute(
                "SELECT COUNT(*) FROM relationship_events WHERE run_id = ?",
                (incomplete_run_id,),
            ).fetchone()[0]
            assert incomplete_event_count == 0
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
