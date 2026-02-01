# InstaLab app notes

Last updated: 2026-02-01

## Purpose
InstaLab runs Instagram snapshot jobs, schedules, and cleanup workflows (unfollow) with a web UI.

## Stack
- Docker compose: /srv/apps/instalab
- API (Flask): http://192.168.4.30:5000
- UI (Django + nginx): http://192.168.4.30:8000 (proxy), http://127.0.0.1:8002 (direct)
- Secrets: /srv/secrets/instalab.env (group: secrets, 640)
- Logins store: /srv/secrets/instalab-logins.json (group: secrets, 660)
- Data: /srv/data/instalab

## Preview UI (live iteration)
- Purpose: run a separate preview stack with auto-reload while keeping prod intact
- Compose overlay: /srv/apps/instalab/docker-compose.preview.yml
- Preview UI: http://192.168.4.30:8003 (LAN) or http://127.0.0.1:8003 (local)
- Preview API: http://192.168.4.30:5002 (LAN) or http://127.0.0.1:5002 (local)
- Start preview stack:
  - `docker compose -f /srv/apps/instalab/docker-compose.yml -f /srv/apps/instalab/docker-compose.preview.yml up -d`
- Stop preview stack:
  - `docker compose -f /srv/apps/instalab/docker-compose.yml -f /srv/apps/instalab/docker-compose.preview.yml down`
- Preview services use Django runserver + Flask debug for auto-reload

## UI build (Tailwind)
- UI source: /srv/apps/instalab/app/ui_src/ui.css
- Tailwind config: /srv/apps/instalab/app/tailwind.config.js
- Output CSS: /srv/apps/instalab/app/django_app/static/ui.css
- Build once: `cd /srv/apps/instalab/app && npm run build`
- Watch (live CSS rebuild): `cd /srv/apps/instalab/app && npm run dev`

## Auth & sessions
- Instaloader session files live under /srv/data/instalab/home/.config/instaloader
- Manage logins via UI top bar → Accounts (stored in /srv/secrets/instalab-logins.json; password is scrubbed after session is saved)
- Session reset is available in Accounts manager per-login action
- New runs after reset prompt for 2FA and save a new session
- Snapshot/count workers allow session-only runs; a password is required only when no session file exists
- Browser cookie bootstrap: store Netscape/Mozilla cookie file in /srv/secrets/instalab-cookies (mounted as /data/instalab/cookies) and set cookie filename in Accounts
- Cookie-only logins are allowed for snapshot/count; InstaLab will import cookies and save an Instaloader session on first run
- If a login was reset and has no session, re-add it in Accounts; the API will update the existing record with the new password
- Accounts manager UI shows each account (file/env), session status, and provides Reauth/Reset/Delete actions
- Scraper backend set via `INSTALAB_SCRAPER_BACKEND` (now set to instaloader)
- Instaloader backend uses session files; cookies bootstrap the session on first run.
- Cookie bootstrap validates auth via Instaloader test_login; it refreshes the session with password login when provided, and falls back to password login if cookies aren't authenticated.
- Selenium backend relies on browser cookies or username/password login; it does not use Instaloader session files
- Blocked logins: `mzio` is blocked from use; extend via `INSTALAB_BLOCKED_LOGINS`
- Accounts add flow accepts cookie-only login (password optional if cookie filename provided)
- Selenium uses the system chromedriver at `/usr/bin/chromedriver` by default
- Selenium followers/following trigger clicks visible text when links are not anchors
- Selenium scroll container detection scans dialog descendants (robust against IG layout changes)
- Headful Selenium runs (SELENIUM_HEADLESS=0) set HOME/XDG_RUNTIME_DIR and use a temp profile to avoid Chromium crash
- On list open failure, Selenium dumps debug HTML/PNG to /tmp/instalab_list_<kind>_* for troubleshooting
- Selenium cookie logins now fail fast if Instagram shows a challenge checkpoint
- Selenium re-checks login state after profile load and errors if a challenge appears
- VNC helper (local-only): `instalab-vnc` exposes noVNC on `127.0.0.1:7900` for manual login
- Cookie export helper (host): `python /srv/apps/instalab/app/scripts/selenium_login.py --login <user> --cookie-out /srv/secrets/instalab-cookies/cookies_<user>.txt --display :1`
- Cookie export helper (in-container): `docker exec -it instalab-vnc /app/scripts/selenium_login.py --login <user> --cookie-out /data/instalab/cookies/cookies_<user>.txt`
- noVNC uses `/usr/share/novnc/utils/novnc_proxy` if `novnc_proxy` is not on PATH; X11 socket dir is created on start
- Selenium login helper forces HOME/XDG_RUNTIME_DIR and uses a temp Chrome profile to avoid crashpad errors in VNC

## UI behavior
- Control rail is intentionally minimal: collector login selector + session status only
- Progress percent is computed across followers + following totals (combined)
- Progress bar shows small percent movement (decimal + minimum visual width) to avoid “stuck at 0%”
- Multi-job ticker shows per-job overall percent instead of repeating the same status line
- Recent follow changes card shows new follows within a time window + recent unfollows (labels adapt to followers vs following)
- Preview UI: TailAdmin-inspired header + sidebar navigation with a flatter, table-first ops surface
- Preview UI: Signal strip replaces KPI cards (pressure, freshness, backlog ETA, momentum, NFB risk)
- Preview UI: Roster stays table-forward with minimal chrome; focus + queue flow remain in primary canvas

## Data model
- followers_history + followees_history track first_seen/last_seen/active/unfollowed_at/first_seen_known
- First run seeds baseline rows with first_seen_known=0 (unknown) so later unfollows are tracked
- Follow re-activations update first_seen to the new follow event and set first_seen_known=1
- count_checks table tracks lightweight count probes + auto-trigger decisions

## Monitoring
- Lightweight count checks run via /srv/apps/instalab/app/count_worker.py
- Defaults: interval 120 minutes, threshold delta 4, min gap 180 minutes
- Triggers a full snapshot when follower/following delta >= threshold
- Uses monitor_login_username (defaults to primary login)
- Docker healthchecks:
  - instalab-api -> GET /api/health
  - instalab-ui -> GET /
- Apps nginx serves static UI assets directly:
  - Config: /etc/nginx/sites-available/instalab-ui (location /static/ -> /srv/apps/instalab/app/django_app/static/)
- Health endpoints (API):
  - /api/health (core: DB + scheduler + files) -> JSON `status`
  - /api/health/detail (full: DB, files, scheduler, monitor, sessions, scraper backend, runs, unfollow)
- Monitor status endpoint (API):
  - /api/monitor/status (last count_checks per target + overall latest)
- APM: API/UI run via `ddtrace-run` with DD_SERVICE=instalab-api / instalab-ui
- RUM: Datadog RUM snippet injected into UI template (see /srv/secrets/datadog-rum.env)
- Datadog log alert: “Service: InstaLab - static asset 404s (edge nginx)” (`254564356`)

## Run flow
- API launches snapshot_worker.py with progress.json + result.json in /srv/data/instalab/job_runs/job_<id>
- Run lock prevents overlapping runs per login (API returns 429 if busy)
- UI polls /api/status for active jobs and percent
- Job detail endpoint: /api/jobs/<job_id>/detail (progress/result + worker log tails)
- Insights endpoint: /api/insights/followers?target=<user>&kind=followers|following&days=7

## Notes
- Avoid running Codex with sudo; run as apps and elevate per-command only
- Rate-limit tuning: Settings → Runner safeguards → Per-account delay min/max (default 0.25–0.75s)
- OSINTGraph import: `python3 /srv/apps/instalab/app/scripts/import_osintgraph.py --target <username> --login <login_username>`
- OSINTGraph auto-import runs after `osintgraph discover` when followers/followees are complete

## Access (SSO)
- Public URL: https://instalab.mzio.dev (Azure SSO enforced via edge oauth2-proxy)
- oauth2-proxy instance: 127.0.0.1:4181 on edge
- Azure redirect URI: https://instalab.mzio.dev/oauth2/callback
