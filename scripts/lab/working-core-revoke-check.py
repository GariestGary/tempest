#!/usr/bin/env python3
"""Revoke/check the one synthetic lab user; capture sink-side leakage."""
import configparser
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import requests

if os.geteuid()!=0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip()!='ce5fdfe6-0c5d-4487-82d8-f158cdb89609': raise SystemExit('Wrong VM')
if len(sys.argv)!=2 or sys.argv[1] not in ('initial','restart','reboot'): raise SystemExit('Expected initial, restart or reboot')
phase=sys.argv[1]; base=Path('/var/lib/tempest-live-acceptance-20261126')
http=requests.Session(); api='http://127.0.0.1:8787/api'
password=Path('/var/lib/tempest-core-private/bootstrap-password').read_text().strip()
r=http.post(api+'/auth/login',json={'username':'admin','password':password},timeout=10);r.raise_for_status()
http.headers['Authorization']='Bearer '+r.json()['access_token']
r=http.get(api+'/core',timeout=15);r.raise_for_status();state=r.json()
if phase=='initial':
    clients=[c for c in state['clients'] if c['name']=='Lab user client']
    if len(clients)!=1: raise RuntimeError('Expected exactly one named lab client')
    r=http.delete(api+'/core/clients/'+clients[0]['id'],timeout=100);r.raise_for_status()
    print('Revocation API status',r.json()['state'],flush=True)
else:
    if any(c['name']=='Lab user client' for c in state['clients']): raise RuntimeError('Revoked client still in draft')
cfg=configparser.ConfigParser(interpolation=None);cfg.read(base/'private/user.conf')
public=cfg['Peer']['PublicKey']
def run(*args): subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
run('ip','netns','exec','tpab-w','wg','set','tpab-user','peer',public,'remove')
run('ip','netns','exec','tpab-w','wg','set','tpab-user','peer',public,'allowed-ips','0.0.0.0/0,::/0','endpoint','192.0.2.1:51871','persistent-keepalive','10')
captures=[];ok=True
filt='(dst net 198.51.100.0/24 or dst net 2001:db8:2::/64) and (tcp or udp)'
try:
    for iface in ('tpab-sg','tpab-su'):
        path=base/('revoke-'+phase+'-'+iface+'.pcap')
        if path.exists(): raise RuntimeError('Existing capture; refusing overwrite')
        p=subprocess.Popen(['ip','netns','exec','tpab-s','tcpdump','-U','-n','-i',iface,'-w',str(path),filt],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        captures.append((p,path,iface))
    time.sleep(1)
    ok &= subprocess.run(['/usr/bin/python3','/home/gary/working-core-traffic.py','--revoked-only'],timeout=60).returncode==0
finally:
    for p,path,iface in captures:
        p.send_signal(signal.SIGINT);_,err=p.communicate(timeout=10)
        lines=subprocess.run(['tcpdump','-nn','-r',str(path)],capture_output=True,text=True,check=True).stdout.splitlines()
        good=not lines and '0 packets dropped by kernel' in err
        print('PASS' if good else 'FAIL','revocation capture',phase,iface,'packets',len(lines),flush=True);ok &= good
out=subprocess.run(['ip','netns','exec','tpab-w','wg','show','tpab-user','latest-handshakes'],capture_output=True,text=True,check=True).stdout.strip().splitlines()
no_handshake=len(out)==1 and int(out[0].split()[-1])==0
print('PASS' if no_handshake else 'FAIL','revoked peer new handshake',phase,flush=True);ok &= no_handshake
ok &= subprocess.run(['/usr/bin/python3','/home/gary/working-core-traffic.py','--fault-control'],timeout=60).returncode==0
if not ok: sys.exit(1)
print('PASS revocation',phase,flush=True)
