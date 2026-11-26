#!/usr/bin/env python3
"""Exercise the synthetic Reality server directly, without the WG dataplane."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time

if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609':
    raise SystemExit('Wrong VM or user')
private = Path('/var/lib/tempest-live-acceptance-20261126/private')
data = json.loads((private/'synthetic.json').read_text())
client = {'log': {'loglevel':'warning'},
          'inbounds':[{'listen':'127.0.0.1','port':1080,'protocol':'socks','settings':{'udp':False}}],
          'outbounds':[{'protocol':'vless','settings':{'vnext':[{'address':'192.0.2.20','port':8443,'users':[{'id':data['client_id'],'encryption':'none','flow':'xtls-rprx-vision'}]}]},
                        'streamSettings':{'network':'tcp','security':'reality','realitySettings':{'fingerprint':'chrome','publicKey':data['reality_public_key'],'shortId':data['reality_short_id'],'serverName':'egress.poc.test'}}}]}
path = private/'reality-client.json'
path.write_text(json.dumps(client))
os.chmod(path, 0o600)
def run(*args):
    return subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
run('/opt/tempest/bin/xray','run','-test','-config',str(path))
for unit in ('tempest-lab-reality-client','tempest-lab-reality-upstream','tempest-lab-reality-decoy'):
    state=subprocess.run(['systemctl','show',unit,'-p','LoadState','--value'],capture_output=True,text=True,check=True).stdout.strip()
    if state!='not-found': raise SystemExit('Reality test unit already exists: '+unit)
active=lambda unit: subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
upstream='tempest-lab-upstream-boot' if active('tempest-lab-upstream-boot') else 'tempest-lab-upstream'
if not active(upstream): raise SystemExit('Expected TLS lab upstream is not active')
run('systemctl','stop',upstream)
try:
    common = ['systemd-run','--property=NoNewPrivileges=yes','--property=PrivateDevices=yes','--property=ProtectSystem=strict','--property=CapabilityBoundingSet=']
    run(*common,'--unit=tempest-lab-reality-decoy','--property=NetworkNamespacePath=/run/netns/tpab-u','/usr/bin/openssl','s_server','-accept','127.0.0.1:8443','-cert',str(private/'tls.crt'),'-key',str(private/'tls.key'),'-alpn','h2','-www')
    run(*common,'--unit=tempest-lab-reality-upstream','--property=NetworkNamespacePath=/run/netns/tpab-u','/opt/tempest/bin/xray','run','-config',str(private/'upstream-reality.json'))
    run(*common,'--unit=tempest-lab-reality-client','--property=NetworkNamespacePath=/run/netns/tpab-g','/opt/tempest/bin/xray','run','-config',str(path))
    # The helper runs inside tpab-g and checks the sink's observed source.
    helper = '''import socket,sys,time
for i in range(30):
 try:
  s=socket.create_connection(("127.0.0.1",1080),timeout=2);break
 except OSError: time.sleep(.2)
else: raise RuntimeError("SOCKS not ready")
s.settimeout(5);s.sendall(b"\\x05\\x01\\x00");assert s.recv(2)==b"\\x05\\x00"
s.sendall(b"\\x05\\x01\\x00\\x01"+socket.inet_aton("198.51.100.20")+(18080).to_bytes(2,"big"))
r=s.recv(10);assert len(r)==10 and r[:2]==b"\\x05\\x00"
s.sendall(b"REALITY-PROBE\\n");f=s.makefile("rb");assert f.readline()==b"REALITY-PROBE\\n" and f.readline().strip()==b"203.0.113.5"
'''
    subprocess.run(['ip','netns','exec','tpab-g','/usr/bin/python3','-c',helper], check=True, timeout=20)
    print('PASS real Xray Reality/Vision TCP handshake and payload; sink observed upstream source', flush=True)
finally:
    for unit in ('tempest-lab-reality-client','tempest-lab-reality-upstream','tempest-lab-reality-decoy'):
        subprocess.run(['systemctl','stop',unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if upstream=='tempest-lab-upstream-boot':
        run('systemctl','start',upstream)
    else:
        run('systemd-run','--unit=tempest-lab-upstream','--property=NetworkNamespacePath=/run/netns/tpab-u','--property=NoNewPrivileges=yes','--property=PrivateDevices=yes','--property=ProtectSystem=strict','--property=CapabilityBoundingSet=','/opt/tempest/bin/xray','run','-config',str(private/'upstream-tls.json'))
