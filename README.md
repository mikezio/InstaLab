# InstaLab

InstaLab is an operations console for Instagram snapshot runs (followers/following), scheduling, run history, insights, and unfollow cleanup. It runs as a two-container stack: Flask API + Django UI. Collection supports two collector families: **browser** (web session collectors) and **private_api** (`instagrapi`).

## What you get
- Single‑pane UI for queueing runs, viewing history, and monitoring status.
- Snapshot/count workers with safeguards to avoid overlapping runs.
- Accounts manager with password + TOTP + challenge handling.
- Health endpoints and metrics-friendly status surfaces.
- Built‑in per-run trace logging for API request/response visibility.

## Repo layout
- `docker-compose.yml` / `docker-compose.preview.yml`
- `docker-compose.local.yml` + `docker-compose.local-postgres.yml`
- `Dockerfile`
- `app/` (Flask API, Django UI, workers, scripts, UI build pipeline)

## Quick start (local)
1) Create a local env:
   - `cp .env.example .env`
2) Start Postgres + app stack:
   - `docker compose -f docker-compose.local.yml -f docker-compose.local-postgres.yml up -d --build`
   - Or: `./scripts/local_postgres_up.sh`
3) Open UI:
   - http://localhost:8000

## Local without bundled Postgres
If you already have Postgres running, set the `INSTALAB_DB_*` values in `.env` and start:
- `docker compose -f docker-compose.local.yml up -d --build`

## Environment and secrets
- Secrets are not committed. Use `.env.example` as a template.
- **Required for production:**
  - `DJANGO_SECRET_KEY` (strong random value)
  - `DJANGO_DEBUG=False`
  - `DJANGO_ALLOWED_HOSTS`
  - `INSTALAB_ENCRYPTION_KEY` (encrypts login secrets in Postgres)
- Runtime data (DB, sessions, job artifacts) should live outside the repo.
- See [SECURITY.md](SECURITY.md) for security best practices and deployment guidelines.

## Database
- **Postgres only.** Set `INSTALAB_DB_TYPE=postgres`.
- Default DB name/user: `instalab` (see `docker-compose.local-postgres.yml`).

## Authentication flow
- Browser collector family: initialize collector auth via `POST /api/collector/auth/init` for a specific `login_username`, then confirm readiness with `GET /api/collector/auth/status?login_username=<login>`. Browser auth state is stored per login under `INSTALAB_BROWSER_STORAGE_DIR` (default `/data/instalab/browser/<login>.json`).
- Browser collection method: when `run_scraper_backend=browser`, the concrete browser collector is selected by `run_browser_collection_method`:
  - `browser_native` for live browser collection plus browser/web-session top-off
  - `instaloader_session` for dedicated-session Instaloader using the per-login browser session
- Private API collector family (`instagrapi`): logins are stored in Postgres `login_accounts` and encrypted at rest.
- For the private API family, a first successful login caches session settings; subsequent runs reuse the session.
- 2FA is supported via TOTP or SMS/email challenge codes.
- Device profile settings are persisted to avoid “new device” loops.
- Collector setup and collector->target execution runbook: [docs/COLLECTOR_TARGET_SETUP_FLOW.md](docs/COLLECTOR_TARGET_SETUP_FLOW.md).

## UI build
- Legacy template styles:
  - Source: `app/ui_src/ui.css`
  - Output: `app/django_app/static/ui.css`
- Modern React UI:
  - Source: `app/frontend/`
  - Output: `app/django_app/static/modern/`
- Build both UI assets: `cd app && npm run build`
- Watch legacy CSS: `cd app && npm run dev`
- Run modern UI dev server: `cd app && npm run frontend:dev`

## UI routing
- `/` serves legacy or modern UI based on `INSTALAB_UI_VARIANT` (`legacy` or `modern`)
- `/legacy/` always serves legacy UI
- `/app/` serves the modern React UI

## Operational notes
- This repo contains code + compose; environment‑specific ops runbooks live outside the repo.
- In production behind nginx, ensure `/static/` serves Django’s `STATIC_ROOT` (default: `app/django_app/staticfiles` after `collectstatic`).
- Proxy routing (Decodo): configure under Settings → Proxy routing (stored in the config table). Native proxying requires host/port/username/password.

## Branching
- `dev`: active development
- `main`: stable / public-ready

## Release checklist
- Update version / changelog (if you keep one)
- Run UI build (`cd app && npm run build`)
- Verify API + UI start clean (`docker compose up -d`)
- Sanity check a run end‑to‑end in dev
- Merge `dev` -> `main`
- Tag release (`git tag vX.Y.Z && git push --tags`)

## Documentation
- [API.md](API.md) – API endpoint documentation
- [ARCHITECTURE.md](ARCHITECTURE.md) – System architecture and run lifecycle
- [docs/COLLECTOR_TARGET_SETUP_FLOW.md](docs/COLLECTOR_TARGET_SETUP_FLOW.md) – collector login setup and target execution flow
- [SECURITY.md](SECURITY.md) – Security considerations and deployment best practices
- [CONTRIBUTING.md](CONTRIBUTING.md) – Guidelines for contributing to the project

## License
See LICENSE file for license information.
