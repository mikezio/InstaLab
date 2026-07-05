# InstaLab

InstaLab is a private Instagram operations console for collecting follower/following snapshots, comparing relationship changes over time, scheduling runs, tracking account health, and managing cleanup workflows from one web UI.

It is built as a two-service app:

- **Flask API**: run orchestration, collectors, workers, persistence, integrations
- **Django UI**: operator dashboard, settings, account management, API proxy

The default local setup uses Docker Compose with Postgres and opens the modern UI at `http://localhost:8000/app/`.

## Important Security Note

InstaLab is designed for trusted/private deployments. The Flask API does not provide a built-in public authentication layer. Do not expose the API or UI directly to the public internet without a reverse proxy, VPN, SSO, basic auth, firewall rules, or equivalent access control.

See [SECURITY.md](SECURITY.md) before deploying outside localhost.

## Features

- Snapshot runs for followers, following, non-followbacks, and relationship deltas
- Modern React operator UI plus legacy Django UI route
- Per-login and per-target run locks to avoid overlapping collection
- Browser collector support with per-login persisted browser auth state
- Private API collector support through `instagrapi`
- Password, TOTP, challenge-code, and challenge-email account handling
- Active job progress with page-level telemetry, ETA, trace tails, and worker logs
- Run history, run detail, deleted-run restore, and relationship event rebuilding
- Audit-only handling for incomplete/profile-only snapshots so partial data does not pollute baselines
- Count-watch monitoring and scheduled runs
- Optional proxy routing and WhatsApp Cloud API integration
- Postgres-backed login/session/config storage with encrypted login secrets

## Requirements

Recommended:

- Docker and Docker Compose v2
- Git

For local development without relying entirely on containers:

- Python 3.11+
- Node.js 20+
- Postgres 16+

## Quick Start

Clone the repo:

```bash
git clone https://github.com/mikezio/InstaLab.git
cd InstaLab
```

Create an environment file:

```bash
cp .env.example .env
```

Set the required local values in `.env`:

```bash
DJANGO_SECRET_KEY=replace-with-a-long-random-value
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
INSTALAB_ENCRYPTION_KEY=replace-with-a-long-random-value
```

Start Postgres, the API, and the UI:

```bash
docker compose -f docker-compose.local.yml -f docker-compose.local-postgres.yml up -d --build
```

Open:

- Modern UI: `http://localhost:8000/app/`
- Legacy UI: `http://localhost:8000/legacy/`
- API health: `http://localhost:5000/api/health`

Stop the stack:

```bash
docker compose -f docker-compose.local.yml -f docker-compose.local-postgres.yml down
```

## Configuration

Use `.env.example` as the source of truth for available environment variables.

Important settings:

| Variable | Purpose |
| --- | --- |
| `INSTALAB_DB_TYPE` | Must be `postgres` |
| `INSTALAB_DB_HOST` / `PORT` / `NAME` / `USER` / `PASS` | Postgres connection |
| `INSTALAB_ENCRYPTION_KEY` | Encrypts stored login secrets |
| `DJANGO_SECRET_KEY` | Django signing secret |
| `DJANGO_DEBUG` | Set `False` outside local dev |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated UI host allowlist |
| `INSTALAB_SCRAPER_BACKEND` | `browser` or `private_api` |
| `INSTALAB_BROWSER_COLLECTION_METHOD` | Usually `browser_native`; `instaloader_session` is also supported |
| `INSTALAB_BROWSER_STORAGE_DIR` | Per-login browser auth storage directory |
| `INSTALAB_PROXY_*` | Optional proxy routing defaults |

For local Compose with bundled Postgres, `docker-compose.local-postgres.yml` supplies the database connection values automatically.

## Collector Setup

InstaLab separates the collector login from the target account:

- **Collector login**: the Instagram account used for collection
- **Target account**: the account being monitored

Recommended flow:

1. Add or update a collector login in the UI.
2. For browser collection, initialize auth for that login from the UI or `POST /api/collector/auth/init`.
3. Confirm readiness with `GET /api/collector/auth/status?login_username=<login>`.
4. Start a run with explicit `login_username` and `target_username`.
5. Watch progress in the UI or poll `GET /api/run/<job_id>`.

See [docs/COLLECTOR_TARGET_SETUP_FLOW.md](docs/COLLECTOR_TARGET_SETUP_FLOW.md) for the detailed operator flow.

## UI Routes

- `/app/`: modern React UI
- `/legacy/`: legacy Django UI
- `/`: route selected by `INSTALAB_UI_VARIANT`
- `/api/*`: proxied from Django UI to the Flask API

Build UI assets:

```bash
cd app
npm install
npm run build
```

Run only the modern UI dev server:

```bash
cd app
npm run frontend:dev
```

## API

Common endpoints:

- `GET /api/health`
- `GET /api/status`
- `POST /api/run`
- `GET /api/run/<job_id>`
- `GET /api/runs`
- `GET /api/run/<run_id>`
- `GET /api/logins`
- `GET /api/config`
- `GET /api/collector/auth/status`
- `POST /api/collector/auth/init`

See [API.md](API.md) for endpoint details.

## Development

Install frontend dependencies when working outside Docker:

```bash
cd app
npm install
npm install --prefix frontend
```

Run Python tests against the local Compose Postgres port:

```bash
INSTALAB_DB_HOST=127.0.0.1 pytest
```

Run the production UI build:

```bash
npm run -C app build
```

The current pushed branch was verified with:

```bash
INSTALAB_DB_HOST=127.0.0.1 pytest
npm run -C app build
```

## Repository Layout

```text
.
├── app/
│   ├── server.py                  # Flask API and orchestration
│   ├── snapshot_worker.py          # Snapshot worker entry point
│   ├── private_api_tracker.py      # instagrapi collector
│   ├── browser_tracker.py          # browser/session collector
│   ├── tracker_db.py               # run persistence and relationship history
│   ├── django_app/                 # Django UI and API proxy
│   ├── frontend/                   # modern React UI
│   └── tests/                      # pytest suite
├── docker-compose.local.yml        # fresh-clone local app stack
├── docker-compose.local-postgres.yml
├── docker-compose.yml              # maintainer production/homelab stack
├── Dockerfile
├── API.md
├── ARCHITECTURE.md
├── SECURITY.md
└── docs/
```

## Deployment Notes

- Use the local Compose files for a portable fresh clone.
- `docker-compose.yml` is the maintainer production stack and includes host-specific `/srv/...` paths and an external `apps-db` Docker network.
- Put runtime data, browser storage, sessions, and job artifacts outside the Git repo.
- Put secrets in `.env` or your secret manager. Never commit `.env`, session files, cookies, or database files.
- Place the app behind HTTPS and access control before exposing it beyond a private network.

## Maintenance

Before opening a PR or publishing a release:

```bash
INSTALAB_DB_HOST=127.0.0.1 pytest
npm run -C app build
docker compose -f docker-compose.local.yml -f docker-compose.local-postgres.yml up -d --build
```

Also update:

- `.env.example` when config changes
- [API.md](API.md) when endpoints or response shapes change
- [ARCHITECTURE.md](ARCHITECTURE.md) when worker/data flow changes
- [SECURITY.md](SECURITY.md) when deployment assumptions change

## Branches

- `dev`: default branch and active integration branch
- `main`: stable/public branch when maintained
- Feature branches: scoped work before merge/release

## License

No license file is currently included. Until a license is added, all rights are reserved by default.
