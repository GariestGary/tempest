#!/usr/bin/env python3
"""Test upstream loss with sink captures and positive controls; restore TLS."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

if os.geteuid()!=0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip()!='ce5fdfe6-0c5d-4487-82d8-f158cdb89609': raise SystemExit('Wrong VM')
base=Path('/var/lib/tempest-live-acceptance-20261126')
private=base/'private'
for ns in ('tpab-g','tpab-u','tpab-s','tpab-c','tpab-w'):
    if os.stat('/run/netns/'+ns).st_ino==os.stat('/proc/1/ns/net').st_ino: raise SystemExit('Unsafe namespace')
def cmd(*args): subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
captures=[]; ok=True
capture_id=str(time.time_ns())
active=lambda unit: subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
upstream='tempest-lab-upstream-boot' if active('tempest-lab-upstream-boot') else 'tempest-lab-upstream'
if not active(upstream): raise SystemExit('Expected TLS lab upstream is not active')
cmd('systemctl','stop',upstream)
try:
    filt='(dst host 198.51.100.20 or dst host 2001:db8:2::20 or dst host 198.51.100.53 or dst host 2001:db8:2::53) and (tcp or udp)'
    for iface in ('tpab-sg','tpab-su'):
        path=base/('fault-'+capture_id+'-'+iface+'.pcap')
        if path.exists(): raise RuntimeError('Existing evidence; refusing overwrite')
        p=subprocess.Popen(['ip','netns','exec','tpab-s','tcpdump','-U','-n','-i',iface,'-w',str(path),filt],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        captures.append((p,path,iface))
    try:
        time.sleep(1)
        ok &= subprocess.run(['/usr/bin/python3','/home/gary/working-core-traffic.py','--fault-only'],timeout=60).returncode==0
    finally:
        for p,path,iface in captures:
            p.send_signal(signal.SIGINT); _,err=p.communicate(timeout=10)
            lines=subprocess.run(['tcpdump','-nn','-r',str(path)],capture_output=True,text=True,check=True).stdout.splitlines()
            good=not lines and '0 packets dropped by kernel' in err
            print('PASS' if good else 'FAIL','fault capture',iface,'packets',len(lines),'zero drops','0 packets dropped by kernel' in err,flush=True);ok &= good
    ok &= subprocess.run(['/usr/bin/python3','/home/gary/working-core-traffic.py','--fault-control'],timeout=60).returncode==0
finally:
    if upstream=='tempest-lab-upstream-boot':
        cmd('systemctl','start',upstream)
    else:
        cmd('systemd-run','--unit=tempest-lab-upstream','--property=NetworkNamespacePath=/run/netns/tpab-u','--property=NoNewPrivileges=yes','--property=PrivateDevices=yes','--property=ProtectSystem=strict','--property=CapabilityBoundingSet=','/opt/tempest/bin/xray','run','-config',str(private/'upstream-tls.json'))
if not ok: sys.exit(1)
print('PASS upstream fault fail-closed; TLS upstream restored',flush=True)
