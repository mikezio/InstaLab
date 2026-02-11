# GitHub Copilot Instructions for InstaLab

## Project Overview
InstaLab is an operations console for Instagram snapshot runs (followers/following), scheduling, run history, insights, and unfollow cleanup. It runs as a three-container stack: Flask API, Django UI, and a VNC/noVNC helper for interactive login workflows.

## Architecture
- **Backend API**: Flask (Python 3.11) - serves REST endpoints
- **Web UI**: Django (Python 3.11) - admin and dashboard interface
- **Workers**: Python workers for snapshots, counts, and unfollow operations
- **Frontend**: Tailwind CSS 3.x for styling
- **Database**: PostgreSQL only
- **Deployment**: Docker Compose with multi-container orchestration

## Tech Stack
- **Python**: 3.11
  - Flask 3.x (API server)
  - Django 5.x (UI framework)
  - Instaloader 4.x (Instagram operations)
  - Selenium & Playwright (browser automation)
  - APScheduler (job scheduling)
  - Gunicorn (production WSGI server)
- **Node.js**: 20
  - Tailwind CSS 3.x (utility-first CSS)
  - PostCSS & Autoprefixer
- **Docker**: Multi-stage builds with docker-compose orchestration

## Key File Locations
- **API Server**: `app/server.py` (Flask)
- **Django App**: `app/dj.py` and `app/django_app/`
- **Workers**: `app/snapshot_worker.py`, `app/count_worker.py`, `app/unfollow_bot.py`
- **Trackers**: `app/instaloader_tracker.py`, `app/selenium_tracker.py`
- **UI Styles**: `app/ui_src/ui.css` → builds to `app/django_app/static/ui.css`
- **Dependencies**: `app/requirements.txt` (Python), `app/package.json` (Node)

## Development Setup

### Local Development
```bash
# Copy environment template
cp .env.example .env

# Build and start all services
docker compose -f docker-compose.local.yml up -d --build
# OR use the script
./scripts/local_up.sh

# Access the UI
# UI: http://localhost:8000
# VNC Helper: http://localhost:7900
```

### With PostgreSQL
```bash
docker compose -f docker-compose.local.yml -f docker-compose.local-postgres.yml up -d --build
# OR use the script
./scripts/local_postgres_up.sh
```

### UI Development (Tailwind CSS)
```bash
cd app

# Install dependencies
npm ci

# One-time build
npm run build

# Watch mode for development
npm run dev
```

## Build & Test Commands

### Python
```bash
cd app

# Install dependencies
pip install -r requirements.txt

# Smoke test imports
python -c "import server; import instaloader_tracker; print('python ok')"
```

### Frontend
```bash
cd app

# Install dependencies
npm ci

# Build CSS
npm run build

# Watch mode
npm run dev
```

## Branching Strategy
- `main`: Stable, public-ready releases
- `dev`: Active development branch
- Feature branches: Create from `dev`, merge back to `dev`

## Code Style Guidelines
- Python code should follow PEP 8 conventions
- Use clear, descriptive variable and function names
- Keep functions focused and single-purpose
- Add docstrings for complex functions
- Avoid committing secrets or sensitive data - use `.env` files

## Environment Variables
- Never commit secrets or sensitive data
- Use `.env.example` as a template for required variables
- Store runtime data (DB, cookies, artifacts) outside the repository

## Release Process
1. Update version/changelog (if maintained)
2. Run UI build: `cd app && npm run build`
3. Verify services start cleanly: `docker compose up -d`
4. Test a complete run end-to-end in dev environment
5. Merge `dev` → `main`
6. Tag release: `git tag vX.Y.Z && git push --tags`

## Testing
- CI runs Python import smoke tests
- Manual end-to-end testing is performed in dev environment
- Verify PostgreSQL behavior for all DB changes

## Docker Services
- **Flask API**: Serves backend endpoints
- **Django UI**: Admin console and dashboard
- **VNC/noVNC**: Interactive login helper for 2FA workflows
- Services are orchestrated via docker-compose files

## Common Tasks

### Making Code Changes
- Backend API changes: Edit `app/server.py` or related modules
- UI changes: Edit Django templates in `app/django_app/` and styles in `app/ui_src/`
- Worker changes: Edit worker files in `app/` directory
- Always rebuild CSS after style changes: `npm run build`

### Adding Dependencies
- Python: Add to `app/requirements.txt`
- Node.js: Use `npm install --save-dev <package>` in `app/` directory

### Database Changes
- InstaLab is Postgres-only (`INSTALAB_DB_TYPE=postgres`)
- Test schema and query changes against Postgres
- Keep migration scripts in appropriate locations

## Important Notes
- This repo is Docker-first; local dev runs in containers
- Workers have safeguards to avoid overlapping runs
- Session/cookie management supports 2FA flows
- Health endpoints and metrics are available for monitoring
- VNC helper UI available at port 7900 for interactive login workflows
