# InstaLab API Documentation

This document describes the Flask API endpoints available in InstaLab.

**Base URL:** `http://localhost:5000` (default)

⚠️ **Security Note:** All endpoints are currently unauthenticated. See [SECURITY.md](SECURITY.md) for deployment recommendations.

---

## Table of Contents

- [Health & Status](#health--status)
- [Run Lifecycle](#run-lifecycle)
- [Runs](#runs)
- [Jobs](#jobs)
- [Targets](#targets)
- [Logins](#logins)
- [Insights](#insights)
- [Unfollow](#unfollow)
- [Monitor](#monitor)
- [Summary](#summary)
- [Configuration](#configuration)
- [Integrations](#integrations)
- [Schedules](#schedules)
- [Import](#import)
- [Error Responses](#error-responses)

---

## Health & Status

### GET `/api/health`

**Response:**
```json
{
  "status": "ok",
  "checks": {
    "db": {"status": "ok"},
    "files": {"status": "ok"},
    "scheduler": {"status": "ok"}
  }
}
```

### GET `/api/health/detail`

Includes extra checks (sessions, scraper, runs, unfollow).

### GET `/api/status`

Real‑time run status **for active jobs only**. Completed jobs are not listed here.

**Response:**
```json
{
  "state": "running",
  "active_jobs": [
    {
      "job_id": "<job_id>",
      "login_username": "login",
      "target_username": "target",
      "phase": "followers",
      "followers_progress": 120,
      "following_progress": 0,
      "followers_total": 500,
      "following_total": 300,
      "elapsed_seconds": 42,
      "eta_seconds": 180
    }
  ],
  "queued_jobs": [],
  "active_logins": ["login"],
  "queued_logins": [],
  "unfollow": {"state": "idle"}
}
```

---

## Run Lifecycle

### Typical flow
1. Add login (`/api/logins/add`) or ensure existing login has password/session.
2. Start run (`/api/run`).
3. If 2FA/challenge required, submit code via `/api/logins/challenge` or include `two_factor_code` / `challenge_code` in the run request.
4. Poll `/api/run/<job_id>` until `done=true`.
5. Use `/api/jobs/latest?login_username=...` for logs/trace tail.
6. View run history via `/api/runs` and `/api/run/<run_id>`.

### Locking behavior
- Per‑login run lock: only one active run per login.
- Per‑target run lock: only one active run per target.
- If locked, API returns **429**.

---

## Runs

### POST `/api/run`

Queue a snapshot run.

**Request:**
```json
{
  "login_username": "login",
  "target_username": "target",
  "two_factor_code": "123456",
  "challenge_code": "123456"
}
```

**Response:**
```json
{ "job_id": "<job_id>" }
```

### GET `/api/run/<job_id>`

Poll job status.

**Response (running):**
```json
{ "done": false, "meta": {"login_username": "login", "target_username": "target"} }
```

**Response (completed):**
```json
{
  "done": true,
  "meta": {"login_username": "login", "target_username": "target"},
  "payload": {
    "status": "success",
    "started_at": "...",
    "finished_at": "...",
    "result": {
      "run_id": 143,
      "followers_count": 2,
      "followees_count": 3,
      "non_followbacks_count": 3,
      "followers_fetch_seconds": 5,
      "followees_fetch_seconds": 4
    }
  }
}
```

### GET `/api/runs?target=<user>&limit=<n>`

List run history for a target.

### GET `/api/run/<int:run_id>`

Full run detail including follower/followee lists and deltas.

### POST `/api/run/cancel`

Cancel a running job.

**Request:**
```json
{ "login_username": "login", "target_username": "target" }
```

### DELETE `/api/run/<int:run_id>`

Delete a run (soft delete).

### POST `/api/run/undo/<int:run_id>`

Restore a deleted run.

---

## Jobs

### GET `/api/jobs/<job_id>/detail`

Returns progress/result plus log tails.

### GET `/api/jobs/latest?login_username=<login>`

Returns the latest job for a login (progress/result/log tails + trace tail).

**Response keys:**
- `progress` – current phase/count
- `result` – final success/error payload if finished
- `worker_out_tail` / `worker_err_tail`
- `trace_tail` – private API request/response trace (JSONL tail)

### GET `/api/collector/auth/status?login_username=<login>`

Returns browser-auth readiness for a collector login.

**Response keys:**
- `storage_path` – per-login browser storage path
- `storage_exists` – whether the storage file exists
- `auth_ready` – whether the storage contains a valid authenticated session for that login
- `storage_mtime` – storage file mtime when present

### POST `/api/collector/auth/init`

Starts interactive collector login for a specific `login_username`.

**Request:**
```json
{
  "login_username": "login",
  "max_wait_seconds": 300
}
```

---

## Targets

### GET `/api/targets`

List targets with latest run timestamps.

### GET `/api/targets_summary`

Aggregate summary per target (latest run + deltas + week aggregates).

### GET `/api/last_status`

Latest run per target plus overall latest run.

---

## Logins

### GET `/api/logins`

List available logins (masked fields only).

**Fields include:**
- `login_username`
- `has_password`, `has_totp_seed`
- `private_session_exists`, `private_session_mtime`
- `last_login_at`, `last_error`

### POST `/api/logins/add`

Add or update a login.

**Request:**
```json
{
  "login_username": "login",
  "login_password": "password",
  "totp_seed": "BASE32"
}
```

### POST `/api/logins/reset`

Clear cached session for a login.

### POST `/api/logins/delete`

Delete a login.

### POST `/api/logins/challenge`

Store an SMS/email challenge code for the next run.
Codes are consumed once and expire after ~2 minutes.

### POST `/api/logins/new-password`

Store a forced‑reset password.

### POST `/api/logins/totp/seed`

Generate a new TOTP seed (returns `totp_seed`).

### POST `/api/logins/totp/enable`

Enable TOTP (returns backup codes if provided by Instagram).

### POST `/api/logins/totp/disable`

Disable TOTP.

### POST `/api/logins/totp/code`

Generate a TOTP code from stored seed.

---

## Insights

### GET `/api/insights/followers?target=<user>&kind=followers|following&days=7`

Returns active + churned follower/following insights for the time window.

---

## Unfollow

### GET `/api/unfollow/status?login_username=<login>`

### GET `/api/unfollow/preview?login_username=<login>`

### POST `/api/unfollow/start`

**Request:**
```json
{
  "login_username": "login",
  "max_actions": 25,
  "dry_run": false,
  "delay_min": 25,
  "delay_max": 45
}
```

### POST `/api/unfollow/cancel`

### POST `/api/unfollow/init`

Starts interactive login for unfollow flow (used only if required).

---

## Monitor

### GET `/api/monitor/status`

Status for automated count checks (per target + overall).

---

## Summary

### GET `/api/summary`

High‑level totals and performance stats.

---

## Configuration

### GET `/api/config`

Returns current config + defaults (masked for sensitive values).

### PUT `/api/config`

Update config values. If `proxy_enabled` is true, host/port/user/pass are required.

Relevant runtime keys include:
- `run_scraper_backend`: collector family, either `browser` or `private` (`private_api` is accepted as a compatibility alias)
- `run_browser_collection_method`: browser collector method, either `browser_native` or `instaloader_session` when the collector family is `browser`

---

## Integrations

### WhatsApp Cloud API

### GET `/api/integrations/whatsapp/webhook`

Webhook verification endpoint for Meta WhatsApp Cloud API (`hub.mode`, `hub.verify_token`, `hub.challenge`).

### POST `/api/integrations/whatsapp/webhook`

Inbound webhook endpoint for WhatsApp messages. Supported text commands:

- `status`
- `run <login_username> <target_username>`
- `cancel <login_username> <target_username>`
- `help`

### POST `/api/integrations/whatsapp/test`

Send a test WhatsApp message via Cloud API.

**Request (optional):**
```json
{
  "to": "+15551234567",
  "message": "InstaLab test message"
}
```

If `to` is omitted, the endpoint uses configured `whatsapp_notify_to`.

---

## Schedules

### GET `/api/schedules`

List schedules.

### POST `/api/schedules`

Create schedule with cron expression.

### PUT `/api/schedules/<id>`

Update target or cron.

### DELETE `/api/schedules/<id>`

---

## Import

### POST `/api/import/osintgraph`

Bulk import follower/followee lists.

**Request:**
```json
{
  "target_username": "target",
  "login_username": "login",
  "followers": ["user1"],
  "followees": ["user2"],
  "timestamp": "2026-02-04_12-08-23"
}
```

**Response:**
```json
{ "ok": true, "run_id": 123, "changes": {"followers": {"added": [], "removed": []}} }
```

---

## Error Responses

**400:** `{"error": "..."}`

**404:** `{"error": "not found"}`

**429:** `{"error": "another run is already in progress for this login"}`

**500:** `{"error": "..."}`

---

For security best practices, see [SECURITY.md](SECURITY.md).
