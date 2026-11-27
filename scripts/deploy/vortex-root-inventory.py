#!/usr/bin/env python3
"""Read-only root inventory: report firewall selectors and sing-box ports, never secrets."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import ipaddress

PORTS = (51820, 51789, 9080)


def clean_tag(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value) else '<redacted>'


def references(value, wanted, path='$'):
    result = []
    if isinstance(value, dict):
        for key, child in value.items():
            # Keys are schema names; unknown keys may contain secrets.
            key = key if key in {'inbounds', 'endpoints', 'outbounds', 'route', 'rules', 'inbound', 'outbound', 'tag', 'final', 'default', 'detour'} else '<field>'
            result.extend(references(child, wanted, path + '.' + key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(references(child, wanted, f'{path}[{index}]'))
    elif value == wanted:
        result.append(path)
    return result


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo: this inspection does not write to the server')
    p = subprocess.run(['nft', '-j', '-a', 'list', 'ruleset'], check=True, capture_output=True, text=True, timeout=20)
    nft = json.loads(p.stdout)['nftables']
    chains, candidates = [], []
    for entry in nft:
        if 'chain' in entry and entry['chain'].get('hook'):
            c = entry['chain']; chains.append({k: c.get(k) for k in ('family', 'table', 'name', 'hook', 'policy')})
        if 'rule' in entry:
            r = entry['rule']; serialized = json.dumps(r.get('expr', []), separators=(',', ':'))
            hits = [port for port in PORTS if re.search(r'(?<!\d)' + str(port) + r'(?!\d)', serialized)]
            if hits:
                candidates.append({'family': r.get('family'), 'table': r.get('table'), 'chain': r.get('chain'), 'handle': r.get('handle'), 'matched_ports': hits})
    configs = []
    for path in sorted(Path('/etc/sing-box').glob('*.json')):
        try:
            cfg = json.loads(path.read_text())
            objects = []
            for field in ('inbounds', 'endpoints', 'outbounds'):
                for obj in cfg.get(field, []):
                    if not isinstance(obj, dict):
                        continue
                    port = obj.get('listen_port')
                    tag = obj.get('tag')
                    item = {'section': field, 'type': clean_tag(obj.get('type')), 'tag': clean_tag(tag), 'listen_port': port if isinstance(port, int) else None}
                    if field == 'outbounds':
                        server = obj.get('server')
                        try:
                            ip = ipaddress.ip_address(server)
                            item['server_scope'] = 'loopback' if ip.is_loopback else 'private' if ip.is_private else 'public'
                        except ValueError:
                            item['server_scope'] = 'hostname' if isinstance(server, str) else 'none'
                        item['server_port'] = obj.get('server_port') if isinstance(obj.get('server_port'), int) else None
                        item['route_reference_paths'] = references(cfg.get('route', {}), tag) if isinstance(tag, str) and tag else []
                    objects.append(item)
            configs.append({'filename': path.name, 'objects': objects, 'route_rule_count': len(cfg.get('route', {}).get('rules', [])), 'wg_clients_reference_paths': references(cfg, 'wg-clients')})
        except (OSError, ValueError, AttributeError, TypeError):
            configs.append({'filename': path.name, 'status': 'unreadable_or_invalid'})
    print(json.dumps({'base_chains': chains, 'candidate_port_rules': candidates, 'singbox_active_directory': configs, 'scope': 'Read-only inventory. Port matches are candidates only; generic policy and all dependencies need review.'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print('INCOMPLETE:', type(exc).__name__, file=sys.stderr)
        sys.exit(1)
