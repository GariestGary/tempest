#!/usr/bin/env bash
# Start the synthetic TLS upstream and independent userspace WG probe.
set -euo pipefail
[[ $EUID == 0 && $(hostname) == tempest-lab-ubuntu ]] || exit 1
[[ $(cat /sys/class/dmi/id/product_uuid) == ce5fdfe6-0c5d-4487-82d8-f158cdb89609 ]] || exit 1
for ns in tpab-g tpab-u tpab-s tpab-c tpab-w; do
    [[ -e /run/netns/$ns && $(stat -Lc '%d:%i' "/run/netns/$ns") != "$(stat -Lc '%d:%i' /proc/1/ns/net)" ]] || exit 1
done
if ip -n tpab-c link show tpab-wg >/dev/null 2>&1; then echo 'Probe interface already exists' >&2; exit 1; fi
if systemctl is-active --quiet tempest-lab-upstream.service; then echo 'Upstream already active' >&2; exit 1; fi
private=/var/lib/tempest-live-acceptance-20261126/private
systemd-run --unit=tempest-lab-upstream --property=NetworkNamespacePath=/run/netns/tpab-u --property=NoNewPrivileges=yes --property=PrivateDevices=yes --property=ProtectSystem=strict --property=CapabilityBoundingSet= /opt/tempest/bin/xray run -config "$private/upstream-tls.json"
ip netns exec tpab-c wireguard-go tpab-wg
ip netns exec tpab-c wg set tpab-wg private-key /etc/wireguard/tempest-lab-probe.key listen-port 51870 peer "$(cat "$private/gateway.pub")" allowed-ips 0.0.0.0/0,::/0 endpoint 192.0.2.1:51871 persistent-keepalive 10
ip -n tpab-c addr add 10.88.0.2/32 dev tpab-wg
ip -n tpab-c -6 addr add fd42:88::2/128 dev tpab-wg nodad
ip -n tpab-c link set tpab-wg mtu 1280 up
ip -n tpab-c route add default dev tpab-wg metric 10
ip -n tpab-c route add default via 203.0.113.10 dev tpab-cs metric 100
ip -n tpab-c -6 route add default dev tpab-wg metric 10
ip -n tpab-c -6 route add default via 2001:db8:12::2 dev tpab-cs metric 100
echo 'Synthetic TLS upstream and userspace probe started in their namespaces.'
