# GitHub Copilot Instructions for InstaLab

## Project Overview

InstaLab is a private Instagram operations console for follower/following snapshots, relationship history, scheduled monitoring, account health, and cleanup workflows.

The active runtime is a two-service app:

- Flask API in `app/server.py`
- Django UI/API proxy in `app/django_app/`

The modern React UI lives in `app/frontend/` and builds into `app/django_app/static/modern/`.

## Current Architecture

- **API/orchestration:** `app/server.py`
- **Workers:** `app/snapshot_worker.py`, `app/count_worker.py`, `app/unfollow_bot.py`
- **Collectors:**
  - `app/browser_tracker.py` for browser/session collection
  - `app/private_api_tracker.py` for `instagrapi` private API collection
- **Persistence:** Postgres only, through `app/db.py` and `app/tracker_db.py`
- **Login secrets/session state:** `app/login_store.py`, encrypted with `INSTALAB_ENCRYPTION_KEY`
- **Modern UI:** `app/frontend/src/`
- **Legacy/Django UI:** `app/django_app/dashboard/`

Do not assume old VNC/noVNC, Selenium, or `instaloader_tracker.py` flows exist. Those are retired from active code paths.

## Local Setup

Use Docker Compose for fresh-clone development:

```bash
cp .env.example .env
docker compose -f docker-compose.local.yml -f docker-compose.local-postgres.yml up -d --build
```

Open:

- UI: `http://localhost:8000/app/`
- API health: `http://localhost:5000/api/health`

## Build And Test

Python tests should run against Postgres:

```bash
INSTALAB_DB_HOST=127.0.0.1 pytest
```

Build UI assets:

```bash
npm run -C app build
```

For dependency install outside Docker:

```bash
pip install -r app/requirements.txt
npm ci --prefix app
npm ci --prefix app/frontend
```

## Development Rules

- Keep secrets out of git. Use `.env` and runtime data directories.
- Update `.env.example` when adding config.
- Update `API.md` when endpoint request/response behavior changes.
- Update `ARCHITECTURE.md` when run lifecycle, collectors, or persistence behavior changes.
- Keep Postgres compatibility in mind for every DB change.
- Avoid reviving retired recon/VNC/Selenium flows.

## Branching

- Work on feature branches.
- Keep `main` public/stable when maintained.
- Use conventional commits where practical.
