# InstaLab

InstaLab is an operations console for Instagram snapshot runs (followers/following), scheduling, run history, insights, and unfollow cleanup. It runs as a three-container stack: Flask API, Django UI, and a VNC/noVNC helper for interactive login workflows.

## What you get
- Single-pane UI for queueing runs, viewing history, and monitoring status.
- Snapshot/count workers with safeguards to avoid overlapping runs.
- Accounts manager with session/cookie bootstrap and 2FA flow support.
- Health endpoints and metrics-friendly status surfaces.

## Repo layout
- `docker-compose.yml` / `docker-compose.preview.yml`
- `docker-compose.local.yml` (one-command local setup)
- `Dockerfile`
- `app/` (Flask API, Django UI, workers, scripts, UI build pipeline)

## Quick start (local)
1) Create a local env:
   - `cp .env.example .env`
2) Build and run:
   - `docker compose -f docker-compose.local.yml up -d --build`
   - Or: `./scripts/local_up.sh`
3) Open UI:
   - http://localhost:8000
4) Optional VNC login helper:
   - http://localhost:7900

## Local with Postgres (optional)
If you prefer Postgres instead of SQLite:
- `docker compose -f docker-compose.local.yml -f docker-compose.local-postgres.yml up -d --build`
- Or: `./scripts/local_postgres_up.sh`

## Environment and secrets
- Secrets are not committed. Use `.env.example` as a template.
- Runtime data (DB, cookies, job artifacts) should live outside the repo.

## UI build (Tailwind)
- Source: `app/ui_src/ui.css`
- Output: `app/django_app/static/ui.css`
- Build once: `cd app && npm run build`
- Watch: `cd app && npm run dev`

## Operational notes
- This repo contains code and compose; environment-specific ops runbooks live outside the repo.

## Branching
- `dev`: active development
- `main`: stable / public-ready

## Release checklist
- Update version / changelog (if you keep one)
- Run UI build (`cd app && npm run build`)
- Verify API + UI start clean (`docker compose up -d`)
- Sanity check a run end-to-end in dev
- Merge `dev` -> `main`
- Tag release (`git tag vX.Y.Z && git push --tags`)
