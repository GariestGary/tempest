#!/usr/bin/env python3
"""Activate the generated user WG profile only in the approved tpab-w namespace."""
import configparser
import os
from pathlib import Path
import subprocess

if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609':
    raise SystemExit('Wrong VM or user')
if subprocess.run(['ip','-n','tpab-w','link','show','tpab-user'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
    raise SystemExit('User interface already exists')
config = configparser.ConfigParser(interpolation=None)
if not config.read('/var/lib/tempest-live-acceptance-20261126/private/user.conf'):
    raise SystemExit('Missing user profile')
interface, peer = config['Interface'], config['Peer']
assert peer['Endpoint'] == '192.0.2.1:51871'
keyfile = Path('/etc/wireguard/tempest-lab-user.key')
key = interface['PrivateKey'].strip()+'\n'
if keyfile.exists():
    if keyfile.is_symlink() or keyfile.stat().st_uid != 0 or keyfile.stat().st_mode & 0o077 or keyfile.read_text() != key:
        raise SystemExit('Existing user key does not match the protected profile')
else:
    fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write(key)
def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
run('ip','netns','exec','tpab-w','wireguard-go','tpab-user')
run('ip','netns','exec','tpab-w','wg','set','tpab-user','private-key',str(keyfile),'listen-port','51870','peer',peer['PublicKey'],'allowed-ips','0.0.0.0/0,::/0','endpoint',peer['Endpoint'],'persistent-keepalive','10')
for address in interface['Address'].split(','):
    value = address.strip()
    if ':' in value:
        run('ip','-n','tpab-w','-6','addr','add',value,'dev','tpab-user','nodad')
    else:
        run('ip','-n','tpab-w','addr','add',value,'dev','tpab-user')
run('ip','-n','tpab-w','link','set','tpab-user','mtu','1280','up')
run('ip','-n','tpab-w','route','add','default','dev','tpab-user')
run('ip','-n','tpab-w','-6','route','add','default','dev','tpab-user')
print('Generated user profile configured in tpab-w without disclosing keys.')
