#!/usr/bin/env bash
# Install the reviewed AWG integration into the existing disposable VM only.
set -euo pipefail
umask 077
[[ $EUID == 0 && $(hostname) == tempest-lab-ubuntu ]] || exit 1
[[ $(cat /sys/class/dmi/id/product_uuid) == ce5fdfe6-0c5d-4487-82d8-f158cdb89609 ]] || exit 1
stage=/home/gary/tempest-awg-stage
source_dir="$stage/source"
backup=/var/lib/tempest-awg-trial-2
[[ -d "$source_dir" && ! -e "$backup" && ! -e /opt/tempest/bin/awgproxy ]]
[[ ! -e /etc/systemd/system/tempest-awgproxy.service && ! -e /etc/systemd/system/tempest-xray-probe.service.d/awg-lab.conf ]]
if getent passwd tempest-awgproxy >/dev/null; then
  [[ $(id -gn tempest-awgproxy) == tempest-xray && $(getent passwd tempest-awgproxy | cut -d: -f7) == /usr/sbin/nologin ]]
fi
[[ $(stat -Lc '%i' /proc/1/ns/net) != "$(stat -Lc '%i' /run/netns/tpab-g)" ]]
[[ $(stat -Lc '%i' /run/netns/tpab-c) != "$(stat -Lc '%i' /run/netns/tpab-g)" ]]
printf '%s  %s\n' e0fe256ad362221026a15a372a246b21026b0405e4dc32ab8f2c87c92543686e "$stage/awgproxy" | sha256sum -c -

files=(
  /opt/tempest/backend/app/core/working_core.py
  /opt/tempest/backend/app/network_agent/models.py
  /opt/tempest/backend/app/network_agent/xray_agent.py
  /opt/tempest/backend/app/network_agent/xray_profile.py
  /opt/tempest/backend/app/network_agent/xray_runtime.py
  /etc/polkit-1/rules.d/49-tempest-xray.rules
  /var/lib/tempest-core-private/deployment.json
  /var/lib/tempest-xray-agent/xray.json
)
[[ ! -e /opt/tempest/backend/app/core/awg_bridge.py ]]
install -d -m 0700 "$backup"
cp -a --parents "${files[@]}" "$backup"
printf '%s\n' "${files[@]}" > "$backup/original-paths"

restore_files() {
  systemctl stop tempest-xray-agent.service || true
  for file in "${files[@]}"; do
    # The durable journal can contain later client revocations: never restore it.
    [[ "$file" == /var/lib/tempest-xray-agent/xray.json ]] && continue
    cp -a "$backup$file" "$file"
  done
  rm -f /opt/tempest/backend/app/core/awg_bridge.py /opt/tempest/bin/awgproxy \
    /opt/tempest/bin/working-core-awg-probe /opt/tempest/bin/xray_mvp_probe_checks.py \
    /etc/systemd/system/tempest-awgproxy.service /etc/systemd/system/tempest-xray-probe.service.d/awg-lab.conf
  systemctl daemon-reload
  systemctl start tempest-xray-agent.service || true
}
trap 'restore_files' ERR
systemctl stop tempest-xray-agent.service
if ! getent passwd tempest-awgproxy >/dev/null; then
  useradd --system --no-create-home --shell /usr/sbin/nologin --gid tempest-xray tempest-awgproxy
fi
install -o root -g root -m 0755 "$stage/awgproxy" /opt/tempest/bin/awgproxy
for relative in core/working_core.py core/awg_bridge.py network_agent/models.py network_agent/xray_agent.py network_agent/xray_profile.py network_agent/xray_runtime.py; do
  install -o root -g root -m 0644 "$source_dir/backend/app/$relative" "/opt/tempest/backend/app/$relative"
done
install -o root -g root -m 0755 "$source_dir/scripts/lab/working-core-awg-probe.py" /opt/tempest/bin/working-core-awg-probe
install -o root -g root -m 0644 "$source_dir/scripts/lab/xray_mvp_probe_checks.py" /opt/tempest/bin/xray_mvp_probe_checks.py
sed 's@/run/netns/tempest-ns-xray@/run/netns/tpab-g@g' "$source_dir/deploy/systemd/tempest-awgproxy.service" > /etc/systemd/system/tempest-awgproxy.service
chmod 0644 /etc/systemd/system/tempest-awgproxy.service
install -o root -g root -m 0644 "$source_dir/deploy/systemd/49-tempest-xray.rules" /etc/polkit-1/rules.d/49-tempest-xray.rules
install -d -m 0755 /etc/systemd/system/tempest-xray-probe.service.d
printf '[Service]\nExecStart=\nExecStart=/opt/tempest/bin/working-core-awg-probe\n' > /etc/systemd/system/tempest-xray-probe.service.d/awg-lab.conf
chmod 0644 /etc/systemd/system/tempest-xray-probe.service.d/awg-lab.conf
python3 - "$backup/var/lib/tempest-core-private/deployment.json" "$stage/deployment.json" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    value = json.load(f)
rule = {'destination': '1.1.1.1/32', 'port': 443, 'egress': 'vpn'}
if rule in value['probe']['rules']:
    raise SystemExit('probe route already exists')
value['probe']['rules'].append(rule)
with open(sys.argv[2], 'w') as f:
    json.dump(value, f)
PY
install -o root -g tempest-control -m 0640 "$stage/deployment.json" /var/lib/tempest-core-private/deployment.json
rm -f "$stage/deployment.json"
systemctl daemon-reload
systemctl start tempest-xray-agent.service
systemctl is-active --quiet tempest-xray-agent.service
for attempt in {1..30}; do
  if systemctl is-active --quiet tempest-xray.service; then break; fi
  sleep 1
done
systemctl is-active --quiet tempest-xray.service
trap - ERR
echo 'AWG lab components installed; Xray Agent and previous LKG restored.'
