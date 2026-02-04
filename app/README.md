# InstaLab Console

Single‑pane console for Instagram snapshot runs, scheduling, history, and cleanup actions.

## Components
- **Flask API** (`server.py`): runs snapshots, schedules, unfollow cleanup, and provides the API.
- **Django UI** (`django_app/`): modern dashboard that proxies `/api/*` to the Flask backend.

## Quick start
```bash
cd /srv/apps/instalab/app
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# copy env and add secrets
cp .env.example .env

# start API
nohup python server.py > server.log 2>&1 &

# start UI
cd django_app
nohup python manage.py runserver 0.0.0.0:8000 > django.log 2>&1 &
```

UI: http://<server-ip>:8000/
API: http://127.0.0.1:5000/api

## Notes
- Secrets are kept in `.env` (not committed). Use `.env.example` as a template.
- Primary storage is Postgres (see `INSTALAB_DB_*` env vars in `.env.example`).
- Settings are stored in the DB and editable via the **Settings** button in the UI.
