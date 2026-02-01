# InstaLab

InstaLab is an operations console for Instagram snapshot runs (followers/following), scheduling, run history, insights, and unfollow cleanup. It runs as a three-container stack: Flask API, Django UI, and a VNC/noVNC helper for interactive login workflows.

## What you get
- Single-pane UI for queueing runs, viewing history, and monitoring status.
- Snapshot/count workers with safeguards to avoid overlapping runs.
- Accounts manager with session/cookie bootstrap and 2FA flow support.
- Health endpoints and metrics-friendly status surfaces.

## Repo layout
- `docker-compose.yml` / `docker-compose.preview.yml`
- `Dockerfile`
- `app/` (Flask API, Django UI, workers, scripts, UI build pipeline)

## Quick start (local)
1) Copy env template and fill secrets:
   - `cp .env.example .env`
2) Build and run:
   - `docker compose up -d`
3) Open UI:
   - http://localhost:8000 (if your compose exposes it)

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
