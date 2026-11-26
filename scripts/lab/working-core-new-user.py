#!/usr/bin/env python3
"""Replace the revoked synthetic lab user with a newly issued profile."""
import os
from pathlib import Path
import subprocess
import requests

if os.geteuid()!=0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip()!='ce5fdfe6-0c5d-4487-82d8-f158cdb89609': raise SystemExit('Wrong VM')
private=Path('/var/lib/tempest-live-acceptance-20261126/private')
for name in ('revoked-user.conf','revoked-user.key','new-user.conf'):
    if (private/name).exists(): raise SystemExit('Existing archived/new profile; refusing overwrite')
http=requests.Session();base='http://127.0.0.1:8787/api'
password=Path('/var/lib/tempest-core-private/bootstrap-password').read_text().strip()
r=http.post(base+'/auth/login',json={'username':'admin','password':password},timeout=10);r.raise_for_status()
http.headers['Authorization']='Bearer '+r.json()['access_token']
r=http.get(base+'/core',timeout=10);r.raise_for_status()
if r.json()['clients']: raise SystemExit('Unexpected existing client; refusing changes')
r=http.post(base+'/core/clients',json={'name':'Lab manual client'},timeout=10);r.raise_for_status()
fd=os.open(private/'new-user.conf',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'wb') as f: f.write(r.content)
r=http.post(base+'/core/preview',timeout=30);r.raise_for_status();p=r.json()
r=http.post(base+'/core/apply',json={'revision':p['revision'],'base_generation':p['base_generation']},timeout=100);r.raise_for_status()
print('Applied replacement client, generation',r.json()['active_generation'],flush=True)
subprocess.run(['systemctl','stop','tempest-lab-user.service'],check=True)
if subprocess.run(['ip','-n','tpab-w','link','show','tpab-user'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
    raise RuntimeError('Old client device remains; refusing to replace profile')
oldkey=Path('/etc/wireguard/tempest-lab-user.key')
oldkey.rename(private/'revoked-user.key')
(private/'user.conf').rename(private/'revoked-user.conf')
(private/'new-user.conf').rename(private/'user.conf')
subprocess.run(['systemctl','start','tempest-lab-user.service'],check=True)
print('Replacement profile installed in tpab-w; revoked profile archived privately',flush=True)
