#!/usr/bin/env bash
set -euo pipefail

# Run Django migrations
echo "Running Django migrations..."
cd /app/django_app
python manage.py migrate --noinput

# Start gunicorn
echo "Starting Django UI with gunicorn..."
# Check if we should use ddtrace (when DD_TRACE_ENABLED is true)
if [ "${DD_TRACE_ENABLED:-false}" = "true" ] && command -v ddtrace-run >/dev/null 2>&1; then
    exec ddtrace-run gunicorn controlpanel.wsgi:application --chdir /app/django_app --bind 0.0.0.0:8000 --workers 3 --timeout 120
else
    exec gunicorn controlpanel.wsgi:application --chdir /app/django_app --bind 0.0.0.0:8000 --workers 3 --timeout 120
fi
