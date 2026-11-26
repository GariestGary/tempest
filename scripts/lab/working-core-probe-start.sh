#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 && $(cat /sys/class/dmi/id/product_uuid) == ce5fdfe6-0c5d-4487-82d8-f158cdb89609 ]] || exit 1
[[ -e /run/netns/tpab-c && -e /run/netns/tpab-g ]] || exit 1
[[ $(stat -Lc '%d:%i' /run/netns/tpab-c) != "$(stat -Lc '%d:%i' /proc/1/ns/net)" ]] || exit 1
if ip -n tpab-c link show tpab-wg >/dev/null 2>&1; then echo 'Existing probe device' >&2; exit 1; fi
private=/var/lib/tempest-live-acceptance-20261126/private
cmp -s /etc/wireguard/tempest-lab-probe.key "$private/probe.key" || exit 1
ip netns exec tpab-c wireguard-go tpab-wg
ip netns exec tpab-c wg set tpab-wg private-key /etc/wireguard/tempest-lab-probe.key listen-port 51870 peer "$(cat "$private/gateway.pub")" allowed-ips 0.0.0.0/0,::/0 endpoint 192.0.2.1:51871 persistent-keepalive 10
ip -n tpab-c addr add 10.88.0.2/32 dev tpab-wg
ip -n tpab-c -6 addr add fd42:88::2/128 dev tpab-wg nodad
ip -n tpab-c link set tpab-wg mtu 1280 up
ip -n tpab-c route add default dev tpab-wg metric 10
ip -n tpab-c route add default via 203.0.113.10 dev tpab-cs metric 100
ip -n tpab-c -6 route add default dev tpab-wg metric 10
ip -n tpab-c -6 route add default via 2001:db8:12::2 dev tpab-cs metric 100
