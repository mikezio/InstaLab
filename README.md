# InstaLab

InstaLab is a single‑pane operations console for Instagram snapshot runs (followers/following), scheduling, run history, insights, and unfollow cleanup. It runs as a 3‑container Docker stack (Flask API, Django UI, VNC/noVNC).

## Repo layout
- `docker-compose.yml` / `docker-compose.preview.yml`
- `Dockerfile`
- `app/` (Flask API, Django UI, workers, scripts)
- `docs/` (overview + operational notes)

## Notes
- Secrets are not committed. Use `.env.example` as a template.
- Runtime data (DB, cookies, job artifacts) should live outside the repo.

## Quick start (local)
1) Copy env template and fill secrets:
   - `cp .env.example .env`
2) Build and run:
   - `docker compose up -d`

## Docs
- `docs/InstaLabOverview.md` (deep dive + diagrams)
- `docs/instalab.md` (ops notes)
