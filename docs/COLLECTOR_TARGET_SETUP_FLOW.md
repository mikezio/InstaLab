# Collector Login and Target Run Flow

This is the current operator runbook for launching a target run with an explicit collector login.

Verified against the live apps VM runtime on March 27, 2026:
- `run_scraper_backend=browser`
- `run_browser_collection_method=browser_native`
- `run_login_mode=auto`
- `proxy_enabled=false`
- collector auth storage under `/data/instalab/browser/<login>.json`

## Terms
- Collector login: the Instagram account InstaLab authenticates as (`login_username`).
- Target account: the subject being monitored (`target_username`).

## Current launch flow
1. Confirm the runtime is healthy:
   - `GET /api/health`
   - `GET /api/status`
2. Confirm the collector exists:
   - `GET /api/logins`
3. Confirm collector auth is ready for the selected login:
   - `GET /api/collector/auth/status?login_username=<collector_login>`
4. Confirm current runtime behavior before launching:
   - `GET /api/config`
   - confirm both the collector family and browser sub-method:
     - `run_scraper_backend`
     - `run_browser_collection_method`
5. Launch the run with an explicit pair:
   - `POST /api/run` with `login_username=<collector_login>` and `target_username=<target_account>`
6. Poll status until completion:
   - `GET /api/run/<job_id>`
   - `GET /api/status` for active queue state
   - `GET /api/jobs/latest?login_username=<collector_login>` for logs and trace tails

## Launch preflight checklist
- The login is present in `/api/logins`.
- Collector auth status reports `auth_ready=true`.
- `/api/status` does not already show an active job for the same login or target.
- `/api/config` matches the runtime you expect before you start:
  - browser or private API collector family
  - browser-native or dedicated-session Instaloader when collector family is `browser`
  - proxy enabled or disabled
  - login mode and pacing

## Collector -> target execution path
1. Run request is queued with explicit `login_username` and `target_username`.
2. The worker resolves runtime config from `/api/config` backed settings.
3. For the browser collector family, the worker uses per-login browser auth storage from `/data/instalab/browser/<collector_login>.json`.
4. When `run_browser_collection_method=browser_native`, the worker collects via persistent Playwright browser context plus browser/web-session pagination.
5. When `run_browser_collection_method=instaloader_session`, the worker imports the per-login browser session into a dedicated Instaloader session and collects through the Instaloader iterator path.
6. The worker resolves the target by `target_username` and collects followers/following.
7. Results are persisted as target-linked outputs:
   - artifacts under `/srv/data/instalab/job_runs/job_<id>/`
   - DB writes in `run_followers`, `run_followees`, `followers_history`, `followees_history`, `relationship_events`

## Current behavior notes
- Browser auth storage is account-scoped, not shared.
- Collector auth readiness is browser-storage based in the live runtime.
- Browser collector family now has two internal methods:
  - `browser_native`
  - `instaloader_session`
- Private API collector family remains supported, but it is not the current live default.
- Proxy routing is configurable, but it is currently disabled in the live runtime.
- Per-login and per-target locks still prevent overlapping runs.

## If auth is not ready
1. Run auth diagnostics:
   - `POST /api/logins/auth/preflight`
   - `GET /api/logins/auth/trace`
2. Initialize collector auth for that same login:
   - `POST /api/collector/auth/init`
3. Complete any challenge or 2FA prompt.
4. Re-check `GET /api/collector/auth/status?login_username=<collector_login>`.

## If you intentionally switch to the private API family
- Set `run_scraper_backend=private` through `/api/config`.
- Re-check the collector login in `/api/logins`.
- Re-run the same launch sequence above.
- Do not assume the live browser defaults still apply once you switch collector families.
