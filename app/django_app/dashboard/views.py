import json
import os
from pathlib import Path
from urllib.parse import urljoin

import requests
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

try:
    from dotenv import load_dotenv

    env_path = Path(os.getenv("INSTALAB_ENV", "/srv/secrets/instalab.env"))
    if env_path.exists():
        load_dotenv(env_path)
except Exception:
    pass

FLASK_BASE = os.getenv("INSTALAB_API_BASE", "http://127.0.0.1:5000/")


def index(request):
    return render(request, "dashboard/index.html")


@csrf_exempt
def api_proxy(request, path: str):
    # Proxy all /api/* calls to the existing Flask backend.
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

    resp = requests.request(
        method,
        target,
        params=params,
        json=json_body,
        data=data,
        headers=headers,
        timeout=60,
    )

    content_type = resp.headers.get("Content-Type", "application/json")
    if "application/json" in content_type:
        try:
            return JsonResponse(resp.json(), status=resp.status_code, safe=False)
        except ValueError:
            return HttpResponse(resp.text, status=resp.status_code, content_type=content_type)
    return HttpResponse(resp.content, status=resp.status_code, content_type=content_type)
