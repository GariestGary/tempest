#!/usr/bin/env python3
"""Apply the synthetic Reality exit and test the real WG client; restore TLS."""
from pathlib import Path
import json
import os
import subprocess
import requests

if os.geteuid()!=0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip()!='ce5fdfe6-0c5d-4487-82d8-f158cdb89609': raise SystemExit('Wrong VM')
private=Path('/var/lib/tempest-live-acceptance-20261126/private')
http=requests.Session(); base='http://127.0.0.1:8787/api'
password=Path('/var/lib/tempest-core-private/bootstrap-password').read_text().strip()
r=http.post(base+'/auth/login',json={'username':'admin','password':password},timeout=10);r.raise_for_status()
http.headers['Authorization']='Bearer '+r.json()['access_token']
def cmd(*args): subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
def apply(security):
    r=http.get(base+'/core',timeout=10);r.raise_for_status();state=r.json()
    entries=[v for v in state['saved_egresses'] if v['name']==('Synthetic Reality' if security=='reality' else 'Synthetic TLS') and v['security']==security]
    if len(entries)!=1: raise RuntimeError('Unexpected egress catalog')
    r=http.post(base+'/core/egresses/'+entries[0]['id']+'/select',json={'revision':state['revision']},timeout=10);r.raise_for_status()
    r=http.post(base+'/core/preview',timeout=30);r.raise_for_status();p=r.json()
    r=http.post(base+'/core/apply',json={'revision':p['revision'],'base_generation':p['base_generation']},timeout=100);r.raise_for_status()
    print('Applied',security,'generation',r.json()['active_generation'],flush=True)
common=['systemd-run','--property=NoNewPrivileges=yes','--property=PrivateDevices=yes','--property=ProtectSystem=strict','--property=CapabilityBoundingSet=']
for unit in ('tempest-lab-reality-decoy','tempest-lab-reality-upstream'):
    state=subprocess.run(['systemctl','show',unit,'-p','LoadState','--value'],capture_output=True,text=True,check=True).stdout.strip()
    if state!='not-found': raise SystemExit('Reality test unit already exists: '+unit)
active=lambda unit: subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
upstream='tempest-lab-upstream-boot' if active('tempest-lab-upstream-boot') else 'tempest-lab-upstream'
if not active(upstream): raise SystemExit('Expected TLS lab upstream is not active')
cmd('systemctl','stop',upstream)
try:
    cmd(*common,'--unit=tempest-lab-reality-decoy','--property=NetworkNamespacePath=/run/netns/tpab-u','/usr/bin/openssl','s_server','-accept','127.0.0.1:8443','-cert',str(private/'tls.crt'),'-key',str(private/'tls.key'),'-alpn','h2','-www')
    cmd(*common,'--unit=tempest-lab-reality-upstream','--property=NetworkNamespacePath=/run/netns/tpab-u','/opt/tempest/bin/xray','run','-config',str(private/'upstream-reality.json'))
    apply('reality')
    subprocess.run(['/usr/bin/python3','/home/gary/working-core-traffic.py','--warmup'],check=True,timeout=40)
    subprocess.run(['/usr/bin/python3','/home/gary/working-core-traffic.py','--positive-only'],check=True,timeout=60)
    print('PASS real WG user -> Reality/Vision -> sink and DNS',flush=True)
    material=json.loads((private/'synthetic.json').read_text())
    before=http.get(base+'/core',timeout=10);before.raise_for_status();lkg=before.json()['agent']['last_known_good_generation']
    sid=material['reality_short_id']; bad=('0' if sid[0]!='0' else '1')+sid[1:]
    wrong={'address':'192.0.2.20','port':8443,'client_id':material['client_id'],'server_name':'egress.poc.test',
           'reality':{'public_key':material['reality_public_key'],'short_id':bad,'fingerprint':'chrome','flow':'xtls-rprx-vision'}}
    r=http.put(base+'/core/egress',json=wrong,timeout=10);r.raise_for_status()
    r=http.post(base+'/core/preview',timeout=30);r.raise_for_status();p=r.json()
    r=http.post(base+'/core/apply',json={'revision':p['revision'],'base_generation':p['base_generation']},timeout=100)
    if r.status_code < 400: raise RuntimeError('Invalid Reality short ID unexpectedly applied')
    current=http.get(base+'/core',timeout=10);current.raise_for_status();state=current.json()['agent']
    if state['last_known_good_generation']!=lkg or state['active_generation']!=lkg: raise RuntimeError('Reality LKG not restored')
    print('PASS invalid Reality short ID rejected; prior Reality LKG restored',flush=True)
    subprocess.run(['/usr/bin/python3','/home/gary/working-core-traffic.py','--warmup'],check=True,timeout=40)
    subprocess.run(['/usr/bin/python3','/home/gary/working-core-traffic.py','--positive-only'],check=True,timeout=60)
finally:
    for unit in ('tempest-lab-reality-upstream','tempest-lab-reality-decoy'):
        subprocess.run(['systemctl','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
    if upstream=='tempest-lab-upstream-boot':
        cmd('systemctl','start',upstream)
    else:
        cmd(*common,'--unit=tempest-lab-upstream','--property=NetworkNamespacePath=/run/netns/tpab-u','/opt/tempest/bin/xray','run','-config',str(private/'upstream-tls.json'))
    apply('tls')
