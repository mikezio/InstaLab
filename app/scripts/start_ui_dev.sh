#!/usr/bin/env bash
set -euo pipefail

# Run Django migrations
echo "Running Django migrations..."
cd /app/django_app
python manage.py migrate --noinput

# Start Django development server
echo "Starting Django UI development server..."
exec python manage.py runserver 0.0.0.0:8000
