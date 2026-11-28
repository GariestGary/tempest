from pathlib import Path
import importlib.util
import os
import socket
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_firewall_selects_udp_for_original_port_and_dnat():
    firewall = (ROOT / 'scripts/deploy/working-core-firewall.sh').read_text()
    assert '--ctproto udp --ctorigdst "$HOST_IP" --ctorigdstport 51820' in firewall
    assert '-p udp -j DNAT --to-destination "$GW_IP:51820"' in firewall


def test_agent_archive_permissions_are_restricted_to_control_group():
    installer = (ROOT / 'scripts/deploy/install-agent-code.sh').read_text()
    assert 'chown -R root:tempest-control -- "$staging/app"' in installer
    assert '-type d -exec chmod 0750' in installer
    assert '-type f -exec chmod 0640' in installer


def test_apparmor_exception_is_limited_to_probe_key_read():
    policy = (ROOT / 'deploy/working-core/apparmor-wg.local').read_text()
    rules = [line.strip() for line in policy.splitlines() if line.strip() and not line.startswith('#')]
    assert rules == ['/etc/tempest/working-core-probe.key r,']


def test_public_context_ignores_restrictive_umask_and_untracked_files(tmp_path):
    target = tmp_path / 'public'
    subprocess.run(['bash', str(ROOT / 'scripts/deploy/prepare-build-context.sh'), 'HEAD', str(target)],
                   check=True, preexec_fn=lambda: os.umask(0o077))
    assert stat.S_IMODE((target / 'backend').stat().st_mode) == 0o755
    assert stat.S_IMODE((target / 'backend/app/working_core.py').stat().st_mode) == 0o644
    assert stat.S_IMODE((target / 'backend/entrypoint.sh').stat().st_mode) == 0o755
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_readiness_requires_connectable_socket_and_active_service(tmp_path):
    spec = importlib.util.spec_from_file_location('wait_agent', ROOT / 'scripts/deploy/wait-agent-socket.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / 'agent.sock'
    assert not module.wait_for_agent(path, 0, lambda: True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        assert not module.wait_for_agent(path, 0, lambda: True)
        server.listen()
        assert not module.wait_for_agent(path, 0, lambda: False)
        assert module.wait_for_agent(path, 0, lambda: True)


def test_panel_boot_unit_waits_for_network_and_connectable_agent():
    panel = (ROOT / 'deploy/working-core/tempest-wc-panel.service').read_text()
    agent = (ROOT / 'deploy/systemd/tempest-xray-agent.service').read_text()

    assert 'Requires=docker.service tempest-wc-network.service tempest-xray-agent.service' in panel
    assert 'After=docker.service tempest-wc-network.service tempest-xray-agent.service' in panel
    assert 'wait-agent-socket.py --socket=/run/tempest-xray-agent/control.sock --timeout=120' in panel
    assert 'docker compose' in panel and 'start --wait --wait-timeout 120' in panel
    assert 'docker compose' in panel and 'stop panel api' in panel
    assert 'BindsTo=tempest-wc-network.service' in agent
    assert 'After=tempest-wc-network.service' in agent


def test_systemd_installer_preflight_is_read_only_and_does_not_print_env(tmp_path):
    import shutil

    deploy_dir = tmp_path / 'deploy'
    (deploy_dir / 'deploy/working-core').mkdir(parents=True)
    shutil.copy(ROOT / 'docker-compose.working-core.yml', deploy_dir)
    shutil.copy(ROOT / 'deploy/working-core/vortex.compose.yml', deploy_dir / 'deploy/working-core')
    (deploy_dir / '.env').write_text('TEMPEST_PRIVATE_MARKER=must-not-be-printed\n')
    installer = ROOT / 'scripts/deploy/install-working-core-systemd.sh'

    result = subprocess.run(
        ['bash', str(installer), '--check', '--deploy-dir', str(deploy_dir)],
        check=True, capture_output=True, text=True,
    )

    assert 'Read-only preflight passed' in result.stdout
    assert 'must-not-be-printed' not in result.stdout + result.stderr
    assert not (deploy_dir / 'systemd').exists()
    source = installer.read_text()
    assert 'systemctl enable tempest-wc-network.service tempest-xray-agent.service tempest-wc-panel.service' in source
    assert 'systemctl start ' not in source
    assert '--now' not in source


def test_systemd_installer_refuses_incomplete_compose_bundle_without_echoing_env(tmp_path):
    deploy_dir = tmp_path / 'incomplete'
    deploy_dir.mkdir()
    (deploy_dir / '.env').write_text('TEMPEST_PRIVATE_MARKER=must-not-be-printed\n')
    installer = ROOT / 'scripts/deploy/install-working-core-systemd.sh'

    result = subprocess.run(
        ['bash', str(installer), '--check', '--deploy-dir', str(deploy_dir)],
        capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert 'Required regular deployment file is missing' in result.stderr
    assert 'must-not-be-printed' not in result.stdout + result.stderr
