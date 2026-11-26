#!/usr/bin/env python3
"""Apply the current reviewed synthetic draft via the deployed local API."""
from pathlib import Path
import requests

http = requests.Session()
base = 'http://127.0.0.1:8787/api'
password = Path('/var/lib/tempest-core-private/bootstrap-password').read_text().strip()
response = http.post(base+'/auth/login', json={'username':'admin', 'password':password}, timeout=10)
response.raise_for_status()
http.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
preview = http.post(base+'/core/preview', timeout=30)
preview.raise_for_status()
value = preview.json()
print('Preview succeeded; applying reviewed synthetic draft', flush=True)
result = http.post(base+'/core/apply', json={'revision':value['revision'], 'base_generation':value['base_generation']}, timeout=100)
print(f'Apply returned HTTP {result.status_code}', flush=True)
result.raise_for_status()
status = result.json()
print('Agent state:', status['state'], 'generation:', status['active_generation'], 'LKG:', status['last_known_good_generation'], flush=True)
