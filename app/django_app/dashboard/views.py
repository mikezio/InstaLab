import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
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
    variant = str(getattr(settings, "INSTALAB_UI_VARIANT", "legacy")).lower()
    if variant == "modern":
        return redirect("modern_app")
    return render(request, "dashboard/index.html")


def legacy_index(request):
    return render(request, "dashboard/index.html")


def modern_app(request):
    return render(request, "dashboard/modern_index.html")


def modern_shortcut(request, subpath: str = ""):
    clean = (subpath or "").strip("/")
    if clean:
        return redirect(f"/app/{clean}")
    return redirect("/app/")


def healthz(request):
    return JsonResponse({"status": "ok"})


@csrf_exempt  # Note: CSRF exemption required for API proxy; Flask backend should validate requests
def api_proxy(request, path: str):
    """
    Proxy all /api/* calls to the existing Flask backend.
    
    Security note: Path is validated to prevent path traversal attacks.
    CSRF is exempted because this is an API proxy to a Flask backend,
    but the Flask backend should implement its own validation.
    """
    # Validate path to prevent path traversal and malicious input
    # Normalize the path first to resolve any .. or . segments
    import os
    normalized_path = os.path.normpath(path)
    
    # Check for directory traversal attempts
    # The normalized path should not start with .. or contain absolute path indicators
    if normalized_path.startswith('..') or os.path.isabs(normalized_path):
        logger.warning(f"Path traversal attempt rejected: {path}")
        return JsonResponse({"error": "Invalid API path"}, status=400)
    
    # Only allow alphanumeric, hyphens, underscores, and slashes (no dots)
    # This prevents any further path manipulation attempts
    if not re.match(r'^[a-zA-Z0-9/_-]+$', normalized_path):
        logger.warning(f"Invalid API path characters rejected: {path}")
        return JsonResponse({"error": "Invalid API path"}, status=400)
    
    target = urljoin(FLASK_BASE, f"api/{normalized_path}")
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
