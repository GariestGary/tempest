#!/usr/bin/env python3
"""Generate private synthetic credentials/configs for the approved VM fixture."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import uuid

if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609':
    raise SystemExit('Wrong VM or user')
private = Path('/var/lib/tempest-live-acceptance-20261126/private')
private.mkdir(mode=0o700, exist_ok=False)
provision = Path('/var/lib/tempest-core-private')
provision.mkdir(mode=0o750, exist_ok=False)
os.umask(0o077)

def command(*args, data=None):
    return subprocess.run(args, input=data, text=True, capture_output=True, check=True).stdout.strip()

gateway = command('wg', 'genkey')
probe = command('wg', 'genkey')
gateway_pub = command('wg', 'pubkey', data=gateway + '\n')
probe_pub = command('wg', 'pubkey', data=probe + '\n')
(private/'gateway.key').write_text(gateway + '\n')
(private/'gateway.pub').write_text(gateway_pub + '\n')
(private/'probe.key').write_text(probe + '\n')
client_id = str(uuid.uuid4())
short_id = secrets.token_hex(8)
lines = dict(line.split(': ', 1) for line in command('/opt/tempest/bin/xray', 'x25519').splitlines())
reality_private = lines['PrivateKey']
reality_public = lines['Password (PublicKey)']
keyfile, certfile = private/'tls.key', private/'tls.crt'
subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(keyfile), '-out', str(certfile), '-days', '7', '-subj', '/CN=egress.poc.test', '-addext', 'subjectAltName=DNS:egress.poc.test'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
fingerprint = command('openssl', 'x509', '-in', str(certfile), '-noout', '-fingerprint', '-sha256').split('=', 1)[1].replace(':', '').lower()
rules = [{'destination': f'{prefix}{last}/{bits}', 'port': 18080, 'egress': egress}
         for prefix, bits in [('198.51.100.', 32), ('2001:db8:2::', 128)]
         for last, egress in [('10', 'direct'), ('20', 'vpn'), ('30', 'block')]]
deployment = {'target_namespace_id': 'tpab-g', 'listen_address': '192.0.2.1', 'listen_port': 51871,
              'client_endpoint_address': '192.0.2.1', 'client_endpoint_port': 51871,
              'tunnel_addresses': ['10.88.0.1/32', 'fd42:88::1/128'],
              'client_pools': ['10.88.0.0/24', 'fd42:88::/64'], 'private_key': gateway,
              'resolvers': ['198.51.100.53', '2001:db8:2::53'],
              'probe': {'peer': {'public_key': probe_pub, 'addresses': ['10.88.0.2/32', 'fd42:88::2/128']}, 'rules': rules}}
(provision/'deployment.json').write_text(json.dumps(deployment))
(provision/'app-secret').write_text(secrets.token_urlsafe(48))
(provision/'bootstrap-password').write_text(secrets.token_urlsafe(24))
os.chown(provision, 0, int(command('getent', 'group', 'tempest-control').split(':')[2]))
os.chmod(provision, 0o750)
for path in provision.iterdir():
    os.chown(path, 0, int(command('getent', 'group', 'tempest-control').split(':')[2]))
    os.chmod(path, 0o640)

allow = [{'action': 'allow', 'network': 'tcp,udp', 'ip': ['198.51.100.0/24', '2001:db8:2::/64'], 'port': '53,18080'}]
base = {'log': {'loglevel': 'warning'}, 'outbounds': [{'protocol': 'freedom', 'settings': {'finalRules': allow}}]}
tls = {'listen': '192.0.2.20', 'port': 8443, 'protocol': 'vless', 'settings': {'clients': [{'id': client_id}], 'decryption': 'none'},
       'streamSettings': {'network': 'tcp', 'security': 'tls', 'tlsSettings': {'certificates': [{'certificateFile': str(certfile), 'keyFile': str(keyfile)}]}}}
reality = {'listen': '192.0.2.20', 'port': 8443, 'protocol': 'vless', 'settings': {'clients': [{'id': client_id, 'flow': 'xtls-rprx-vision'}], 'decryption': 'none'},
           'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': {'target': '127.0.0.1:8443', 'serverNames': ['egress.poc.test'], 'privateKey': reality_private, 'shortIds': [short_id]}}}
for label, inbound in [('tls', tls), ('reality', reality)]:
    path = private/f'upstream-{label}.json'
    path.write_text(json.dumps({**base, 'inbounds': [inbound]}))
    subprocess.run(['/opt/tempest/bin/xray', 'run', '-test', '-config', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
(private/'synthetic.json').write_text(json.dumps({'client_id': client_id, 'certificate_sha256': fingerprint, 'reality_public_key': reality_public, 'reality_short_id': short_id}))
print('Private synthetic WG, TLS and Reality material created; both Xray upstream configurations validated.')
