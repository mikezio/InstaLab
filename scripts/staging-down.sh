#!/usr/bin/env bash
set -euo pipefail
cd /srv/apps/instalab-staging
docker compose -f docker-compose.staging.yml --project-name instalab-staging down
