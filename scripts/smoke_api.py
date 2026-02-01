import os
import sys
from pathlib import Path

import server

app = server.app
client = app.test_client()

for path in ("/api/health", "/api/health/detail", "/api/targets_summary"):
    resp = client.get(path)
    print(path, resp.status_code)
    if resp.status_code >= 400:
        try:
            print(resp.get_data(as_text=True))
        except Exception:
            pass
        sys.exit(1)
