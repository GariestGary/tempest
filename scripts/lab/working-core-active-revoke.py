#!/usr/bin/env python3
"""Revoke the UI test peer with live TCP/UDP flows in the approved lab VM."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import requests

if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609':
    raise SystemExit('Wrong lab VM or user')
namespaces = [Path('/proc/1/ns/net'), *(Path('/var/run/netns') / n for n in ('tpab-w','tpab-s','tpab-g','tpab-u'))]
if len({p.stat().st_ino for p in namespaces}) != len(namespaces):
    raise SystemExit('Network namespaces are not distinct')
base = Path('/var/lib/tempest-final-a-20261126')
base.mkdir(mode=0o700, exist_ok=True)
worker = r'''import socket,sys
socks=[]
for addr in ('198.51.100.10','2001:db8:2::10','198.51.100.20','2001:db8:2::20'):
 for kind in (socket.SOCK_STREAM,socket.SOCK_DGRAM):
  s=socket.socket(socket.AF_INET6 if ':' in addr else socket.AF_INET,kind);s.settimeout(2);s.connect((addr,18080));socks.append((addr,kind,s))
def probe(tag,required):
 ok=True
 for addr,kind,s in socks:
  token=(tag+'-'+addr+'-'+str(kind)+'\n').encode()
  try:
   s.sendall(token)
   if kind==socket.SOCK_STREAM:
    data=b''
    while data.count(b'\n') < 2:
     chunk=s.recv(4096)
     if not chunk: break
     data+=chunk
   else: data=s.recv(4096)
   passed=data.startswith(token)
  except OSError: passed=False
  good=passed==required
  print(('PASS' if good else 'FAIL'),tag,addr,'TCP' if kind==socket.SOCK_STREAM else 'UDP',flush=True);ok &= good
 return ok
ok=probe('PRE1',True) and probe('PRE2',True)
print('READY' if ok else 'FAILED',flush=True)
if sys.stdin.readline().strip()=='GO' and ok:
 ok=probe('POSTREVOKE',False)
else: ok=False
sys.exit(0 if ok else 1)
'''
http = requests.Session()
api = 'http://127.0.0.1:8787/api'
r = http.post(api+'/auth/login', json={'username':'admin','password':Path('/var/lib/tempest-core-private/bootstrap-password').read_text().strip()}, timeout=10)
r.raise_for_status()
http.headers['Authorization'] = 'Bearer '+r.json()['access_token']
r = http.get(api+'/core',timeout=10); r.raise_for_status()
clients = [c for c in r.json()['clients'] if c['name'] == 'UI live client']
if len(clients) != 1: raise SystemExit('Expected exactly one UI live client')
captures=[]
try:
    for iface in ('tpab-sg','tpab-su'):
        path=base/('active-revoke-retry-'+iface+'.pcap')
        if path.exists(): raise RuntimeError('Capture exists; refusing overwrite')
        p=subprocess.Popen(['ip','netns','exec','tpab-s','tcpdump','-U','-n','-i',iface,'-w',str(path),'port 18080'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        captures.append((p,path,iface))
    time.sleep(1)
    proc=subprocess.Popen(['ip','netns','exec','tpab-w','/usr/bin/python3','-u','-c',worker],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    while True:
        line=proc.stdout.readline()
        if not line: raise RuntimeError('Flow worker terminated before revocation: '+proc.stderr.read())
        print(line,end='',flush=True)
        if line.strip() == 'FAILED': raise RuntimeError('Baseline flows failed; peer not revoked')
        if line.strip() == 'READY': break
    r=http.delete(api+'/core/clients/'+clients[0]['id'],timeout=100); r.raise_for_status()
    print('PASS API revoke',r.json()['state'],flush=True)
    proc.stdin.write('GO\n');proc.stdin.flush()
    out,err=proc.communicate(timeout=45);print(out,end='',flush=True)
    if proc.returncode: raise RuntimeError('Post-revocation flow test failed: '+err)
    subprocess.run(['/usr/bin/python3','/home/gary/working-core-traffic.py','--fault-control'],check=True,timeout=60)
finally:
    capture_ok = True
    for p,path,iface in captures:
        p.send_signal(signal.SIGINT); _,err=p.communicate(timeout=10)
        raw=path.read_bytes()
        good=b'PRE1-' in raw and b'PRE2-' in raw and b'POSTREVOKE-' not in raw and '0 packets dropped by kernel' in err
        print('PASS' if good else 'FAIL','capture',iface,'baseline markers present, revoked markers absent, zero drops:',good,flush=True)
        capture_ok &= good
    if not capture_ok:
        raise RuntimeError('Capture did not validate the test')
