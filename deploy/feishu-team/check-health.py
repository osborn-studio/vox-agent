#!/usr/bin/env python3
"""No employee data: report service availability and root disk headroom."""
import json
import shutil
import time
import urllib.request

for attempt in range(3):
    try:
        with urllib.request.urlopen('https://api.osbornvox.com/healthz', timeout=15) as response:
            if json.load(response).get('status') != 'ok':
                raise ValueError('Unhealthy response')
        break
    except Exception:
        if attempt == 2:
            raise SystemExit('Vox HTTPS health failed after three attempts') from None
        time.sleep(2)
total, used, free = shutil.disk_usage('/')
if free / total < 0.10:
    raise SystemExit('Vox host disk below 10% free')
print('Vox HTTPS healthy; root disk has at least 10% free')
