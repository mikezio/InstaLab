from __future__ import annotations

import os
from pathlib import Path


def app_version(request):
    version = os.getenv("INSTALAB_VERSION", "").strip()
    if not version:
        # VERSION sits at repo root (/app/VERSION in containers).
        base_dir = Path(__file__).resolve().parent.parent
        version_file = base_dir.parent / "VERSION"
        try:
            version = version_file.read_text().strip()
        except OSError:
            version = "dev"
    return {"APP_VERSION": version}
