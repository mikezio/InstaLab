#!/usr/bin/env python3
"""
Run Blackbird from a writable runtime directory.

Blackbird writes results relative to its own source tree. In our container,
/opt/blackbird is owned by root and not writable by the app user, so we copy
it into /tmp once and execute from there.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


SRC_DIR = Path("/opt/blackbird")
RUNTIME_DIR = Path("/tmp/instalab-blackbird")
MARKER = RUNTIME_DIR / ".ready"


def _ensure_runtime_copy() -> None:
    if MARKER.exists() and (RUNTIME_DIR / "blackbird.py").exists():
        return
    if RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR, ignore_errors=True)
    shutil.copytree(SRC_DIR, RUNTIME_DIR)
    (RUNTIME_DIR / "results").mkdir(parents=True, exist_ok=True)
    MARKER.write_text("ok\n", encoding="utf-8")


def main() -> int:
    _ensure_runtime_copy()
    os.chdir(RUNTIME_DIR)
    cmd = [sys.executable, str(RUNTIME_DIR / "blackbird.py"), *sys.argv[1:]]
    env = dict(os.environ)
    proc = subprocess.run(cmd, env=env, check=False)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
