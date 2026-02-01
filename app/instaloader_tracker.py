import os
import random
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from http.cookiejar import MozillaCookieJar
from db import get_db, get_columns, ddl, insert_ignore_sql, is_postgres

import instaloader
from instaloader.exceptions import TwoFactorAuthRequiredException
from tqdm import tqdm
from proxy_utils import load_proxy_from_env


def _ensure_session_dir():
    session_dir = os.path.expanduser("~/.config/instaloader")
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


def _session_file(login_username):
    session_dir = _ensure_session_dir()
    return os.path.join(session_dir, f"session-{login_username.lower()}")


def has_session(login_username):
    return os.path.exists(_session_file(login_username))


def _password_login(loader, login_username, login_password):
    try:
        loader.login(login_username, login_password)
    except TwoFactorAuthRequiredException:
        code = os.getenv("RUN_2FA_CODE") or os.getenv("INSTALAB_2FA_CODE")
        if not code and sys.stdin.isatty():
            code = input("Enter 2FA code: ").strip()
        if not code:
            raise RuntimeError("2FA required; enter the code in the Quick capture 2FA field and retry.")
        loader.two_factor_login(code.strip())


def _cookies_login(loader, cookie_file, login_username=None, login_password=None):
    if not (cookie_file and os.path.exists(cookie_file)):
        return False
    print("Importing browser cookies...")
    cookie_jar = MozillaCookieJar(cookie_file)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    loader.context._session.cookies.update(cookie_jar)
    try:
        if loader.context.test_login():
            print("✓ Cookies are authenticated")
            if login_username and login_password:
                print("Refreshing session with password login...")
                _password_login(loader, login_username, login_password)
            return True
    except (ConnectionError, RuntimeError) as e:
        print(f"Cookie authentication failed: {e}")
    if login_username and login_password:
        print("Cookies not authenticated, falling back to password login...")
        _password_login(loader, login_username, login_password)
        return True
    return False


def login_with_session(loader, login_username, login_password, cookie_file=None):
    session_file = _session_file(login_username)
    print("Attempting to load existing session...")
    try:
        # Pass username explicitly so instaloader doesn't treat the path as a username
        loader.load_session_from_file(login_username, session_file)
        print("✓ Loaded existing session from default path")
        return
    except FileNotFoundError:
        if _cookies_login(loader, cookie_file, login_username, login_password):
            loader.save_session_to_file(session_file)
            print("✓ Session saved to default path")
            return
        print("No session found. Logging in...")
        _password_login(loader, login_username, login_password)
        loader.save_session_to_file(session_file)
        print("✓ Login successful and session saved to default path")
        return
    except Exception as exc:
        print(f"Session load failed: {exc}")
        if _cookies_login(loader, cookie_file, login_username, login_password):
            loader.save_session_to_file(session_file)
            print("✓ Cookies imported and session resaved")
            return
        raise


def fetch_counts(
    *,
    login_username,
    login_password,
    target_username,
    cookie_file=None,
    request_timeout=120.0,
):
    tz = ZoneInfo("America/New_York")
    loader = instaloader.Instaloader(quiet=True, sleep=True, request_timeout=request_timeout)
    proxy = load_proxy_from_env()
    if proxy:
        loader.context._session.proxies.update({"http": proxy["url"], "https": proxy["url"]})
    login_with_session(loader, login_username, login_password, cookie_file=cookie_file)
    profile = instaloader.Profile.from_username(loader.context, target_username)
    timestamp = datetime.now(tz).strftime("%Y-%m-%d_%H-%M-%S")
    return {
        "timestamp": timestamp,
        "followers_count": int(getattr(profile, "followers", 0) or 0),
        "followees_count": int(getattr(profile, "followees", 0) or 0),
    }



def fetch_user_list(profile, list_name, desc, cancel_check=None, progress=None, item_delay_min=0.0, item_delay_max=0.0):
    fetcher = profile.get_followers if list_name == "followers" else profile.get_followees
    items_iter = fetcher()
    items = []
    delay_min = max(0.0, float(item_delay_min or 0))
    delay_max = max(0.0, float(item_delay_max or 0))
    if delay_max and delay_min > delay_max:
        delay_min, delay_max = delay_max, delay_min
    for item in tqdm(items_iter, desc=desc, unit=" accounts"):
        if cancel_check and cancel_check():
            raise RuntimeError("cancelled")
        items.append(item.username)
        if progress:
            try:
                progress(len(items))
            except Exception:
                pass
        if delay_max or delay_min:
            wait = random.uniform(delay_min, max(delay_min, delay_max))
            if wait > 0:
                time.sleep(wait)
    return sorted(items)


def snapshot_profile(
    *,
    login_username,
    login_password,
    target_username,
    cookie_file=None,
    request_timeout=600.0,
    db_path=None,
    cancel_check=None,
    progress=None,
    item_delay_min=0.0,
    item_delay_max=0.0,
):
    tz = ZoneInfo("America/New_York")
    loader = instaloader.Instaloader(quiet=False, sleep=True, request_timeout=request_timeout)
    proxy = load_proxy_from_env()
    if proxy:
        loader.context._session.proxies.update({"http": proxy["url"], "https": proxy["url"]})
    login_with_session(loader, login_username, login_password, cookie_file=cookie_file)

    print(f"Loading profile @{target_username}...")
    profile = instaloader.Profile.from_username(loader.context, target_username)
    if progress:
        try:
            progress("totals", {
                "followers_total": int(getattr(profile, "followers", 0) or 0),
                "following_total": int(getattr(profile, "followees", 0) or 0),
            })
        except Exception:
            pass

    print("Fetching followers...")
    t0 = time.time()
    if progress:
        try:
            progress("followers", 0)
        except Exception:
            pass
    followers = fetch_user_list(
        profile,
        "followers",
        "Followers",
        cancel_check=cancel_check,
        progress=(lambda c: progress("followers", c)) if progress else None,
        item_delay_min=item_delay_min,
        item_delay_max=item_delay_max,
    )
    followers_fetch_seconds = int(time.time() - t0)

    print("Fetching followees...")
    t1 = time.time()
    if progress:
        try:
            progress("following", 0)
        except Exception:
            pass
    followees = fetch_user_list(
        profile,
        "followees",
        "Followees",
        cancel_check=cancel_check,
        progress=(lambda c: progress("following", c)) if progress else None,
        item_delay_min=item_delay_min,
        item_delay_max=item_delay_max,
    )
    followees_fetch_seconds = int(time.time() - t1)

    timestamp = datetime.now(tz).strftime("%Y-%m-%d_%H-%M-%S")
    non_followbacks = sorted(set(followees) - set(followers))

    changes = None
    run_id = None
    followers_rate = round(len(followers) / followers_fetch_seconds, 3) if followers_fetch_seconds else None
    followees_rate = round(len(followees) / followees_fetch_seconds, 3) if followees_fetch_seconds else None

    if db_path:
        # Normalize db path to avoid empty dirname errors
        db_path = os.path.abspath(db_path)
        changes, run_id = write_run_metadata(
            db_path=db_path,
            login_username=login_username,
            target_username=target_username,
            timestamp=timestamp,
            followers=followers,
            followees=followees,
            non_followbacks_count=len(non_followbacks),
            followers_fetch_seconds=followers_fetch_seconds,
            followees_fetch_seconds=followees_fetch_seconds,
            followers_rate=followers_rate,
            followees_rate=followees_rate,
        )

    return {
        "timestamp": timestamp,
        "followers_count": len(followers),
        "followees_count": len(followees),
        "followers": followers,
        "followees": followees,
        "non_followbacks": non_followbacks,
        "followers_fetch_seconds": followers_fetch_seconds,
        "followees_fetch_seconds": followees_fetch_seconds,
        "followers_rate": followers_rate,
        "followees_rate": followees_rate,
        "changes": changes,
        "run_id": run_id,
    }


def _init_db(conn):
    conn.execute(
        ddl(
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
                created_at TEXT NOT NULL,
                duration_seconds INTEGER
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
                duration_seconds INTEGER
            )
            """,
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_target_time ON runs(target_username, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_followers_run ON run_followers(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_followees_run ON run_followees(run_id)")
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

        current_followers = set(followers)
        current_followees = set(followees)

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
                followees_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
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
            followers_fetch_seconds,
            followees_fetch_seconds,
            followers_rate,
            followees_rate,
        )
        if is_postgres():
            cur = conn.execute(insert_sql + " RETURNING id", params)
            run_id = cur.fetchone()[0]
        else:
            conn.execute(insert_sql, params)
            run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.executemany(
            insert_ignore_sql("run_followers", ["run_id", "username"]),
            [(run_id, username) for username in followers],
        )
        conn.executemany(
            insert_ignore_sql("run_followees", ["run_id", "username"]),
            [(run_id, username) for username in followees],
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
        },
        run_id,
    )


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
