#!/usr/bin/env python3
"""Run userspace WG traffic and bounded sink-side capture in the named lab VM."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609':
    raise SystemExit('Wrong VM or user')
base = Path('/var/lib/tempest-live-acceptance-20261126')
helper = r'''import socket,subprocess,sys,time,uuid
def echo(addr,port,wire,expected,timeout=1.5):
 f=socket.AF_INET6 if ':' in addr else socket.AF_INET; t=socket.SOCK_DGRAM if wire=='udp' else socket.SOCK_STREAM
 s=socket.socket(f,t);s.settimeout(timeout);mark=('CHECK-'+uuid.uuid4().hex+'\n').encode()
 try:
  s.connect((addr,port));s.sendall(mark)
  if wire=='tcp':
   h=s.makefile('rb'); result=h.readline()+h.readline().rstrip(b'\n')
  else: result=s.recv(1024)
 except OSError: result=b''
 finally:s.close()
 return result==(mark+expected.encode() if expected else b'')
def dns(addr,transport,typ,expected):
 r=subprocess.run(['dig','-r','@'+addr,uuid.uuid4().hex+'.poc.test',typ,'+short','+norecurse','+timeout=1','+tries=1',transport],capture_output=True,text=True,timeout=4)
 return r.returncode==0 and r.stdout.strip()==expected
mode=sys.argv[1]; ok=True
if mode=='positive':
 for addr,source in [('198.51.100.10','203.0.113.1'),('2001:db8:2::10','2001:db8:10::1'),('198.51.100.20','203.0.113.5'),('2001:db8:2::20','2001:db8:11::1')]:
  for wire in ['tcp','udp']:
   value=echo(addr,18080,wire,source);print('PASS' if value else 'FAIL',addr,wire,flush=True);ok &= value
 for addr in ['198.51.100.53','2001:db8:2::53']:
  for wire in ['+tcp','+notcp']:
   for typ,expected in [('A','198.51.100.20'),('AAAA','2001:db8:2::20')]:
    value=dns(addr,wire,typ,expected);print('PASS' if value else 'FAIL','DNS',addr,wire,typ,flush=True);ok &= value
elif mode=='negative':
 for addr,port in [('198.51.100.30',18080),('2001:db8:2::30',18080),('198.51.100.30',18081),('2001:db8:2::30',18081),('198.51.100.10',53),('2001:db8:2::10',53),('198.51.100.10',853),('2001:db8:2::10',853)]:
  for wire in ['tcp','udp']:
   value=echo(addr,port,wire,'',timeout=.8);print('PASS' if value else 'FAIL','blocked',addr,port,wire,flush=True);ok &= value
elif mode=='control':
 expected=sys.argv[2]
 for wire in ['tcp','udp']:
  value=echo('198.51.100.30',18080,wire,expected);print('PASS' if value else 'FAIL','positive-control',wire,flush=True);ok &= value
elif mode=='changed':
 for addr,source in [('198.51.100.10','203.0.113.5'),('2001:db8:2::10','2001:db8:11::1')]:
  for wire in ['tcp','udp']:
   value=echo(addr,18080,wire,source);print('PASS' if value else 'FAIL','changed-rule',addr,wire,flush=True);ok &= value
elif mode=='fault':
 for addr in ['198.51.100.20','2001:db8:2::20']:
  for wire in ['tcp','udp']:
   value=echo(addr,18080,wire,'',timeout=.8);print('PASS' if value else 'FAIL','upstream-down',addr,wire,flush=True);ok &= value
 for addr in ['198.51.100.53','2001:db8:2::53']:
  for wire in ['+tcp','+notcp']:
   value=not dns(addr,wire,'A','198.51.100.20');print('PASS' if value else 'FAIL','upstream-down DNS',addr,wire,flush=True);ok &= value
 for addr,source in [('198.51.100.10','203.0.113.1'),('2001:db8:2::10','2001:db8:10::1')]:
  value=echo(addr,18080,'tcp',source);print('PASS' if value else 'FAIL','DIRECT still works',addr,flush=True);ok &= value
elif mode=='fault-control':
 for addr,source in [('198.51.100.20','203.0.113.1'),('2001:db8:2::20','2001:db8:10::1')]:
  for wire in ['tcp','udp']:
   value=echo(addr,18080,wire,source);print('PASS' if value else 'FAIL','fault positive control',addr,wire,flush=True);ok &= value
 for addr in ['198.51.100.53','2001:db8:2::53']:
  for wire in ['+tcp','+notcp']:
   value=dns(addr,wire,'A','198.51.100.20');print('PASS' if value else 'FAIL','fault DNS control',addr,wire,flush=True);ok &= value
elif mode=='warmup':
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  if echo('198.51.100.10',18080,'udp','203.0.113.1',timeout=1):
   print('PASS user WG reconnected',flush=True);break
  time.sleep(.5)
 else:
  print('FAIL user WG did not reconnect in 30s',flush=True);ok=False
elif mode=='revoked':
 for addr in ['198.51.100.10','2001:db8:2::10','198.51.100.20','2001:db8:2::20']:
  for wire in ['tcp','udp']:
   value=echo(addr,18080,wire,'',timeout=.8);print('PASS' if value else 'FAIL','revoked client',addr,wire,flush=True);ok &= value
 for addr in ['198.51.100.53','2001:db8:2::53']:
  for wire in ['+tcp','+notcp']:
   value=not dns(addr,wire,'A','198.51.100.20');print('PASS' if value else 'FAIL','revoked DNS',addr,wire,flush=True);ok &= value
sys.exit(0 if ok else 1)
'''
def probe(ns, mode, *args):
    return subprocess.run(['ip','netns','exec',ns,'/usr/bin/python3','-c',helper,mode,*args], check=False, timeout=60).returncode == 0

if sys.argv[1:] in (['--positive-only'], ['--changed-only'], ['--fault-only'], ['--fault-control'], ['--warmup'], ['--revoked-only']):
    modes={'--positive-only':('tpab-w','positive'), '--changed-only':('tpab-w','changed'), '--fault-only':('tpab-w','fault'), '--fault-control':('tpab-g','fault-control'), '--warmup':('tpab-w','warmup'), '--revoked-only':('tpab-w','revoked')}
    sys.exit(0 if probe(*modes[sys.argv[1]]) else 1)

ok = probe('tpab-w','positive')
ok &= probe('tpab-g','control','203.0.113.1')
ok &= probe('tpab-u','control','203.0.113.5')
captures=[]
filt='(dst host 198.51.100.30 or dst host 2001:db8:2::30 or dst host 198.51.100.10 or dst host 2001:db8:2::10) and (tcp or udp)'
for iface in ('tpab-sg','tpab-su'):
    path=base/('negative-'+iface+'.pcap')
    if path.exists(): raise SystemExit(f'Capture exists: {path}')
    p=subprocess.Popen(['ip','netns','exec','tpab-s','tcpdump','-U','-n','-i',iface,'-w',str(path),filt], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    captures.append((p,path,iface))
try:
    time.sleep(1)
    ok &= probe('tpab-w','negative')
finally:
    for p,path,iface in captures:
        p.send_signal(signal.SIGINT)
        _,err=p.communicate(timeout=10)
        lines=subprocess.run(['tcpdump','-nn','-r',str(path)],capture_output=True,text=True,check=True).stdout.splitlines()
        good=not lines and '0 packets dropped by kernel' in err
        print('PASS' if good else 'FAIL','negative capture',iface,'packets',len(lines),'zero drops', '0 packets dropped by kernel' in err,flush=True)
        ok &= good
sys.exit(0 if ok else 1)
