"""Offline regression tests for the operator-triggered updater."""
import importlib.util
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from uuid import UUID, uuid4

import pytest


MODULE = Path(__file__).resolve().parents[2] / 'scripts/deploy/update_vortex.py'
spec = importlib.util.spec_from_file_location('update_vortex', MODULE)
up = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = up
spec.loader.exec_module(up)


@pytest.mark.parametrize(('names', 'expected'), [
    (['docs/STATUS.md', 'backend/tests/test_core.py'], 'none'),
    (['frontend/src/pages/WorkingCore.tsx', 'frontend/package-lock.json'], 'panel'),
    (['backend/app/working_core.py'], 'api'),
    (['backend/app/working_core.py', 'frontend/src/pages/WorkingCore.tsx'], 'both'),
    (['backend/app/network_agent/xray_agent.py'], 'runtime'),
    (['backend/Dockerfile'], 'runtime'),
    (['backend/alembic/versions/009.py'], 'runtime'),
    (['backend/app/models.py'], 'runtime'),
    (['docker-compose.working-core.yml'], 'runtime'),
    (['deploy/working-core/vortex.compose.yml'], 'runtime'),
    (['scripts/deploy/install-runtime.sh'], 'runtime'),
])
def test_classification(names, expected):
    assert up.classify(names) == expected


def test_insufficient_space(monkeypatch):
    monkeypatch.setattr(up, 'run', lambda *args: 'active' if args[0] == 'systemctl' else '')
    monkeypatch.setattr(up.shutil, 'which', lambda name: '/bin/' + name)
    monkeypatch.setattr(up.shutil, 'disk_usage', lambda path: type('Usage', (), {'free': 5 * 1024**3})())
    images = {service: {'health': 'healthy'} for service in up.SERVICES}
    with pytest.raises(up.Refusal, match='insufficient disk space'):
        up.preflight(Path('/opt/tempest-deploy-' + 'a' * 40), 'both', images)


def test_runtime_refused_before_disk_and_mutation(monkeypatch):
    monkeypatch.setattr(up, 'run', lambda *args: 'active')
    monkeypatch.setattr(up.shutil, 'which', lambda name: '/bin/' + name)
    monkeypatch.setattr(up.shutil, 'disk_usage', lambda path: pytest.fail('disk check reached'))
    with pytest.raises(up.Refusal, match='separately approved'):
        up.preflight(Path('/opt'), 'runtime', {s: {'health': 'running'} for s in up.SERVICES})


def test_build_failure_never_switches_containers(monkeypatch, tmp_path):
    calls = []
    old = tmp_path / 'old'
    target = tmp_path / 'target'
    recovery = tmp_path / 'recovery'
    old.mkdir()
    target.mkdir()
    recovery.mkdir()
    (target / '.tempest-revision').write_text('b' * 40)
    monkeypatch.setattr(up, 'STATE_DIR', tmp_path / 'state')
    monkeypatch.setattr(up, 'private_root_directory', lambda path: True)
    monkeypatch.setattr(up, 'revision', lambda **kwargs: 'b' * 40)
    monkeypatch.setattr(up, 'workflow_success', lambda sha: None)
    monkeypatch.setattr(up.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(up, 'installed', lambda: (old, 'a' * 40))
    monkeypatch.setattr(up, 'containers', lambda path: {
        s: {'health': 'healthy', 'bundle': str(old), 'id': s, 'image': 'sha256:old'}
        for s in up.SERVICES
    })
    monkeypatch.setattr(up, 'changed', lambda a, b: ['backend/app/working_core.py'])
    monkeypatch.setattr(up, 'preflight', lambda *args, **kwargs: None)
    monkeypatch.setattr(up, 'agent_status', lambda container: {
        'active_generation': 6, 'last_known_good_generation': 6,
        'pending_generation_id': None,
    })
    monkeypatch.setattr(up, 'backup', lambda *args: recovery)
    monkeypatch.setattr(up, 'stage', lambda *args: target)
    monkeypatch.setattr(up, 'run', lambda *args: calls.append(args))
    def fake_compose(path, *args, **kwargs):
        calls.append(args)
        if args[0] == 'build':
            raise up.Refusal('build failed')
    monkeypatch.setattr(up, 'compose', fake_compose)
    monkeypatch.setattr(sys, 'argv', ['update_vortex.py', '--internal-root'])
    with pytest.raises(up.Refusal):
        up.main()
    assert ('build', 'api') in calls
    assert not any(args[0] == 'up' for args in calls)
    assert not recovery.exists() and not target.exists()


def test_rollback_restores_tags_and_path(monkeypatch, tmp_path):
    old = tmp_path / ('tempest-deploy-' + 'a' * 40)
    target = tmp_path / ('tempest-deploy-' + 'b' * 40)
    old.mkdir()
    events = []
    monkeypatch.setattr(up, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(up, 'private_root_directory', lambda path: True)
    (tmp_path / 'pending.json').write_text('{}')
    monkeypatch.setattr(up, 'ENV_FILE', tmp_path / 'panel.env')
    monkeypatch.setattr(up, 'run', lambda *args: events.append(args))
    monkeypatch.setattr(up, 'compose', lambda *args, **kwargs: events.append(args))
    monkeypatch.setattr(up, 'verify', lambda *args: events.append(('verify',)))
    monkeypatch.setattr(up, 'write_installed', lambda *args: events.append(('manifest',)))
    tx = {'old': str(old), 'target': str(target), 'base': 'a' * 40,
          'services': ['api'], 'images': {'api': 'sha256:old'}, 'agent': {}}
    up.restore(tx)
    assert events[0] == ('docker', 'tag', 'sha256:old', 'tempest-working-core-api:latest')
    assert events[1][1:4] == ('up', '-d', '--no-build')
    assert events[1][6:9] == ('--wait', '--wait-timeout', '120')
    assert events[2] == ('verify',)
    assert events[3] == ('manifest',)
    assert up.ENV_FILE.read_text() == f'TEMPEST_DEPLOY_DIR={old}\n'
    assert not (tmp_path / 'pending.json').exists()


def test_health_failure_prevents_commit(monkeypatch):
    monkeypatch.setattr(up, 'containers', lambda path: {
        'api': {'health': 'unhealthy', 'id': 'api'}, 'panel': {'health': 'running', 'id': 'panel'},
    })
    with pytest.raises(up.Refusal, match='API healthcheck'):
        up.verify(Path('/opt/stage'))


def test_panel_must_stay_on_loopback(monkeypatch):
    monkeypatch.setattr(up, 'containers', lambda path: {
        'api': {'health': 'healthy', 'id': 'api'},
        'panel': {'health': 'running', 'id': 'panel',
                  'ports': {'8080/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '9080'}]}},
    })
    with pytest.raises(up.Refusal, match='loopback'):
        up.verify(Path('/opt/stage'))


def test_mismatched_live_container_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(up, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(up, 'private_root_directory', lambda path: True)
    old = Path('/opt/tempest-deploy-' + 'a' * 40)
    live = Path('/opt/tempest-deploy-' + 'b' * 40)
    with pytest.raises(up.Refusal, match='disagrees'):
        up.validate_lineage(old, {'api': {'bundle': str(live), 'image': 'sha256:new'}})


def test_interrupted_update_recovers_before_new_work(monkeypatch, tmp_path):
    """A pending transaction must be restored before installed() is consulted."""
    events = []
    monkeypatch.setattr(up, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(up, 'private_root_directory', lambda path: True)
    monkeypatch.setattr(up, 'revision', lambda **kwargs: 'b' * 40)
    monkeypatch.setattr(up, 'workflow_success', lambda sha: None)
    monkeypatch.setattr(up.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(up, 'restore', lambda tx: events.append('restore'))
    monkeypatch.setattr(up, 'installed', lambda: (events.append('installed'), (Path('/opt/a'), 'b' * 40))[1])
    monkeypatch.setattr(up, 'containers', lambda path: {s: {'health': 'healthy', 'bundle': str(path)} for s in up.SERVICES})
    monkeypatch.setattr(up, 'changed', lambda a, b: [])
    monkeypatch.setattr(up, 'preflight', lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, 'argv', ['update_vortex.py', '--internal-root'])
    (tmp_path / 'pending.json').write_text(json.dumps({'base': 'a' * 40}))
    up.main()
    assert events[:2] == ['restore', 'installed']


def test_repeat_same_revision_skips_backup_and_build(monkeypatch, tmp_path):
    sha = 'a' * 40
    monkeypatch.setattr(up, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(up, 'private_root_directory', lambda path: True)
    monkeypatch.setattr(up, 'revision', lambda **kwargs: sha)
    monkeypatch.setattr(up, 'workflow_success', lambda sha: None)
    monkeypatch.setattr(up.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(up, 'installed', lambda: (Path('/opt/a'), sha))
    monkeypatch.setattr(up, 'containers', lambda path: {s: {'health': 'healthy', 'bundle': str(path)} for s in up.SERVICES})
    monkeypatch.setattr(up, 'changed', lambda a, b: [])
    monkeypatch.setattr(up, 'preflight', lambda *args, **kwargs: None)
    monkeypatch.setattr(up, 'backup', lambda *args: pytest.fail('backup reached'))
    monkeypatch.setattr(up, 'stage', lambda *args: pytest.fail('stage reached'))
    monkeypatch.setattr(sys, 'argv', ['update_vortex.py', '--internal-root'])
    up.main()


def ipc_response(**changes):
    """Serialize the actual typed IPCResponse returned by xray_client.py."""
    sys.path.insert(0, str(MODULE.parents[2] / 'backend'))
    try:
        from app.network_agent.models import AgentStatus, IPCResponse
    finally:
        sys.path.pop(0)
    status = AgentStatus(state='idle', active_generation=6,
                         active_generation_id=UUID('ff523ea8-e2b0-441d-8e56-7686bbd7fe8c'),
                         last_known_good_generation=6)
    response = json.loads(IPCResponse(request_id=uuid4(), ok=True, result=status).model_dump_json(exclude_none=True))
    response['result'].update(changes)
    return response


def test_agent_status_reads_typed_ipc_response(monkeypatch):
    response = ipc_response()
    monkeypatch.setattr(up, 'run', lambda *args: json.dumps(response))
    status = up.agent_status('api-container')
    assert status == {'state': 'idle', 'active_generation': 6,
                      'active_generation_id': 'ff523ea8-e2b0-441d-8e56-7686bbd7fe8c',
                      'last_known_good_generation': 6, 'pending_generation_id': None}


@pytest.mark.parametrize('mutate', [
    lambda data: data.update(ok=False),
    lambda data: data.pop('result'),
    lambda data: data['result'].pop('state'),
    lambda data: data['result'].update(state='degraded'),
    lambda data: data['result'].update(active_generation=7),
    lambda data: data['result'].update(pending_generation_id=str(uuid4())),
])
def test_agent_status_rejects_invalid_ipc(monkeypatch, mutate):
    response = ipc_response()
    mutate(response)
    monkeypatch.setattr(up, 'run', lambda *args: json.dumps(response))
    with pytest.raises(up.Refusal):
        up.agent_status('api-container')


def test_backend_update_verifies_nginx_after_stale_ip(monkeypatch):
    """A 502 from the old cached API IP must not pass verification."""
    urls = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def urlopen(request, timeout):
        url = request.full_url if hasattr(request, 'full_url') else request
        urls.append(url)
        if url.endswith('/api/core'):
            code = 502 if urls.count(url) == 1 else 401
            raise HTTPError(url, code, 'proxy', {}, None)
        return Response()
    monkeypatch.setattr(up, 'containers', lambda path: {
        'api': {'health': 'healthy', 'id': 'new-api'},
        'panel': {'ports': {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '9080'}]}},
    })
    before = {'state': 'idle', 'active_generation': 6, 'last_known_good_generation': 6,
              'active_generation_id': 'ff523ea8-e2b0-441d-8e56-7686bbd7fe8c',
              'pending_generation_id': None}
    monkeypatch.setattr(up, 'agent_status', lambda container: before if container == 'new-api' else pytest.fail('old API used'))
    monkeypatch.setattr(up.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(up.time, 'sleep', lambda seconds: None)
    assert up.verify(Path('/opt/new'), before) == before
    assert urls.count('http://127.0.0.1:9080/api/core') == 2
    config = (MODULE.parents[2] / 'frontend/nginx-working-core.conf').read_text()
    assert 'resolver 127.0.0.11 valid=10s' in config
    assert 'set $backend http://api:8000;' in config
    assert 'proxy_pass $backend;' in config


def test_backend_update_rejects_persistent_nginx_502(monkeypatch):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def urlopen(request, timeout):
        url = request.full_url if hasattr(request, 'full_url') else request
        if url.endswith('/api/core'):
            raise HTTPError(url, 502, 'stale upstream', {}, None)
        return Response()
    monkeypatch.setattr(up, 'containers', lambda path: {
        'api': {'health': 'healthy', 'id': 'new-api'},
        'panel': {'ports': {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '9080'}]}},
    })
    monkeypatch.setattr(up.urllib.request, 'urlopen', urlopen)
    ticks = iter((0, 31))
    monkeypatch.setattr(up.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(up.Refusal, match='through panel Nginx'):
        up.verify(Path('/opt/new'))


def test_api_update_waits_for_health_before_verify(monkeypatch, tmp_path):
    state, old, target = (tmp_path / name for name in ('state', 'old', 'target'))
    for path in (state, old, target): path.mkdir()
    monkeypatch.setattr(up, 'STATE_DIR', state)
    monkeypatch.setattr(up, 'ENV_FILE', tmp_path / 'panel.env')
    monkeypatch.setattr(up, 'private_root_directory', lambda path: True)
    monkeypatch.setattr(up, 'revision', lambda **kwargs: 'b' * 40)
    monkeypatch.setattr(up, 'workflow_success', lambda sha: None)
    monkeypatch.setattr(up.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(up, 'installed', lambda: (old, 'a' * 40))
    monkeypatch.setattr(up, 'containers', lambda path: {
        s: {'health': 'healthy', 'bundle': str(path), 'id': s, 'image': 'sha256:old'}
        for s in up.SERVICES})
    monkeypatch.setattr(up, 'changed', lambda *args: ['backend/app/working_core.py'])
    monkeypatch.setattr(up, 'preflight', lambda *args, **kwargs: None)
    agent = {'state': 'idle', 'active_generation': 6, 'last_known_good_generation': 6,
             'active_generation_id': str(uuid4()), 'pending_generation_id': None}
    monkeypatch.setattr(up, 'agent_status', lambda *args: agent)
    monkeypatch.setattr(up, 'backup', lambda *args: tmp_path / 'backup')
    monkeypatch.setattr(up, 'stage', lambda *args: target)
    monkeypatch.setattr(up, 'run', lambda *args: '')
    events = []
    monkeypatch.setattr(up, 'compose', lambda path, *args, **kwargs: events.append(args))
    monkeypatch.setattr(up, 'verify', lambda *args: events.append(('verify',)))
    monkeypatch.setattr(up, 'write_installed', lambda *args: None)
    monkeypatch.setattr(up, 'prune', lambda *args: None)
    monkeypatch.setattr(sys, 'argv', ['update_vortex.py', '--internal-root'])
    up.main()
    up_call = next(args for args in events if args[0] == 'up')
    assert up_call[-4:] == ('--wait', '--wait-timeout', '120', 'api')
    assert events.index(up_call) < events.index(('verify',))


def test_manual_rollback_uses_current_agent_generation(monkeypatch, tmp_path):
    current = tmp_path / 'current'
    current.mkdir()
    state = tmp_path / 'state'
    state.mkdir()
    previous = {'target': str(current), 'old': str(tmp_path / 'old'), 'base': 'a' * 40,
                'services': ['api'], 'images': {'api': 'sha256:old'},
                'agent': {'active_generation': 6}}
    (state / 'previous.json').write_text(json.dumps(previous))
    monkeypatch.setattr(up, 'STATE_DIR', state)
    monkeypatch.setattr(up, 'private_root_directory', lambda path: True)
    monkeypatch.setattr(up, 'revision', lambda **kwargs: 'b' * 40)
    monkeypatch.setattr(up.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(up, 'installed', lambda: (current, 'b' * 40))
    monkeypatch.setattr(up, 'containers', lambda path: {s: {'bundle': str(current), 'image': 'sha256:new'} for s in up.SERVICES})
    monkeypatch.setattr(up, 'validate_lineage', lambda *args: None)
    monkeypatch.setattr(up, 'preflight', lambda *args, **kwargs: None)
    live = {'state': 'idle', 'active_generation': 8, 'last_known_good_generation': 8,
            'active_generation_id': str(uuid4()), 'pending_generation_id': None}
    monkeypatch.setattr(up, 'verify', lambda *args: live)
    monkeypatch.setattr(up, 'backup', lambda *args: tmp_path / 'backup')
    observed = []
    monkeypatch.setattr(up, 'restore', lambda tx: observed.append(tx['agent']))
    monkeypatch.setattr(sys, 'argv', ['update_vortex.py', '--rollback'])
    up.main()
    assert observed == [live]
    assert json.loads((state / 'pending.json').read_text())['agent'] == live


def test_no_build_update_records_pending_before_path_change(monkeypatch, tmp_path):
    state = tmp_path / 'state'
    old = tmp_path / 'old'
    target = tmp_path / 'target'
    for path in (state, old, target): path.mkdir()
    monkeypatch.setattr(up, 'STATE_DIR', state)
    monkeypatch.setattr(up, 'private_root_directory', lambda path: True)
    monkeypatch.setattr(up, 'revision', lambda **kwargs: 'b' * 40)
    monkeypatch.setattr(up, 'workflow_success', lambda sha: None)
    monkeypatch.setattr(up.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(up, 'installed', lambda: (old, 'a' * 40))
    monkeypatch.setattr(up, 'containers', lambda path: {
        s: {'health': 'healthy', 'bundle': str(old), 'id': s, 'image': 'sha256:old'}
        for s in up.SERVICES})
    monkeypatch.setattr(up, 'changed', lambda *args: ['docs/STATUS.md'])
    monkeypatch.setattr(up, 'preflight', lambda *args, **kwargs: None)
    monkeypatch.setattr(up, 'agent_status', lambda *args: {
        'active_generation': 6, 'last_known_good_generation': 6,
        'pending_generation_id': None})
    monkeypatch.setattr(up, 'backup', lambda *args: tmp_path / 'backup')
    monkeypatch.setattr(up, 'stage', lambda *args: target)
    monkeypatch.setattr(up, 'verify', lambda *args: None)
    real_atomic = up.atomic
    def interrupt_path_change(path, *args):
        if path == up.ENV_FILE:
            raise KeyboardInterrupt()
        return real_atomic(path, *args)
    monkeypatch.setattr(up, 'atomic', interrupt_path_change)
    monkeypatch.setattr(sys, 'argv', ['update_vortex.py', '--internal-root'])
    with pytest.raises(KeyboardInterrupt):
        up.main()
    assert json.loads((state / 'pending.json').read_text())['services'] == []


def test_sudo_bootstrap_loads_published_blob(monkeypatch):
    compile(up.ROOT_BOOTSTRAP, '<root-bootstrap>', 'exec')
    monkeypatch.setattr(up, 'revision', lambda **kwargs: 'b' * 40)
    monkeypatch.setattr(up, 'workflow_success', lambda sha: None)
    monkeypatch.setattr(up.os, 'geteuid', lambda: 1000)
    seen = []
    monkeypatch.setattr(up.os, 'execv', lambda path, args: (seen.append((path, args)), None)[1])
    monkeypatch.setattr(sys, 'argv', ['update_vortex.py', '--check'])
    # execv normally does not return; after the mocked handoff main would
    # continue, so stop it at the first root-only read.
    monkeypatch.setattr(up, 'installed', lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        up.main()
    path, args = seen[0]
    assert path == '/usr/bin/sudo' and args[2:6] == ['/usr/bin/python3', '-I', '-S', '-c']
    assert 'git/ref/heads/master' in args[6] and "'show', f'{sha}:scripts/deploy/update_vortex.py'" in args[6]
    assert 'scripts/deploy/update_vortex.py' not in args[:6]
    assert 'safe.directory=' in ' '.join(up.git_command())
    assert up.git_env()['GIT_NO_REPLACE_OBJECTS'] == '1'
    assert up.git_env()['GIT_CONFIG_GLOBAL'] == '/dev/null'
