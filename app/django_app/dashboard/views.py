import json
import logging
import os
import re
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin

import requests
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv

    env_path = Path(os.getenv("INSTALAB_ENV", "/srv/secrets/instalab.env"))
    if env_path.exists():
        load_dotenv(env_path)
except Exception as e:
    logger.warning(f"Failed to load environment variables: {e}")

FLASK_BASE = os.getenv("INSTALAB_API_BASE", "http://127.0.0.1:5000/")
SECRET_DROP_DIR = Path(os.getenv("INSTALAB_SECRET_DROP_DIR", "/srv/secrets/secret-drop"))


def index(request):
    return render(request, "dashboard/index.html")


def healthz(request):
    return JsonResponse({"status": "ok"})


@require_http_methods(["GET", "POST"])
@csrf_exempt
def secret_drop(request):
    status = None
    error = None
    if request.method == "POST":
        secret = (request.POST.get("secret") or "").strip()
        uploads = request.FILES.getlist("secret_file")
        filename_raw = ""
        package_raw = ""
        context_text = (request.POST.get("file_context") or "").strip()
        context_map_raw = (request.POST.get("file_context_map") or "").strip()
        if not secret and not uploads:
            error = "Provide text or upload a file."
        else:
            safe_name = ""
            safe_package = ""
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                SECRET_DROP_DIR.mkdir(parents=True, exist_ok=True)
                saved = []
                if secret:
                    filename = safe_name or f"secret_{ts}.txt"
                    path = SECRET_DROP_DIR / filename
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(secret)
                    saved.append({"path": str(path), "bytes": len(secret)})

                for upload in uploads or []:
                    raw_name = re.sub(r"[^a-zA-Z0-9._-]+", "", upload.name)[:80] or f"upload_{ts}"
                    filename = safe_name if (safe_name and len(uploads or []) == 1 and not secret) else raw_name
                    path = SECRET_DROP_DIR / filename
                    suffix = 1
                    while path.exists():
                        path = SECRET_DROP_DIR / f"{path.stem}_{suffix}{path.suffix}"
                        suffix += 1
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as handle:
                        for chunk in upload.chunks():
                            handle.write(chunk)
                    size = getattr(upload, "size", None)
                    saved.append({"path": str(path), "bytes": size if size is not None else path.stat().st_size})

                package_path = None
                context_lines = []
                if context_map_raw:
                    try:
                        parsed = json.loads(context_map_raw)
                        if isinstance(parsed, list):
                            for entry in parsed:
                                if not isinstance(entry, dict):
                                    continue
                                name = (entry.get("name") or "").strip()
                                ctx = (entry.get("context") or "").strip()
                                size = entry.get("size")
                                if not name or not ctx:
                                    continue
                                suffix = f" ({size} bytes)" if isinstance(size, int) else ""
                                context_lines.append(f"{name}{suffix}: {ctx}")
                    except Exception:
                        pass
                if context_text:
                    context_lines.append(context_text)
                if (uploads or secret) and context_lines:
                    package_name = safe_package or f"package_{ts}.zip"
                    package_path = SECRET_DROP_DIR / package_name
                    import zipfile
                    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        for item in saved:
                            src = Path(item["path"])
                            zf.write(src, arcname=src.name)
                        zf.writestr("context.txt", "\n".join(context_lines).strip() + "\n")

                status = {
                    "files": saved,
                    "package": str(package_path) if package_path else None,
                }
            except FileExistsError:
                error = "That filename already exists. Choose another name."
            except Exception as exc:
                logger.error("Secret drop failed: %s", exc)
                error = "Could not store secret. Check server logs for details."
    return render(
        request,
        "dashboard/secret_drop.html",
        {"status": status, "error": error, "drop_dir": str(SECRET_DROP_DIR)},
    )


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
