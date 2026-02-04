# InstaLab API Documentation

This document describes the Flask API endpoints available in InstaLab.

**Base URL:** `http://localhost:5000` (default)

⚠️ **Security Note:** All endpoints are currently unauthenticated. See [SECURITY.md](SECURITY.md) for deployment recommendations.

---

## Table of Contents

- [Health & Status](#health--status)
- [Runs](#runs)
- [Targets](#targets)
- [Logins](#logins)
- [Insights](#insights)
- [Unfollow](#unfollow)
- [Monitor](#monitor)
- [Configuration](#configuration)
- [Import](#import)

---

## Health & Status

### GET `/api/health`

System health check endpoint.

**Response:**
```json
{
  "status": "ok",
  "db_type": "postgres",
  "db_connected": true,
  "scheduler_running": true,
  "active_jobs": 0,
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### GET `/api/status`

Real-time job queue status.

**Response:**
```json
{
  "active_jobs": {
    "job_abc123": {
      "job_id": "job_abc123",
      "target": "target_username",
      "login": "login_username",
      "started_at": "2024-01-15T10:25:00Z",
      "status": "running"
    }
  },
  "completed_count": 42,
  "failed_count": 3
}
```

---

## Runs

### POST `/api/run`

Queue a new snapshot run.

**Request Body:**
```json
{
  "target": "target_username",
  "login": "login_username",
  "scraper_backend": "private"  // optional, defaults to private API
}
```

**Response (202 Accepted):**
```json
{
  "job_id": "job_abc123",
  "status": "queued",
  "poll_url": "/api/run/job_abc123"
}
```

### GET `/api/run/<job_id>`

Poll the status of a queued/running job.

**Response (still running):**
```json
{
  "job_id": "job_abc123",
  "status": "running",
  "progress": {
    "stage": "fetching_followers",
    "followers_fetched": 1250,
    "followees_fetched": 0
  }
}
```

**Response (completed):**
```json
{
  "job_id": "job_abc123",
  "status": "done",
  "run_id": 42,
  "result": {
    "run_id": 42,
    "target_username": "target_username",
    "login_username": "login_username",
    "followers_count": 5432,
    "followees_count": 1234,
    "non_followbacks_count": 890,
    "created_at": "2024-01-15T10:30:00Z",
    "duration_seconds": 125
  }
}
```

**Response (failed):**
```json
{
  "job_id": "job_abc123",
  "status": "failed",
  "error": "Authentication failed: Invalid credentials"
}
```

### GET `/api/runs`

List historical runs for a target.

**Query Parameters:**
- `target` (required): Target username
- `limit` (optional): Max results (default: 50)

**Response:**
```json
{
  "runs": [
    {
      "id": 42,
      "target_username": "target_username",
      "login_username": "login_username",
      "created_at": "2024-01-15T10:30:00Z",
      "followers_count": 5432,
      "followees_count": 1234,
      "duration_seconds": 125
    }
  ]
}
```

### GET `/api/run/<run_id>`

Get detailed run data including follower/followee lists.

**Response:**
```json
{
  "run": {
    "id": 42,
    "target_username": "target_username",
    "created_at": "2024-01-15T10:30:00Z",
    "followers_count": 5432,
    "followees_count": 1234
  },
  "followers": ["user1", "user2", "..."],
  "followees": ["user3", "user4", "..."]
}
```

---

## Targets

### GET `/api/targets`

List all tracked targets.

**Response:**
```json
{
  "targets": [
    {
      "username": "target1",
      "run_count": 15,
      "first_run": "2023-12-01T10:00:00Z",
      "last_run": "2024-01-15T10:30:00Z"
    }
  ]
}
```

### GET `/api/targets_summary`

Summary of latest metrics for all targets with weekly deltas.

**Response:**
```json
{
  "targets": [
    {
      "username": "target1",
      "latest_followers": 5432,
      "latest_followees": 1234,
      "latest_run": "2024-01-15T10:30:00Z",
      "delta_followers_7d": 45,
      "delta_followees_7d": -12,
      "trend": "growing"
    }
  ]
}
```

### GET `/api/last_status`

Most recent runs across all targets or for a specific target.

**Query Parameters:**
- `target` (optional): Filter to specific target

**Response:**
```json
{
  "last_runs": [
    {
      "id": 42,
      "target_username": "target1",
      "created_at": "2024-01-15T10:30:00Z",
      "followers_count": 5432
    }
  ]
}
```

---

## Logins

### GET `/api/logins`

List available login accounts.

**Response:**
```json
{
  "logins": [
    {
      "username": "login1",
      "has_password": true,
      "has_cookie": true,
      "cookie_file": "/data/instalab/cookies/login1_session.json",
      "last_used": "2024-01-15T10:30:00Z"
    }
  ]
}
```

⚠️ **Security:** This endpoint may expose password status. Should be restricted in production.

### POST `/api/logins/add`

Add or update a login account.

**Request Body:**
```json
{
  "username": "new_login",
  "password": "instagram_password",
  "cookie_file": "/path/to/session.json"  // optional
}
```

**Response:**
```json
{
  "success": true,
  "username": "new_login",
  "message": "Login added successfully"
}
```

### POST `/api/logins/delete`

Remove a login account.

**Request Body:**
```json
{
  "username": "login_to_remove"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Login deleted"
}
```

---

## Insights

### GET `/api/insights/followers`

Follower churn analysis (new, active, churned followers).

**Query Parameters:**
- `target` (required): Target username
- `limit` (optional): Max results (default: 100)

**Response:**
```json
{
  "new_followers": [
    {
      "username": "user1",
      "first_seen": "2024-01-15T10:30:00Z",
      "days_following": 5
    }
  ],
  "churned_followers": [
    {
      "username": "user2",
      "first_seen": "2023-12-01T10:00:00Z",
      "last_seen": "2024-01-10T08:00:00Z",
      "days_followed": 40
    }
  ],
  "active_followers": 5432,
  "total_historical": 5678
}
```

### GET `/api/insights/followees`

Similar to `/api/insights/followers` but for accounts the target follows.

---

## Unfollow

### GET `/api/unfollow/status`

Current unfollow operation status.

**Query Parameters:**
- `login` (required): Login username

**Response (idle):**
```json
{
  "status": "idle",
  "login": "login1",
  "eligible_count": 150,
  "last_run": "2024-01-10T14:30:00Z"
}
```

**Response (running):**
```json
{
  "status": "running",
  "login": "login1",
  "progress": {
    "completed": 12,
    "total": 25,
    "current": "user123",
    "success_count": 11,
    "fail_count": 1
  }
}
```

### GET `/api/unfollow/preview`

Preview list of users eligible for unfollowing.

**Query Parameters:**
- `login` (required): Login username
- `limit` (optional): Max results (default: 25)

**Response:**
```json
{
  "eligible": [
    {
      "username": "user1",
      "not_following_back": true,
      "days_since_follow": 45
    }
  ],
  "total_eligible": 150
}
```

### POST `/api/unfollow/start`

Begin batch unfollow operation.

**Request Body:**
```json
{
  "login": "login1",
  "max_count": 25,
  "dry_run": false,
  "delay_min": 25,
  "delay_max": 45
}
```

**Response:**
```json
{
  "success": true,
  "job_id": "unfollow_xyz789",
  "estimated_duration_seconds": 750,
  "message": "Unfollow started for 25 users"
}
```

### POST `/api/unfollow/cancel`

Cancel a running unfollow operation.

**Request Body:**
```json
{
  "login": "login1"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Unfollow cancelled",
  "completed_count": 12
}
```

---

## Monitor

### GET `/api/monitor/status`

Status of automated monitoring jobs.

**Response:**
```json
{
  "enabled": true,
  "interval_minutes": 120,
  "last_check": "2024-01-15T09:00:00Z",
  "next_check": "2024-01-15T11:00:00Z",
  "recent_checks": [
    {
      "target": "target1",
      "timestamp": "2024-01-15T09:00:00Z",
      "delta": 3,
      "triggered_run": false
    }
  ]
}
```

---

## Configuration

### GET `/api/config`

Get current system configuration.

**Response:**
```json
{
  "run_stall_seconds": 1200,
  "run_max_seconds": 10800,
  "monitor_interval_minutes": 120,
  "monitor_threshold_delta": 4,
  "unfollow_max_per_run": 25
}
```

### PUT `/api/config`

Update system configuration.

**Request Body:**
```json
{
  "monitor_threshold_delta": 5,
  "unfollow_max_per_run": 30
}
```

**Response:**
```json
{
  "success": true,
  "updated": ["monitor_threshold_delta", "unfollow_max_per_run"]
}
```

---

## Import

### POST `/api/import/osintgraph`

Bulk import follower/followee data from external source.

**Request Body:**
```json
{
  "target": "target_username",
  "login": "login_username",
  "followers": ["user1", "user2", "user3"],
  "followees": ["user4", "user5"],
  "timestamp": "2024-01-15T10:30:00Z"
}
```

**Response:**
```json
{
  "success": true,
  "run_id": 43,
  "imported_followers": 3,
  "imported_followees": 2
}
```

---

## Error Responses

All endpoints may return error responses:

**400 Bad Request:**
```json
{
  "error": "Missing required parameter: target"
}
```

**404 Not Found:**
```json
{
  "error": "Run not found: 999"
}
```

**500 Internal Server Error:**
```json
{
  "error": "Database connection failed"
}
```

---

## Rate Limiting

Currently, there is **no rate limiting** implemented. In production:

- Implement per-IP rate limits at reverse proxy level
- Add API key authentication with per-key quotas
- Consider request queuing for expensive operations

---

## Webhooks (Future)

Not currently implemented. Future enhancement ideas:

- POST webhook on run completion
- POST webhook on monitor threshold exceeded
- POST webhook on unfollow batch completion

---

## WebSockets (Future)

Not currently implemented. Future enhancement for real-time updates:

- Live job progress updates
- Real-time follower count changes
- Active user presence indicators

---

For security best practices, see [SECURITY.md](SECURITY.md).
