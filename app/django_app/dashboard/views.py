import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv

    env_path = Path(os.getenv("INSTALAB_ENV", "/srv/secrets/instalab.env"))
    if env_path.exists():
        load_dotenv(env_path)
except Exception as e:
    logger.warning(f"Failed to load environment variables: {e}")

FLASK_BASE = os.getenv("INSTALAB_API_BASE", "http://127.0.0.1:5000/")


def index(request):
    return render(request, "dashboard/index.html")


@csrf_exempt  # Note: CSRF exemption required for API proxy; Flask backend should validate requests
def api_proxy(request, path: str):
    """
    Proxy all /api/* calls to the existing Flask backend.
    
    Security note: Path is validated to prevent path traversal attacks.
    CSRF is exempted because this is an API proxy to a Flask backend,
    but the Flask backend should implement its own validation.
    """
    # Validate path to prevent path traversal and malicious input
    # Allow alphanumeric, hyphens, underscores, slashes, and dots but prevent ../ patterns
    if not re.match(r'^[a-zA-Z0-9/_.-]+$', path) or '..' in path:
        logger.warning(f"Invalid API path rejected: {path}")
        return JsonResponse({"error": "Invalid API path"}, status=400)
    
    target = urljoin(FLASK_BASE, f"api/{path}")
    method = request.method
    headers = {"Content-Type": request.content_type} if request.content_type else {}
    params = request.GET.dict()

    data = None
    json_body = None
    if request.body:
        if request.content_type and "application/json" in request.content_type:
            try:
                json_body = json.loads(request.body.decode("utf-8"))
            except json.JSONDecodeError:
                data = request.body
        else:
            data = request.body

    try:
        resp = requests.request(
            method,
            target,
            params=params,
            json=json_body,
            data=data,
            headers=headers,
            timeout=60,
        )
    except requests.RequestException as e:
        logger.error(f"API proxy request failed: {e}")
        return JsonResponse({"error": "Backend request failed"}, status=502)

    content_type = resp.headers.get("Content-Type", "application/json")
    if "application/json" in content_type:
        try:
            return JsonResponse(resp.json(), status=resp.status_code, safe=False)
        except ValueError:
            return HttpResponse(resp.text, status=resp.status_code, content_type=content_type)
    return HttpResponse(resp.content, status=resp.status_code, content_type=content_type)
