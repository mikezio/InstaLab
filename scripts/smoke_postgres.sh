#!/usr/bin/env bash
set -euo pipefail
ENV_FILE=${1:-/tmp/instalab-smoke-pg.env}
cat <<'EOT' > "$ENV_FILE"
INSTALAB_DB_TYPE=postgres
INSTALAB_DB_HOST=127.0.0.1
INSTALAB_DB_PORT=5433
INSTALAB_DB_NAME=instalab_smoke
INSTALAB_DB_USER=instalab_smoke
INSTALAB_DB_PASS=instalab_smoke
INSTALAB_COOKIE_DIR=/tmp/instalab-smoke-cookies-pg
INSTALAB_LOGINS_FILE=/tmp/instalab-smoke-logins-pg.json
INSTALAB_API_BASE=http://127.0.0.1:5000/
EOT

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for postgres smoke test" >&2
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -q '^instalab-smoke-pg$'; then
  docker run -d --rm --name instalab-smoke-pg \
    -e POSTGRES_DB=instalab_smoke \
    -e POSTGRES_USER=instalab_smoke \
    -e POSTGRES_PASSWORD=instalab_smoke \
    -p 5433:5432 \
    postgres:16 >/dev/null
fi

# Wait for DB
for _ in {1..60}; do
  if docker exec instalab-smoke-pg pg_isready -U instalab_smoke -d instalab_smoke >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Give Postgres a moment to finish startup after pg_isready flips.
sleep 2

for _ in {1..5}; do
  if PYTHONPATH=/srv/apps/instalab/app \
    INSTALAB_ENV="$ENV_FILE" \
    /tmp/instalab-review-venv/bin/python /srv/apps/instalab/scripts/smoke_api.py; then
    exit 0
  fi
  sleep 1
done

exit 1
