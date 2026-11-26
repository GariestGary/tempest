#!/usr/bin/env python3
"""Apply a synthetic invalid UUID and check Agent LKG, then restore the draft."""
from pathlib import Path
import json, uuid
import requests

private=Path('/var/lib/tempest-live-acceptance-20261126/private')
data=json.loads((private/'synthetic.json').read_text())
password=Path('/var/lib/tempest-core-private/bootstrap-password').read_text().strip()
http=requests.Session(); base='http://127.0.0.1:8787/api'
r=http.post(base+'/auth/login',json={'username':'admin','password':password},timeout=10);r.raise_for_status()
http.headers['Authorization']='Bearer '+r.json()['access_token']
tls={'address':'192.0.2.20','port':8443,'client_id':data['client_id'],'server_name':'egress.poc.test','certificate_sha256':data['certificate_sha256']}
def request(method,path,**kwargs):
    r=http.request(method,base+path,timeout=100,**kwargs);r.raise_for_status();return r
before=request('GET','/core').json()['agent']
try:
    request('PUT','/core/egress',json={**tls,'client_id':str(uuid.uuid4())})
    preview=request('POST','/core/preview').json()
    response=http.post(base+'/core/apply',json={'revision':preview['revision'],'base_generation':preview['base_generation']},timeout=100)
    if response.status_code < 400: raise RuntimeError('Invalid UUID unexpectedly applied')
    after=request('GET','/core').json()['agent']
    print('Invalid UUID apply HTTP',response.status_code,'state',after['state'],'active',after['active_generation'],'LKG',after['last_known_good_generation'],flush=True)
    if after['last_known_good_generation']!=before['last_known_good_generation'] or after['active_generation']!=before['active_generation']:
        raise RuntimeError('LKG did not return to previous generation')
finally:
    request('PUT','/core/egress',json=tls)
print('PASS invalid UUID rejected and prior LKG retained; draft restored',flush=True)
