"""Offline regression tests for the operator-triggered updater."""
import importlib.util
import json
from pathlib import Path
import sys

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
