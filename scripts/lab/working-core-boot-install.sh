#!/usr/bin/env bash
# Install lab-only boot ordering in the named disposable VM.
set -euo pipefail
[[ $EUID == 0 && $(cat /sys/class/dmi/id/product_uuid) == ce5fdfe6-0c5d-4487-82d8-f158cdb89609 ]] || exit 1
[[ ${1:-} == --install && $# == 1 ]] || exit 2
for name in tempest-lab-topology.service tempest-lab-probe.service tempest-lab-user.service tempest-lab-sink-boot.service tempest-lab-upstream-boot.service tempest-lab-panel.service; do
    [[ ! -e /etc/systemd/system/$name ]] || { echo "Existing $name" >&2; exit 1; }
done
[[ ! -e /etc/systemd/system/tempest-xray-agent.service.d/lab-order.conf ]] || exit 1
for file in working-core-topology.sh working-core-probe-start.sh working-core-user-start.py working-core-sink.py; do
    install -o root -g root -m 0755 "/home/gary/$file" "/opt/tempest/bin/$file"
done
cat > /etc/systemd/system/tempest-lab-topology.service <<'EOF'
[Unit]
Description=TEMPEST disposable lab namespaces
After=apparmor.service systemd-modules-load.service
Before=tempest-lab-probe.service tempest-lab-sink-boot.service tempest-lab-upstream-boot.service tempest-lab-user.service
[Service]
Type=oneshot
ExecStart=/opt/tempest/bin/working-core-topology.sh --create
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/tempest-lab-probe.service <<'EOF'
[Unit]
Description=TEMPEST userspace WG readiness probe fixture
Requires=tempest-lab-topology.service
After=tempest-lab-topology.service
Before=tempest-xray-agent.service
[Service]
Type=oneshot
ExecStart=/opt/tempest/bin/working-core-probe-start.sh
RemainAfterExit=yes
EOF
cat > /etc/systemd/system/tempest-lab-user.service <<'EOF'
[Unit]
Description=TEMPEST userspace WG user fixture
Requires=tempest-lab-topology.service
After=tempest-lab-topology.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/tempest/bin/working-core-user-start.py
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/tempest-lab-sink-boot.service <<'EOF'
[Unit]
Description=TEMPEST isolated lab sink
Requires=tempest-lab-topology.service
After=tempest-lab-topology.service
Before=tempest-xray-agent.service
[Service]
NetworkNamespacePath=/run/netns/tpab-s
ExecStart=/usr/bin/python3 /opt/tempest/bin/working-core-sink.py
NoNewPrivileges=yes
PrivateDevices=yes
ProtectSystem=strict
Restart=on-failure
EOF
cat > /etc/systemd/system/tempest-lab-upstream-boot.service <<'EOF'
[Unit]
Description=TEMPEST isolated synthetic TLS upstream
Requires=tempest-lab-topology.service
After=tempest-lab-topology.service
Before=tempest-xray-agent.service
[Service]
NetworkNamespacePath=/run/netns/tpab-u
ExecStart=/opt/tempest/bin/xray run -config /var/lib/tempest-live-acceptance-20261126/private/upstream-tls.json
NoNewPrivileges=yes
PrivateDevices=yes
ProtectSystem=strict
CapabilityBoundingSet=
Restart=on-failure
EOF
install -d -m 0755 /etc/systemd/system/tempest-xray-agent.service.d
cat > /etc/systemd/system/tempest-xray-agent.service.d/lab-order.conf <<'EOF'
[Unit]
Requires=tempest-lab-topology.service tempest-lab-probe.service tempest-lab-sink-boot.service
Wants=tempest-lab-upstream-boot.service
After=tempest-lab-topology.service tempest-lab-probe.service tempest-lab-sink-boot.service tempest-lab-upstream-boot.service
EOF
gid=$(getent group tempest-control | cut -d: -f3)
cat > /etc/systemd/system/tempest-lab-panel.service <<EOF
[Unit]
Description=TEMPEST lab Working Core panel
Requires=docker.service tempest-xray-agent.service
After=docker.service tempest-xray-agent.service
[Service]
Type=oneshot
WorkingDirectory=/home/gary/tempest
Environment=TEMPEST_AGENT_GID=$gid
Environment=TEMPEST_CORE_PRIVATE_DIR=/var/lib/tempest-core-private
Environment=TEMPEST_PANEL_PORT=8787
ExecStart=/usr/bin/docker compose -f docker-compose.working-core.yml up -d --no-build
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
systemd-analyze verify /etc/systemd/system/tempest-lab-{topology,probe,user,sink-boot,upstream-boot,panel}.service
systemctl daemon-reload
systemctl enable docker.service docker.socket tempest-lab-topology.service tempest-lab-user.service tempest-xray-agent.service tempest-lab-panel.service
echo 'Lab boot ordering installed; currently running services were not restarted.'
