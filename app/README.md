# InstaLab App Directory

This directory contains the runnable application code for InstaLab.

For a fresh clone, start from the root [README](../README.md). The recommended setup is Docker Compose with Postgres:

```bash
cp .env.example .env
docker compose -f docker-compose.local.yml -f docker-compose.local-postgres.yml up -d --build
```

## Key Files

- `server.py`: Flask API, job orchestration, scheduling, config, integrations
- `snapshot_worker.py`: snapshot worker entry point
- `count_worker.py`: count-watch worker
- `unfollow_bot.py`: cleanup/unfollow worker
- `browser_tracker.py`: browser/session collector
- `private_api_tracker.py`: `instagrapi` private API collector
- `tracker_db.py`: run persistence, relationship events, history rebuilds
- `login_store.py`: encrypted login/session storage
- `django_app/`: Django UI and API proxy
- `frontend/`: modern React UI
- `tests/`: pytest suite

## Local Commands

Run tests against the local Compose Postgres port:

```bash
INSTALAB_DB_HOST=127.0.0.1 pytest
```

Build UI assets:

```bash
npm run -C app build
```

Install dependencies outside Docker:

```bash
pip install -r app/requirements.txt
npm ci --prefix app
npm ci --prefix app/frontend
```

## Runtime Data

Runtime files should not be committed. Keep these in Docker volumes, `/data/instalab`, or another external runtime path:

- browser auth storage
- private API session settings
- job artifacts
- logs
- SQLite scratch files
- `.env`
