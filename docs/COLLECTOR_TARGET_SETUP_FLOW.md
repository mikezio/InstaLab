# Collector Login and Target Run Flow

This runbook documents the known-good setup for using a collector login against target accounts, and why this flow is stable now.

## Terms
- Collector login: the Instagram account InstaLab authenticates as (`login_username`).
- Target account: the subject being monitored (`target_username`).

## Known-good setup flow
1. Add/update collector login in Accounts (username/password, plus TOTP seed if enabled on that account).
2. Run auth diagnostics:
   - `POST /api/logins/auth/preflight`
   - `GET /api/logins/auth/trace`
   Confirm no major clock-skew warning and no immediate auth failure.
3. Initialize collector auth for that same login:
   - `POST /api/collector/auth/init`
   Complete challenge/2FA if prompted.
4. Confirm session readiness:
   - `GET /api/logins` shows `private_session_exists: true` for the collector login.
   - `GET /api/collector/auth/status?login_username=<collector_login>` reports ready.
5. Set runtime behavior:
   - `run_scraper_backend=private` (alias `instagrapi` accepted)
   - `run_login_mode=auto` (session-first, password fallback only when needed)
   - `proxy_enabled=true` for normal run traffic.
6. Start run with explicit pair:
   - `POST /api/run` with `login_username=<collector_login>` and `target_username=<target_account>`.

## Collector -> target execution path
1. Run request is queued with explicit `login_username` and `target_username`.
2. Worker builds client in collector scope:
   - browser auth state from `/data/instalab/browser/<collector_login>.json`
   - private session cache from Postgres `login_accounts.session_settings` for that login
3. Worker restores session (or performs one bounded password reauth in `auto` mode).
4. Worker resolves the target by `target_username` and collects followers/following.
5. Results are persisted as target-linked outputs:
   - artifacts under `/srv/data/instalab/job_runs/job_<id>/`
   - DB writes in `run_followers`, `run_followees`, `followers_history`, `followees_history`, `relationship_events`.

## Why this works now
- Per-login auth/session isolation:
  - browser storage is account-scoped (`/data/instalab/browser/<login>.json`)
  - browser->private bridge rejects username-mismatched sessions.
- Session validation is strict:
  - requires real `sessionid` cookie
  - clears non-authenticated cached settings payloads.
- Proxy identity is stable per collector login:
  - sticky proxy sessions are per-login
  - collector auth init uses the same sticky identity model as run execution.
- 2FA timing is corrected:
  - TOTP is generated at challenge time with +/-30s fallback windows.
- Retry behavior is bounded:
  - extra auth retry loops were removed
  - single-client LoginRequired reauth is capped.
- Runtime behavior is pinned and consistent:
  - `instagrapi` pinned to commit `d181c496e0e2688f3ac7b8b62c7f928ede445a14`
  - deterministic realistic device profiles per login.

## Recovery for checkpoint loops
If one login gets stuck in repeated challenge/checkpoint failures:
1. Temporarily set `proxy_enabled=false`.
2. Complete manual re-auth for that login (Accounts/noVNC flow).
3. Re-enable proxy and re-run.
