#!/usr/bin/env bash
set -euo pipefail
ENV_FILE=${1:-/tmp/instalab-smoke-sqlite.env}
cat <<'EOT' > "$ENV_FILE"
INSTALAB_DB_TYPE=sqlite
INSTALAB_SQLITE_PATH=/tmp/instalab-smoke.db
INSTALAB_COOKIE_DIR=/tmp/instalab-smoke-cookies
INSTALAB_LOGINS_FILE=/tmp/instalab-smoke-logins.json
INSTALAB_API_BASE=http://127.0.0.1:5000/
EOT

PYTHONPATH=/srv/apps/instalab/app \
  INSTALAB_ENV="$ENV_FILE" \
  /tmp/instalab-review-venv/bin/python /srv/apps/instalab/scripts/smoke_api.py
