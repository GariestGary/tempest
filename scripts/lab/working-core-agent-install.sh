#!/usr/bin/env bash
# Install the supplied Agent units for the approved disposable VM only.
set -euo pipefail
[[ $EUID == 0 && $(hostname) == tempest-lab-ubuntu ]] || exit 1
[[ $(cat /sys/class/dmi/id/product_uuid) == ce5fdfe6-0c5d-4487-82d8-f158cdb89609 ]] || exit 1
expected_sha=${TEMPEST_LAB_EXPECTED_SHA:-d98ec3da94469c784f8aaa46401e5f36258bdd0d}
[[ $expected_sha =~ ^[0-9a-f]{40}$ && $(git -C /home/gary/tempest rev-parse HEAD) == "$expected_sha" ]] || exit 1
[[ -z $(git -C /home/gary/tempest status --porcelain) ]] || { echo 'Guest checkout is not clean' >&2; exit 1; }
[[ -e /run/netns/tpab-g && -e /run/netns/tpab-c ]] || exit 1
[[ ! -e /opt/tempest/backend && ! -e /opt/tempest/python311 && ! -e /etc/tempest/xray-agent.env ]] || { echo 'Existing Agent deployment: refusing overwrite' >&2; exit 1; }
for name in tempest-control tempest-xray tempest-xray-agent tempest-xray-probe; do
    if getent group "$name" >/dev/null || getent passwd "$name" >/dev/null; then echo "Identity already exists: $name" >&2; exit 1; fi
done
for name in tempest-control tempest-xray tempest-xray-agent tempest-xray-probe; do groupadd --system "$name"; done
useradd --system --no-create-home --shell /usr/sbin/nologin --gid tempest-xray-agent tempest-xray-agent
useradd --system --no-create-home --shell /usr/sbin/nologin --gid tempest-xray tempest-xray
useradd --system --no-create-home --shell /usr/sbin/nologin --gid tempest-xray-probe tempest-xray-probe
cp -a /home/gary/.local/share/tempest-python311 /opt/tempest/python311
install -d -m 0755 /opt/tempest/backend
cp -a /home/gary/tempest/backend/app /opt/tempest/backend/app
install -m 0644 /home/gary/tempest/backend/requirements.txt /opt/tempest/backend/requirements.txt
/opt/tempest/python311/bin/python3.11 -m venv /opt/tempest/backend/.venv
/home/gary/.local/share/tempest-uv/bin/uv pip install --python /opt/tempest/backend/.venv/bin/python -r /opt/tempest/backend/requirements.txt
install -m 0755 /home/gary/tempest/scripts/lab/xray-mvp-probe.py /opt/tempest/bin/xray-mvp-probe
install -d -m 0755 /etc/tempest
gid=$(getent group tempest-control | cut -d: -f3)
printf 'TEMPEST_AGENT_ALLOWED_UIDS=10001\nTEMPEST_AGENT_ALLOWED_GIDS=%s\nTEMPEST_XRAY_LISTEN=192.0.2.1\nTEMPEST_XRAY_PORT=51871\n' "$gid" > /etc/tempest/xray-agent.env
chmod 0644 /etc/tempest/xray-agent.env
for unit in tempest-xray-agent.service tempest-xray.service tempest-xray-probe.service; do
    sed -e 's@/run/netns/tempest-ns-xray-probe@/run/netns/tpab-c@g' -e 's@/run/netns/tempest-ns-xray@/run/netns/tpab-g@g' -e 's/--xray-netns=tempest-ns-xray/--xray-netns=tpab-g/g' "/home/gary/tempest/deploy/systemd/$unit" > "/etc/systemd/system/$unit"
    chmod 0644 "/etc/systemd/system/$unit"
done
install -m 0644 /home/gary/tempest/deploy/systemd/tempest-xray.tmpfiles.conf /etc/tmpfiles.d/tempest-xray.conf
install -m 0644 /home/gary/tempest/deploy/systemd/49-tempest-xray.rules /etc/polkit-1/rules.d/49-tempest-xray.rules
systemd-tmpfiles --create /etc/tmpfiles.d/tempest-xray.conf
systemctl daemon-reload
systemctl start tempest-xray-agent.service
systemctl is-active tempest-xray-agent.service
echo "Agent installed; authorized group $gid. No Xray dataplane started."
