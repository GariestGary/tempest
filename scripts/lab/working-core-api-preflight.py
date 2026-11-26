#!/usr/bin/env python3
"""Exercise the local deployed API without printing synthetic credentials."""
import json
from pathlib import Path
from urllib.parse import urlencode
import requests

private = Path('/var/lib/tempest-live-acceptance-20261126/private')
synthetic = json.loads((private/'synthetic.json').read_text())
password = Path('/var/lib/tempest-core-private/bootstrap-password').read_text().strip()
http = requests.Session()
base = 'http://127.0.0.1:8787/api'

def call(method, path, expected, body=None):
    result = http.request(method, base+path, json=body, timeout=75)
    if result.status_code != expected:
        raise RuntimeError(f'{method} {path.split("?")[0]} returned HTTP {result.status_code}, expected {expected}')
    return result

assert call('GET', '/core', 401).status_code == 401
token = call('POST', '/auth/login', 200, {'username': 'admin', 'password': password}).json()['access_token']
http.headers['Authorization'] = 'Bearer ' + token
state = call('GET', '/core', 200).json()
if state['revision'] != 0 or state['agent'] is None:
    raise RuntimeError('Expected an empty draft with a live Agent; refusing to overwrite')
print('PASS authentication and API-to-Agent AF_UNIX status', flush=True)
tls = {'address':'192.0.2.20', 'port':8443, 'client_id':synthetic['client_id'], 'server_name':'egress.poc.test', 'certificate_sha256':synthetic['certificate_sha256']}
call('PUT', '/core/egress', 204, tls)
state = call('GET', '/core', 200).json()
provider = call('POST', '/core/providers', 201, {'revision':state['revision'], 'name':'Local synthetic provider'}).json()['id']
state = call('GET', '/core', 200).json()
tls_id = call('POST', '/core/egresses', 201, {'revision':state['revision'], 'name':'Synthetic TLS', 'provider_id':provider, 'config':tls}).json()['id']
reality = {'public_key':synthetic['reality_public_key'], 'short_id':synthetic['reality_short_id'], 'fingerprint':'chrome', 'flow':'xtls-rprx-vision'}
state = call('GET', '/core', 200).json()
reality_config = {k:v for k,v in tls.items() if k != 'certificate_sha256'} | {'reality':reality}
reality_id = call('POST', '/core/egresses', 201, {'revision':state['revision'], 'name':'Synthetic Reality', 'provider_id':provider, 'config':reality_config}).json()['id']
state = call('GET', '/core', 200).json()
query = {'type':'tcp', 'security':'reality', 'sni':'egress.poc.test', 'pbk':reality['public_key'], 'sid':reality['short_id'], 'fp':'chrome', 'flow':'xtls-rprx-vision'}
uri = 'vless://' + synthetic['client_id'] + '@192.0.2.20:8443?' + urlencode(query)
uri2 = 'vless://' + synthetic['client_id'] + '@192.0.2.20:8443?' + urlencode(query | {'flow':''})
prefix = '/core/providers/' + provider + '/subscription'
preview = call('POST', prefix+'/preview', 200, {'revision':state['revision'], 'content':uri+'\n'+uri2}).json()
assert [item['status'] for item in preview['results']] == ['duplicate','ready']
assert call('GET', '/core', 200).json()['revision'] == state['revision']
call('POST', prefix+'/commit', 200, {'revision':state['revision'], 'preview_id':preview['preview_id']})
state = call('GET', '/core', 200).json()
generation = state['agent']['active_generation']
call('POST', '/core/egresses/'+reality_id+'/select', 204, {'revision':state['revision']})
state = call('GET', '/core', 200).json()
assert state['vless']['security'] == 'reality' and state['agent']['active_generation'] == generation
call('POST', '/core/egresses/'+tls_id+'/select', 204, {'revision':state['revision']})
state = call('GET', '/core', 200).json()
assert state['vless']['security'] == 'tls' and state['agent']['active_generation'] == generation
print('PASS TLS/Reality catalog, subscription preview/dedup/commit and selection without apply', flush=True)
profile = call('POST', '/core/clients', 201, {'name':'Lab user client'}).content
(private/'user.conf').write_bytes(profile)
(private/'user.conf').chmod(0o600)
state = call('GET', '/core', 200).json()
rules = [{'destination': f'{prefix}{last}/{bits}', 'port':18080, 'egress':egress}
         for prefix,bits in [('198.51.100.',32), ('2001:db8:2::',128)]
         for last,egress in [('10','direct'),('20','vpn'),('30','block')]]
call('PUT', '/core/rules', 204, {'revision':state['revision'], 'rules':rules})
result = call('POST', '/core/preview', 200).json()
assert result['base_generation'] == generation
print('PASS WG profile generation and real Agent preview; apply intentionally not attempted', flush=True)
