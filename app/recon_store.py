import json
from datetime import datetime, timezone

from db import ddl


CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value) -> str:
    return json.dumps(value or {}, separators=(",", ":"))


def init_recon_tables(conn) -> None:
    conn.execute(
        ddl(
            """
            CREATE TABLE IF NOT EXISTS recon_queries (
                id BIGSERIAL PRIMARY KEY,
                mode TEXT NOT NULL,
                query_value TEXT NOT NULL,
                requested_by TEXT,
                source_tool TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
    )
    conn.execute(
        ddl(
            """
            CREATE TABLE IF NOT EXISTS recon_jobs (
                id TEXT PRIMARY KEY,
                recon_query_id BIGINT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                duration_seconds INTEGER,
                command_fingerprint TEXT,
                error_message TEXT,
                raw_output_path TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (recon_query_id) REFERENCES recon_queries(id)
            )
            """
        )
    )
    conn.execute(
        ddl(
            """
            CREATE TABLE IF NOT EXISTS recon_findings (
                id BIGSERIAL PRIMARY KEY,
                recon_job_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                url TEXT,
                category TEXT,
                tool_status TEXT,
                confidence_tier TEXT NOT NULL,
                evidence_json JSONB,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY (recon_job_id) REFERENCES recon_jobs(id)
            )
            """
        )
    )
    conn.execute(
        ddl(
            """
            CREATE TABLE IF NOT EXISTS recon_artifacts (
                id BIGSERIAL PRIMARY KEY,
                recon_job_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                path TEXT NOT NULL,
                size_bytes BIGINT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (recon_job_id) REFERENCES recon_jobs(id)
            )
            """
        )
    )
    conn.execute(
        ddl("CREATE INDEX IF NOT EXISTS idx_recon_jobs_status_created ON recon_jobs(status, created_at)")
    )
    conn.execute(
        ddl("CREATE INDEX IF NOT EXISTS idx_recon_findings_job ON recon_findings(recon_job_id)")
    )
    conn.execute(
        ddl("CREATE INDEX IF NOT EXISTS idx_recon_queries_mode_created ON recon_queries(mode, created_at)")
    )


def create_recon_query(conn, *, mode: str, query_value: str, requested_by: str | None, source_tool: str) -> int:
    now = _utc_now()
    cur = conn.execute(
        """
        INSERT INTO recon_queries (mode, query_value, requested_by, source_tool, created_at)
        VALUES (?, ?, ?, ?, ?)
        RETURNING id
        """,
        (mode, query_value, requested_by, source_tool, now),
    )
    row = cur.fetchone()
    return int(row[0])


def create_recon_job(
    conn,
    *,
    job_id: str,
    recon_query_id: int,
    status: str,
    command_fingerprint: str | None = None,
) -> None:
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO recon_jobs (
            id, recon_query_id, status, command_fingerprint, created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (job_id, recon_query_id, status, command_fingerprint, now),
    )


def mark_recon_job_running(conn, *, job_id: str) -> None:
    conn.execute(
        "UPDATE recon_jobs SET status = 'running', started_at = ? WHERE id = ?",
        (_utc_now(), job_id),
    )


def finalize_recon_job(
    conn,
    *,
    job_id: str,
    status: str,
    duration_seconds: int | None,
    raw_output_path: str | None,
    error_message: str | None,
    findings: list[dict],
    artifacts: list[dict],
) -> None:
    finished = _utc_now()
    conn.execute(
        """
        UPDATE recon_jobs
        SET status = ?, finished_at = ?, duration_seconds = ?, raw_output_path = ?, error_message = ?
        WHERE id = ?
        """,
        (status, finished, duration_seconds, raw_output_path, error_message, job_id),
    )

    conn.execute("DELETE FROM recon_findings WHERE recon_job_id = ?", (job_id,))
    conn.execute("DELETE FROM recon_artifacts WHERE recon_job_id = ?", (job_id,))

    for item in findings or []:
        platform = str(item.get("platform") or "unknown")
        url = item.get("url")
        category = item.get("category")
        tool_status = item.get("tool_status")
        confidence = str(item.get("confidence_tier") or CONFIDENCE_LOW)
        evidence = item.get("evidence") or {}
        conn.execute(
            """
            INSERT INTO recon_findings (
                recon_job_id, platform, url, category, tool_status, confidence_tier,
                evidence_json, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?)
            """,
            (job_id, platform, url, category, tool_status, confidence, _json_dump(evidence), finished, finished),
        )

    for artifact in artifacts or []:
        path = str(artifact.get("path") or "")
        if not path:
            continue
        conn.execute(
            """
            INSERT INTO recon_artifacts (recon_job_id, artifact_type, path, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                job_id,
                str(artifact.get("artifact_type") or "unknown"),
                path,
                artifact.get("size_bytes"),
                finished,
            ),
        )


def delete_recon_job(conn, *, job_id: str) -> bool:
    row = conn.execute("SELECT recon_query_id FROM recon_jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return False
    recon_query_id = int(row[0])
    conn.execute("DELETE FROM recon_findings WHERE recon_job_id = ?", (job_id,))
    conn.execute("DELETE FROM recon_artifacts WHERE recon_job_id = ?", (job_id,))
    conn.execute("DELETE FROM recon_jobs WHERE id = ?", (job_id,))
    conn.execute(
        """
        DELETE FROM recon_queries
        WHERE id = ?
          AND NOT EXISTS (
              SELECT 1
              FROM recon_jobs
              WHERE recon_query_id = ?
          )
        """,
        (recon_query_id, recon_query_id),
    )
    return True


def get_recon_job(conn, job_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT rj.id, rj.recon_query_id, rj.status, rj.started_at, rj.finished_at,
               rj.duration_seconds, rj.command_fingerprint, rj.error_message,
               rj.raw_output_path, rj.created_at,
               rq.mode, rq.query_value, rq.requested_by, rq.source_tool
        FROM recon_jobs rj
        JOIN recon_queries rq ON rq.id = rj.recon_query_id
        WHERE rj.id = ?
        """,
        (job_id,),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def list_recon_jobs(conn, *, mode: str | None, q: str | None, status: str | None, limit: int) -> list[dict]:
    where = []
    params = []
    if mode:
        where.append("rq.mode = ?")
        params.append(mode)
    if status:
        where.append("rj.status = ?")
        params.append(status)
    if q:
        where.append("LOWER(rq.query_value) LIKE ?")
        params.append(f"%{q.lower()}%")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    query = f"""
        SELECT rj.id, rj.status, rj.started_at, rj.finished_at, rj.duration_seconds,
               rj.error_message, rj.created_at,
               rq.mode, rq.query_value, rq.requested_by, rq.source_tool,
               (SELECT COUNT(*) FROM recon_findings rf WHERE rf.recon_job_id = rj.id) AS findings_count
        FROM recon_jobs rj
        JOIN recon_queries rq ON rq.id = rj.recon_query_id
        {where_sql}
        ORDER BY rj.created_at DESC
        LIMIT ?
    """
    params.append(limit)
    cur = conn.execute(query, tuple(params))
    return [dict(r) for r in cur.fetchall()]


def list_recon_findings(conn, *, job_id: str) -> list[dict]:
    cur = conn.execute(
        """
        SELECT id, recon_job_id, platform, url, category, tool_status,
               confidence_tier, evidence_json, first_seen_at, last_seen_at
        FROM recon_findings
        WHERE recon_job_id = ?
        ORDER BY
            CASE confidence_tier
                WHEN 'high' THEN 0
                WHEN 'medium' THEN 1
                ELSE 2
            END,
            platform ASC
        """,
        (job_id,),
    )
    rows = []
    for r in cur.fetchall():
        item = dict(r)
        ev = item.get("evidence_json")
        if isinstance(ev, str):
            try:
                item["evidence_json"] = json.loads(ev)
            except Exception:
                pass
        rows.append(item)
    return rows


def list_recon_artifacts(conn, *, job_id: str) -> list[dict]:
    cur = conn.execute(
        """
        SELECT artifact_type, path, size_bytes, created_at
        FROM recon_artifacts
        WHERE recon_job_id = ?
        ORDER BY id ASC
        """,
        (job_id,),
    )
    return [dict(r) for r in cur.fetchall()]
