#!/usr/bin/env python3
"""Demonstrate a client-OS kill switch in two approved lab namespaces only."""
import os
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609':
    raise SystemExit('Wrong lab VM or user')
namespaces = [Path('/proc/1/ns/net'), *(Path('/var/run/netns') / n for n in ('tpab-w','tpab-s'))]
if len({p.stat().st_ino for p in namespaces}) != len(namespaces):
    raise SystemExit('Network namespaces are not distinct')
def run(*args,check=True):
    return subprocess.run(args,check=check,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
def ip(ns,*args,check=True): return run('ip','-n',ns,*args,check=check)
for ns,ifname in [('tpab-w','tpab-wn'),('tpab-w','tpab-sn'),('tpab-s','tpab-sn')]:
    if ip(ns,'link','show',ifname,check=False).returncode==0: raise SystemExit('Test interface already exists')
if run('ip','netns','exec','tpab-w','nft','list','table','inet','tempest_test_killswitch',check=False).returncode==0:
    raise SystemExit('Test firewall table already exists')
base=Path('/var/lib/tempest-final-a-20261126'); base.mkdir(mode=0o700,exist_ok=True)
address_info = json.loads(subprocess.check_output(['ip','-n','tpab-w','-j','-6','addr','show','dev','tpab-user'],text=True))
original_v6 = [f"{a['local']}/{a['prefixlen']}" for a in address_info[0]['addr_info'] if a['scope'] == 'global']
cap=base/'client-killswitch.pcap'
if cap.exists(): raise SystemExit('Capture exists')
helper=r'''import socket,sys
allowed=sys.argv[1]=='allow'; tag=sys.argv[2]; ok=True
for addr in ('198.51.100.10','2001:db8:2::10'):
 for kind in (socket.SOCK_STREAM,socket.SOCK_DGRAM):
  s=socket.socket(socket.AF_INET6 if ':' in addr else socket.AF_INET,kind);s.settimeout(1)
  token=(tag+'-'+addr+'-'+str(kind)+'\n').encode()
  try:
   s.connect((addr,18080));s.sendall(token);result=s.recv(4096).startswith(token)
  except OSError: result=False
  finally: s.close()
  good=result==allowed;ok &= good
  print(('PASS' if good else 'FAIL'),tag,addr,'TCP' if kind==socket.SOCK_STREAM else 'UDP',flush=True)
sys.exit(0 if ok else 1)
'''
def probe(allowed,tag):
    subprocess.run(['ip','netns','exec','tpab-w','/usr/bin/python3','-c',helper,'allow' if allowed else 'deny',tag],check=True,timeout=20)
capture=None
created=False
table=False
try:
    ip('tpab-w','link','add','tpab-wn','type','veth','peer','name','tpab-sn');created=True
    ip('tpab-w','link','set','tpab-sn','netns','tpab-s')
    for ns,iface,ipv4,ipv6 in [('tpab-w','tpab-wn','203.0.113.13/30','2001:db8:13::1/64'),('tpab-s','tpab-sn','203.0.113.14/30','2001:db8:13::2/64')]:
        ip(ns,'addr','add',ipv4,'dev',iface);ip(ns,'-6','addr','add',ipv6,'dev',iface,'nodad');ip(ns,'link','set',iface,'up')
    ip('tpab-w','route','add','default','via','203.0.113.14','dev','tpab-wn','metric','100')
    ip('tpab-w','-6','route','add','default','via','2001:db8:13::2','dev','tpab-wn','metric','2048')
    capture=subprocess.Popen(['ip','netns','exec','tpab-s','tcpdump','-U','-n','-i','tpab-sn','-w',str(cap),'port 18080'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    time.sleep(1)
    probe(True,'TUNNEL')
    ip('tpab-w','route','del','default','dev','tpab-user')
    ip('tpab-w','-6','route','del','default','dev','tpab-user')
    probe(True,'NO_SWITCH')
    rules='''add table inet tempest_test_killswitch
add chain inet tempest_test_killswitch output { type filter hook output priority 0; policy drop; }
add rule inet tempest_test_killswitch output oifname "lo" accept
add rule inet tempest_test_killswitch output oifname "tpab-user" accept
add rule inet tempest_test_killswitch output oifname "tpab-wc" ip daddr 192.0.2.1 udp dport 51871 accept
'''
    subprocess.run(['ip','netns','exec','tpab-w','nft','-f','-'],input=rules,text=True,check=True);table=True
    probe(False,'ROUTE_REMOVED')
    ip('tpab-w','route','add','default','dev','tpab-user','metric','10')
    ip('tpab-w','-6','route','add','default','dev','tpab-user','metric','10')
    ip('tpab-w','link','set','tpab-user','down')
    probe(False,'TUNNEL_DOWN')
finally:
    if table: run('ip','netns','exec','tpab-w','nft','delete','table','inet','tempest_test_killswitch',check=False)
    ip('tpab-w','link','set','tpab-user','up',check=False)
    for address in original_v6:
        ip('tpab-w','-6','addr','replace',address,'dev','tpab-user','nodad',check=False)
    ip('tpab-w','route','del','default','dev','tpab-user','metric','10',check=False)
    ip('tpab-w','-6','route','del','default','dev','tpab-user','metric','10',check=False)
    ip('tpab-w','route','replace','default','dev','tpab-user',check=False)
    ip('tpab-w','-6','route','replace','default','dev','tpab-user',check=False)
    if created: ip('tpab-w','link','del','tpab-wn',check=False)
    if capture:
        capture.send_signal(signal.SIGINT);_,err=capture.communicate(timeout=10)
        raw=cap.read_bytes(); good=b'NO_SWITCH-' in raw and b'ROUTE_REMOVED-' not in raw and b'TUNNEL_DOWN-' not in raw and '0 packets dropped by kernel' in err
        print('PASS' if good else 'FAIL','fallback interface capture; baseline, no blocked markers, zero drops:',good,flush=True)
        if not good: sys.exit(1)
print('PASS test-scoped client kill-switch; native fallback and cleanup',flush=True)
