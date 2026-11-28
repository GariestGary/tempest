#!/usr/bin/env python3
"""Conservative, operator-triggered VORTEX control-plane updater.

No network plan, Agent, systemd unit, or host network mutation is performed.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = Path('/etc/tempest/working-core-panel.env')
STATE_DIR = Path('/var/lib/tempest-updater')
BACKUP_DIR = Path('/var/backups/tempest/updater')
PROJECT = 'tempest-working-core'
SHA = re.compile(r'^[0-9a-f]{40}$')
DEPLOY = re.compile(r'^/opt/tempest-deploy-([0-9a-f]{40})$')
SERVICES = ('api', 'panel')
COMPOSE_FILES = ('docker-compose.working-core.yml', 'deploy/working-core/vortex.compose.yml')


class Refusal(RuntimeError):
    pass


def run(*args: str, capture: bool = True, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None, check=False)
    if result.returncode:
        # Subprocess stderr can contain secrets (especially Compose interpolation).
        raise Refusal(f'{args[0]} failed (exit {result.returncode}); inspect its protected local logs')
    return result.stdout.strip() if capture else ''


def git(*args: str) -> str:
    return run('git', '-C', str(ROOT), *args)


def revision(*, online: bool = True) -> str:
    sha = git('rev-parse', 'HEAD')
    if not SHA.fullmatch(sha):
        raise Refusal('invalid checkout revision')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise Refusal('checkout is not clean')
    if git('symbolic-ref', '--short', 'HEAD') != 'master':
        raise Refusal('checkout must be on master')
    if git('remote', 'get-url', 'origin') not in (
        'https://github.com/GariestGary/tempest.git',
        'git@github.com:GariestGary/tempest.git',
    ):
        raise Refusal('unexpected origin')
    remote = (git('ls-remote', 'origin', 'refs/heads/master').split()[0] if online
              else git('rev-parse', 'refs/remotes/origin/master'))
    if remote != sha:
        raise Refusal('HEAD is not the current origin/master; run git pull --ff-only')
    return sha


def workflow_success(sha: str) -> None:
    """Require successful push runs for this exact commit; public API needs no token."""
    for workflow in ('ci.yml', 'codeql.yml'):
        url = ('https://api.github.com/repos/GariestGary/tempest/actions/workflows/'
               f'{workflow}/runs?head_sha={sha}&event=push&per_page=30')
        request = urllib.request.Request(url, headers={
            'Accept': 'application/vnd.github+json', 'User-Agent': 'TEMPEST-vortex-updater',
        })
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.load(response)
        except (OSError, ValueError) as exc:
            raise Refusal(f'cannot verify Hosted CI/CodeQL ({workflow}): {type(exc).__name__}') from None
        runs = [r for r in data.get('workflow_runs', ()) if r.get('head_sha') == sha
                and r.get('head_branch') == 'master' and r.get('event') == 'push']
        if not runs or max(runs, key=lambda r: r['id']).get('conclusion') != 'success':
            raise Refusal(f'{workflow} has no successful latest push run for {sha}')


def installed() -> tuple[Path, str]:
    try:
        if ENV_FILE.is_symlink() or ENV_FILE.stat().st_uid != 0:
            raise Refusal('panel deployment environment is unsafe')
        contents = ENV_FILE.read_text()
    except OSError:
        raise Refusal('panel deployment path is missing') from None
    matches = re.findall(r'^TEMPEST_DEPLOY_DIR=(/opt/tempest-deploy-[0-9a-f]{40})$', contents, re.M)
    if len(matches) != 1 or contents.strip() != f'TEMPEST_DEPLOY_DIR={matches[0]}':
        raise Refusal('panel deployment path is ambiguous')
    path = Path(matches[0])
    if not path.is_dir() or path.is_symlink() or path.resolve() != path:
        raise Refusal('installed bundle is missing or unsafe')
    if path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
        raise Refusal('installed bundle has unsafe ownership or permissions')
    for name in (*COMPOSE_FILES, '.env'):
        file = path / name
        if not file.is_file() or file.is_symlink():
            raise Refusal(f'installed bundle lacks {name}')
        if file.stat().st_uid != 0 or file.stat().st_mode & 0o022:
            raise Refusal(f'installed bundle has unsafe {name} permissions')
    return path, DEPLOY.fullmatch(str(path)).group(1)


def containers(path: Path) -> dict[str, dict]:
    found = {}
    for service in SERVICES:
        ids = run('docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={PROJECT}',
                  '--filter', f'label=com.docker.compose.service={service}').splitlines()
        if len(ids) != 1:
            raise Refusal(f'expected one {PROJECT}/{service} container')
        info = json.loads(run('docker', 'inspect', ids[0]))[0]
        labels = info.get('Config', {}).get('Labels') or {}
        paths = labels.get('com.docker.compose.project.config_files', '').split(',')
        bundles = {str(Path(p).parents[0]) for p in paths if p.endswith(COMPOSE_FILES[0])}
        if len(bundles) != 1 or not DEPLOY.fullmatch(next(iter(bundles))):
            raise Refusal(f'{service} container has an unexpected Compose bundle')
        source = Path(next(iter(bundles)))
        if str(source / COMPOSE_FILES[1]) not in paths or not source.is_dir():
            raise Refusal(f'{service} container has incomplete Compose labels')
        if not info.get('State', {}).get('Running'):
            raise Refusal(f'{service} is not running')
        found[service] = {'id': ids[0], 'image': info['Image'], 'bundle': str(source),
                          'ports': info.get('NetworkSettings', {}).get('Ports', {}),
                          'health': info['State'].get('Health', {}).get('Status', 'running')}
    return found


def classify(paths: list[str]) -> str:
    api = panel = False
    for name in paths:
        if name.startswith(('docs/', 'backend/tests/', 'frontend/src/pages/__tests__/',
                            'frontend/src/__tests__/', 'tests/')) or name in ('AGENTS.md',):
            continue
        if name.startswith('frontend/'):
            if name.endswith(('.test.ts', '.test.tsx', '.spec.ts', '.spec.tsx')):
                continue
            panel = True
        elif name.startswith('backend/'):
            if name.startswith(('backend/app/network_agent/', 'backend/alembic/')) or name in (
                'backend/Dockerfile', 'backend/requirements-agent-linux.lock',
                'backend/app/models.py', 'backend/app/database.py', 'backend/entrypoint.sh',
            ):
                return 'runtime'
            api = True
        elif name.startswith('.github/') or name.startswith('scripts/deploy/update_vortex.py') or name == 'scripts/deploy/update-vortex.sh':
            # CI/updater-only changes do not alter deployed images.
            continue
        else:
            return 'runtime'
    return 'both' if api and panel else 'api' if api else 'panel' if panel else 'none'


def changed(base: str, target: str) -> list[str]:
    if not SHA.fullmatch(base) or not SHA.fullmatch(target):
        raise Refusal('invalid revision')
    run('git', '-C', str(ROOT), 'merge-base', '--is-ancestor', base, target)
    return git('diff', '--name-only', f'{base}..{target}').splitlines()


def validate_lineage(path: Path, images: dict[str, dict]) -> None:
    """Reject manual drift; allow unchanged services in an updater-recorded state."""
    manifest = STATE_DIR / 'installed.json'
    expected = json.loads(manifest.read_text()) if manifest.is_file() else None
    if expected and expected.get('path') != str(path):
        raise Refusal('installed manifest disagrees with panel deployment path')
    for service, info in images.items():
        if expected and expected.get('components', {}).get(service) == {
            'bundle': info['bundle'], 'image': info['image'],
        }:
            continue
        if not expected and info['bundle'] == str(path):
            continue
        raise Refusal(f'{service} container version disagrees with panel deployment path')


def write_installed(path: Path) -> None:
    live = containers(path)
    record(STATE_DIR / 'installed.json', {'path': str(path), 'components': {
        service: {'bundle': info['bundle'], 'image': info['image']}
        for service, info in live.items()
    }})


def compose(path: Path, *args: str, capture: bool = True) -> str:
    return run('docker', 'compose', '--project-directory', str(path), '--env-file', str(path / '.env'),
               '-f', str(path / COMPOSE_FILES[0]), '-f', str(path / COMPOSE_FILES[1]),
               *args, capture=capture)


def preflight(path: Path, kind: str, images: dict[str, dict], *, skip_space: bool = False) -> None:
    for executable in ('docker', 'git', 'python3', 'systemctl', 'flock'):
        if shutil.which(executable) is None:
            raise Refusal(f'missing dependency: {executable}')
    for service in ('docker.service', 'tempest-wc-network.service', 'tempest-xray-agent.service',
                    'tempest-wc-panel.service'):
        if run('systemctl', 'is-active', service) != 'active':
            raise Refusal(f'{service} is not active')
    if any(info['health'] not in ('healthy', 'running') for info in images.values()):
        raise Refusal('current containers are unhealthy')
    if kind == 'runtime':
        raise Refusal('runtime, Compose, Agent, systemd or network change requires a separately approved procedure')
    if not skip_space:
        free = shutil.disk_usage('/opt').free
        reserve = int(os.getenv('TEMPEST_UPDATE_RESERVE_GIB', '3')) * 1024**3
        build = {'none': 0, 'panel': 2, 'api': 2, 'both': 4}[kind] * 1024**3
        if free < reserve + build:
            raise Refusal(f'insufficient disk space: need {reserve + build >> 30} GiB free before build')


def agent_status(container_id: str) -> dict:
    data = json.loads(run('docker', 'exec', container_id, 'python', '-m',
                          'app.network_agent.xray_client', 'read_status'))
    # IPC result contains status directly on supported production profile.
    return {key: data.get(key) for key in ('active_generation', 'active_generation_id',
                                          'last_known_good_generation', 'pending_generation_id')}


def verify(path: Path, before: dict | None = None) -> None:
    current = containers(path)
    if current['api']['health'] != 'healthy':
        raise Refusal('API healthcheck failed')
    published = current['panel']['ports'].get('8080/tcp') or []
    if len(published) != 1 or published[0].get('HostIp') != '127.0.0.1':
        raise Refusal('panel loopback port publication differs from expected deployment')
    port = int(published[0]['HostPort'])
    for endpoint in ('/', '/health'):
        request = urllib.request.Request(f'http://127.0.0.1:{port}{endpoint}')
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status != 200:
                    raise Refusal(f'panel {endpoint} returned HTTP {response.status}')
        except urllib.error.URLError:
            raise Refusal(f'panel {endpoint} is unavailable') from None
    after = agent_status(current['api']['id'])
    if not after['active_generation'] or after['active_generation'] != after['last_known_good_generation']:
        raise Refusal('Agent active generation/LKG mismatch')
    if after['pending_generation_id']:
        raise Refusal('Agent has an unconfirmed generation')
    if before is not None and after != before:
        raise Refusal('Agent generation changed during control-plane update')


def atomic(path: Path, data: str, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.tempest-', delete=False) as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        name = stream.name
    os.replace(name, path)


def record(path: Path, data: dict) -> None:
    atomic(path, json.dumps(data, sort_keys=True))


def private_root_directory(path: Path) -> bool:
    return not path.is_symlink() and path.stat().st_uid == 0 and not path.stat().st_mode & 0o077


def backup(path: Path, images: dict[str, dict], base: str) -> Path:
    if BACKUP_DIR.is_symlink():
        raise Refusal('backup directory is unsafe')
    if any(item.is_symlink() for item in path.rglob('*')):
        raise Refusal('installed bundle contains a symlink')
    dest = BACKUP_DIR / f'{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}-{base[:12]}'
    dest.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        populate_backup(dest, path, images)
    except BaseException:
        shutil.rmtree(dest)
        raise
    return dest


def populate_backup(dest: Path, path: Path, images: dict[str, dict]) -> None:
    shutil.copytree(path, dest / 'bundle', symlinks=False)
    shutil.copy2(ENV_FILE, dest / 'working-core-panel.env')
    volume = json.loads(run('docker', 'volume', 'inspect', f'{PROJECT}_core_db'))[0]
    if volume.get('Name') != f'{PROJECT}_core_db' or volume.get('Driver') != 'local':
        raise Refusal('unexpected SQLite volume')
    db = Path(volume['Mountpoint']) / 'working-core.db'
    if not db.is_file() or db.is_symlink():
        raise Refusal('SQLite volume is missing or unsafe')
    source = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    target = sqlite3.connect(dest / 'working-core.db')
    try:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise Refusal('SQLite backup failed integrity check')
    finally:
        source.close()
        target.close()
    os.chmod(dest / 'working-core.db', 0o600)
    private_match = re.search(r'^TEMPEST_CORE_PRIVATE_DIR=(/[^\n]+)$', (path / '.env').read_text(), re.M)
    if not private_match:
        raise Refusal('private provisioning directory is missing from Compose environment')
    private = Path(private_match.group(1))
    if not private.is_dir() or private.is_symlink():
        raise Refusal('private provisioning directory is missing or unsafe')
    if any(item.is_symlink() for item in private.rglob('*')):
        raise Refusal('private provisioning directory contains a symlink')
    shutil.copytree(private, dest / 'private', symlinks=False)
    record(dest / 'images.json', {service: data['image'] for service, data in images.items()})


def stage(sha: str, old: Path) -> Path:
    target = Path(f'/opt/tempest-deploy-{sha}')
    if target.exists() or target.is_symlink():
        marker = target / '.tempest-revision'
        if marker.is_file() and marker.read_text().strip() == sha:
            return target  # interrupted pre-build stage; safe to rebuild
        raise Refusal('target bundle exists without matching revision marker')
    run(str(ROOT / 'scripts/deploy/prepare-build-context.sh'), sha, str(target), capture=False)
    try:
        for name in COMPOSE_FILES:
            output = target / name
            output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            output.write_bytes(subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{sha}:{name}']))
            os.chmod(output, 0o644)
        shutil.copy2(old / '.env', target / '.env')
        os.chmod(target / '.env', 0o600)
        (target / '.tempest-revision').write_text(sha + '\n')
        os.chmod(target / '.tempest-revision', 0o600)
        compose(target, 'config', '--quiet')
    except BaseException:
        shutil.rmtree(target)
        raise
    return target


def restore(tx: dict) -> None:
    old = Path(tx['old'])
    target = Path(tx['target'])
    images = tx['images']
    for service in tx['services']:
        run('docker', 'tag', images[service], f'{PROJECT}-{service}:latest')
    if tx['services']:
        compose(old, 'up', '-d', '--no-build', '--no-deps', '--force-recreate', *tx['services'], capture=False)
    verify(old, tx['agent'])
    atomic(ENV_FILE, f'TEMPEST_DEPLOY_DIR={old}\n', 0o644)
    write_installed(old)
    (STATE_DIR / 'pending.json').unlink(missing_ok=True)
    print(f'Rollback verified: {tx["base"]}')


def prune(current: Path, previous: Path) -> None:
    # Only updater-created bundles/backups. Never remove arbitrary Docker objects.
    keep = max(2, min(5, int(os.getenv('TEMPEST_UPDATE_KEEP', '2'))))
    bundles = sorted(Path('/opt').glob('tempest-deploy-*'), key=lambda p: p.stat().st_mtime, reverse=True)
    protected = {current, previous}
    for info in containers(current).values():
        protected.add(Path(info['bundle']))
    for bundle in bundles:
        if len(protected) >= keep:
            break
        if (bundle / '.tempest-revision').is_file():
            protected.add(bundle)
    for bundle in bundles:
        if bundle not in protected and (bundle / '.tempest-revision').is_file() and not bundle.is_symlink():
            shutil.rmtree(bundle)
    backups = sorted(BACKUP_DIR.glob('*'), key=lambda p: p.stat().st_mtime, reverse=True)
    for item in backups[keep:]:
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
    allowed = {f'{PROJECT}-{service}:tempest-rollback-{DEPLOY.fullmatch(str(previous)).group(1)[:12]}'
               for service in SERVICES}
    for tag in run('docker', 'image', 'ls', '--format', '{{.Repository}}:{{.Tag}}').splitlines():
        if re.fullmatch(r'tempest-working-core-(api|panel):tempest-rollback-[0-9a-f]{12}', tag) and tag not in allowed:
            run('docker', 'image', 'rm', tag)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    for mode in ('check', 'status', 'rollback'):
        group.add_argument('--' + mode, action='store_true')
    group.add_argument('--internal-root', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.umask(0o077)
    sha = revision(online=not (args.status or args.rollback))
    if not args.status and not args.rollback:
        workflow_success(sha)
    if os.geteuid() != 0:
        mode = '--status' if args.status else '--check' if args.check else '--rollback' if args.rollback else '--internal-root'
        os.execvp('sudo', ['sudo', sys.executable, str(Path(__file__).resolve()), mode])
    if not args.status and not args.check:
        if STATE_DIR.is_symlink():
            raise Refusal('updater state directory is unsafe')
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not private_root_directory(STATE_DIR):
            raise Refusal('updater state directory has unsafe permissions')
        lock = os.open(STATE_DIR / 'lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refusal('another TEMPEST update is running') from None
    pending = STATE_DIR / 'pending.json'
    if pending.exists():
        if args.status or args.check:
            raise Refusal('an interrupted update needs recovery; run updater normally')
        tx = json.loads(pending.read_text())
        print('Recovering interrupted update')
        restore(tx)
    old, base = installed()
    current = containers(old)
    validate_lineage(old, current)
    if args.rollback:
        previous = STATE_DIR / 'previous.json'
        if not previous.is_file():
            raise Refusal('no recorded previous compatible version')
        tx = json.loads(previous.read_text())
        if tx['target'] != str(old):
            raise Refusal('rollback record does not match installed bundle')
        preflight(old, 'none', current)
        verify(old, tx['agent'])
        backup(old, current, base)
        record(pending, tx)
        restore(tx)
        previous.unlink()
        return
    paths = changed(base, sha)
    kind = classify(paths)
    print(f'Installed: {base}\nTarget:    {sha}\nComponents: {kind}\nAPI: {current["api"]["health"]}; panel: {current["panel"]["health"]}')
    if args.status:
        try:
            status = agent_status(current['api']['id'])
            print(f'Agent active/LKG: {status["active_generation"]}/{status["last_known_good_generation"]}')
        except (Refusal, ValueError, KeyError):
            print('Agent IPC: unavailable')
        return
    preflight(old, kind, current, skip_space=(base == sha and kind == 'none'))
    if kind == 'none' and base == sha:
        print('Already current; no rebuild')
        return
    if args.check:
        verify(old)
        print('Preflight passed; no production changes made')
        return
    agent = agent_status(current['api']['id'])
    if not agent['active_generation'] or agent['active_generation'] != agent['last_known_good_generation'] or agent['pending_generation_id']:
        raise Refusal('Agent generation is not confirmed LKG')
    recovery = backup(old, current, base)
    try:
        target = stage(sha, old)
    except BaseException:
        shutil.rmtree(recovery)
        raise
    selected = {'none': (), 'api': ('api',), 'panel': ('panel',), 'both': SERVICES}[kind]
    tx = {'old': str(old), 'target': str(target), 'base': base, 'sha': sha,
          'images': {s: d['image'] for s, d in current.items()}, 'services': selected,
          'agent': agent, 'backup': str(recovery)}
    if selected:
        try:
            for service in selected:
                run('docker', 'tag', current[service]['image'], f'{PROJECT}-{service}:tempest-rollback-{base[:12]}')
            compose(target, 'build', *selected, capture=False)  # before touching running containers
        except BaseException:
            # Existing containers still use their pinned image IDs.
            shutil.rmtree(recovery)
            if target != old and (target / '.tempest-revision').is_file():
                shutil.rmtree(target)
            raise
        record(pending, tx)
        try:
            compose(target, 'up', '-d', '--no-build', '--no-deps', '--force-recreate', *selected, capture=False)
            verify(target, agent)
        except BaseException:
            restore(tx)
            raise
    else:
        verify(old, agent)
    atomic(ENV_FILE, f'TEMPEST_DEPLOY_DIR={target}\n', 0o644)
    write_installed(target)
    record(STATE_DIR / 'previous.json', tx)
    pending.unlink(missing_ok=True)
    prune(target, old)
    print(f'Updated to {sha}; backup: {recovery}')


if __name__ == '__main__':
    try:
        main()
    except (Refusal, OSError, ValueError, KeyError) as error:
        print(f'update-vortex: {error}', file=sys.stderr)
        sys.exit(1)
