# InstaLab Frontend

This directory contains the modern React operator UI. Production builds are emitted into `app/django_app/static/modern/`.

## Dev workflow

- Start the Vite dev server from `app/`:
  - `npm run frontend:dev`
- Build production assets from `app/`:
  - `npm run build`

## Operator surfaces

- `Targets`: primary launch surface for tracked accounts, first runs, and schedule creation
- `Activity`: run explorer and recent relationship evidence
- `Operations`: queue state, live jobs, logs, and manual operator controls
- `Accounts`: collector onboarding, maintenance, diagnostics, and factory flows
- `Settings`: runtime config, backend/profile selection, and proxy testing

## Routing

The React app is mounted at `/app/` and defines these primary routes:

- `/app/` and `/app/digest`
- `/app/targets` and `/app/subjects`
- `/app/network` and `/app/people`
- `/app/activity` and `/app/explorer`
- `/app/system` and `/app/machinery`
- `/app/operations`
- `/app/unfollow`
- `/app/accounts`
- `/app/settings`

## API usage

- `src/lib/api.ts` talks to the Django-proxied API base `/api`
- Active queue state comes from `GET /api/status`
- One-off runs are launched with `POST /api/run`
- Per-job polling uses `GET /api/run/<job_id>` and `GET /api/jobs/latest?login_username=<login>`
- Schedules use `GET/POST/DELETE /api/schedules`

## Notes

- Generated assets under `app/django_app/static/modern/assets/` are build output; edit `src/` instead.
- The legacy Django templates still exist, but the current operator flow is documented against the modern React UI.
