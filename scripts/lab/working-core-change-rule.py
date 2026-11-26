#!/usr/bin/env python3
"""Change the lab user's explicit .10 rule and apply through the real API."""
from pathlib import Path
import sys
import requests

if sys.argv[1:] not in (['vpn'], ['direct']):
    raise SystemExit('Expected vpn or direct')
target = sys.argv[1]
http=requests.Session(); base='http://127.0.0.1:8787/api'
password=Path('/var/lib/tempest-core-private/bootstrap-password').read_text().strip()
r=http.post(base+'/auth/login',json={'username':'admin','password':password},timeout=10);r.raise_for_status()
http.headers['Authorization']='Bearer '+r.json()['access_token']
r=http.get(base+'/core',timeout=10);r.raise_for_status();state=r.json()
rules=state['rules']
matches=[rule for rule in rules if rule['destination'] in ('198.51.100.10/32','2001:db8:2::10/128') and rule['port']==18080]
if len(matches)!=2: raise RuntimeError('Unexpected lab draft; refusing change')
for rule in matches: rule['egress']=target
r=http.put(base+'/core/rules',json={'revision':state['revision'],'rules':rules},timeout=10);r.raise_for_status()
r=http.post(base+'/core/preview',timeout=30);r.raise_for_status();preview=r.json()
r=http.post(base+'/core/apply',json={'revision':preview['revision'],'base_generation':preview['base_generation']},timeout=100);r.raise_for_status()
print('Applied explicit .10 user rules as',target,'generation',r.json()['active_generation'])
