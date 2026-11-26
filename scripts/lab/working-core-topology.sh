#!/usr/bin/env bash
# Create the explicitly approved Working Core fixture in a disposable VM.
set -euo pipefail

[[ ${1:-} == --create && $# == 1 ]] || { echo 'Usage: working-core-topology.sh --create' >&2; exit 2; }
[[ $EUID == 0 ]] || { echo 'Root is required inside the lab VM' >&2; exit 1; }
[[ $(hostname) == tempest-lab-ubuntu ]] || { echo 'Unexpected VM hostname' >&2; exit 1; }
[[ $(cat /sys/class/dmi/id/product_uuid) == ce5fdfe6-0c5d-4487-82d8-f158cdb89609 ]] || { echo 'Unexpected VM UUID' >&2; exit 1; }

names=(tpab-g tpab-u tpab-s tpab-c tpab-w)
links=(tpab-cg tpab-gc tpab-gu tpab-ug tpab-gs tpab-sg tpab-us tpab-su tpab-cs tpab-sc tpab-gw tpab-wc tpab-wg tpab-user)
for name in "${names[@]}"; do
    [[ ! -e /run/netns/$name ]] || { echo "Namespace already exists: $name" >&2; exit 1; }
done
for link in "${links[@]}"; do
    if ip link show dev "$link" >/dev/null 2>&1; then echo "Link already exists: $link" >&2; exit 1; fi
done

for name in "${names[@]}"; do
    ip netns add "$name"
    ip -n "$name" link set lo up
    ip netns exec "$name" sysctl -qw net.ipv4.ip_forward=0 net.ipv6.conf.all.forwarding=0
done
pair() { ip -n "$1" link add "$2" type veth peer name "$4" netns "$3"; ip -n "$1" link set "$2" up; ip -n "$3" link set "$4" up; }
pair tpab-g tpab-gc tpab-c tpab-cg
pair tpab-g tpab-gu tpab-u tpab-ug
pair tpab-g tpab-gs tpab-s tpab-sg
pair tpab-u tpab-us tpab-s tpab-su
pair tpab-c tpab-cs tpab-s tpab-sc
pair tpab-g tpab-gw tpab-w tpab-wc
ip -n tpab-g addr add 192.0.2.1/30 dev tpab-gc
ip -n tpab-c addr add 192.0.2.2/30 dev tpab-cg
ip -n tpab-g addr add 192.0.2.21/31 dev tpab-gu
ip -n tpab-u addr add 192.0.2.20/31 dev tpab-ug
ip -n tpab-g addr add 192.0.2.5/30 dev tpab-gw
ip -n tpab-w addr add 192.0.2.6/30 dev tpab-wc
ip -n tpab-w route add 192.0.2.1/32 via 192.0.2.5 dev tpab-wc
ip -n tpab-g addr add 203.0.113.1/30 dev tpab-gs
ip -n tpab-s addr add 203.0.113.2/30 dev tpab-sg
ip -n tpab-u addr add 203.0.113.5/30 dev tpab-us
ip -n tpab-s addr add 203.0.113.6/30 dev tpab-su
ip -n tpab-c addr add 203.0.113.9/30 dev tpab-cs
ip -n tpab-s addr add 203.0.113.10/30 dev tpab-sc
ip -n tpab-g -6 addr add 2001:db8:10::1/64 dev tpab-gs nodad
ip -n tpab-s -6 addr add 2001:db8:10::2/64 dev tpab-sg nodad
ip -n tpab-u -6 addr add 2001:db8:11::1/64 dev tpab-us nodad
ip -n tpab-s -6 addr add 2001:db8:11::2/64 dev tpab-su nodad
ip -n tpab-c -6 addr add 2001:db8:12::1/64 dev tpab-cs nodad
ip -n tpab-s -6 addr add 2001:db8:12::2/64 dev tpab-sc nodad
for ns in tpab-g tpab-u; do
    if [[ $ns == tpab-g ]]; then via4=203.0.113.2; via6=2001:db8:10::2; else via4=203.0.113.6; via6=2001:db8:11::2; fi
    ip -n "$ns" route add 198.51.100.0/24 via "$via4"
    ip -n "$ns" -6 route add 2001:db8:2::/64 via "$via6"
done
for last in 10 20 30 53; do
    ip -n tpab-s addr add "198.51.100.$last/32" dev lo
    ip -n tpab-s -6 addr add "2001:db8:2::$last/128" dev lo nodad
done
for name in "${names[@]}"; do
    printf '%s ' "$name"; stat -Lc '%d:%i' "/run/netns/$name"
done
echo 'Fixture links and namespace-local routes created; no WG device or services started.'
