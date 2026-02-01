#!/usr/bin/env bash
set -euo pipefail

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit it if needed, then re-run." >&2
  exit 0
fi

docker compose -f docker-compose.local.yml up -d --build
