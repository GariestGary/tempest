#!/usr/bin/env bash
set -euo pipefail

GATEWAY=tempest-ns-xray
PROBE=tempest-ns-xray-probe

[[ $# == 1 ]] || exit 2
[[ $EUID == 0 ]] || exit 1
[[ $(hostname) == vortex ]] || exit 1

STATE=/run/tempest-wc-network.state
umask 077

identity() {
    stat -Lc '%d:%i' "/run/netns/$1"
}

case "$1" in
    --remove)
        [[ -f $STATE && ! -L $STATE ]]
        [[ $(stat -c '%u:%a' "$STATE") == 0:600 ]]

        expected=$(printf '%s\n%s'             "$(identity "$GATEWAY")"             "$(identity "$PROBE")")

        [[ $(cat "$STATE") == "$expected" ]] || {
            echo "Namespace identity mismatch"
            exit 1
        }

        for ns in "$PROBE" "$GATEWAY"; do
            [[ -z $(ip netns pids "$ns") ]] || {
                echo "Namespace still in use: $ns"
                exit 1
            }
        done

        ip netns del "$PROBE"
        ip netns del "$GATEWAY"
        rm -- "$STATE"

        echo "TEMPEST topology removed."
        exit 0
        ;;
    --create)
        [[ ! -e $STATE && ! -L $STATE ]]
        ;;
    *) exit 2 ;;
esac

for ns in "$GATEWAY" "$PROBE"; do
    if [[ -e /run/netns/$ns || -L /run/netns/$ns ]]; then
        echo "Namespace already exists: $ns"
        exit 1
    fi
done

for link in tpc-g-host tpc-p-host; do
    if ip link show "$link" >/dev/null 2>&1; then
        echo "Interface already exists: $link"
        exit 1
    fi
done

# Reject overlapping host routes before making changes.
ip -j -4 route show table all | python3 -c '
import sys, json, ipaddress
target = ipaddress.ip_network("10.245.249.0/28")
for route in json.load(sys.stdin):
    dst = route.get("dst", "default")
    if dst == "default":
        continue
    if target.overlaps(ipaddress.ip_network(dst, strict=False)):
        sys.exit("Route collision: " + dst)
'

created_gateway=0
created_probe=0

rollback() {
    rc=$?
    if (( rc != 0 )); then
        set +e
        if (( created_probe )); then
            ip netns del "$PROBE"
        fi
        if (( created_gateway )); then
            ip netns del "$GATEWAY"
        fi
    fi
}

trap rollback EXIT

ip netns add "$GATEWAY"
created_gateway=1

ip netns add "$PROBE"
created_probe=1

for ns in "$GATEWAY" "$PROBE"; do
    ip -n "$ns" link set lo up
    ip netns exec "$ns" sysctl -qw \
        net.ipv4.ip_forward=0 \
        net.ipv6.conf.all.forwarding=0
done

# Host <-> Gateway.
ip link add tpc-g-host type veth \
    peer name tpc-g-wan netns "$GATEWAY"

ip addr add 10.245.249.1/30 dev tpc-g-host
ip link set tpc-g-host up

ip -n "$GATEWAY" addr add \
    10.245.249.2/30 dev tpc-g-wan
ip -n "$GATEWAY" link set tpc-g-wan up

ip -n "$GATEWAY" route add default \
    via 10.245.249.1 dev tpc-g-wan

# Gateway <-> Probe.
ip -n "$GATEWAY" link add tpc-g-probe type veth \
    peer name tpc-p-gw netns "$PROBE"

ip -n "$GATEWAY" addr add \
    10.245.249.5/30 dev tpc-g-probe

ip -n "$PROBE" addr add \
    10.245.249.6/30 dev tpc-p-gw

ip -n "$GATEWAY" link set tpc-g-probe up
ip -n "$PROBE" link set tpc-p-gw up

ip -n "$PROBE" route add 10.245.249.2/32 \
    via 10.245.249.5 dev tpc-p-gw

# Host <-> Probe control.
ip link add tpc-p-host type veth \
    peer name tpc-p-ctl netns "$PROBE"

ip addr add 10.245.249.9/30 dev tpc-p-host
ip link set tpc-p-host up

ip -n "$PROBE" addr add \
    10.245.249.10/30 dev tpc-p-ctl
ip -n "$PROBE" link set tpc-p-ctl up

# Allow asymmetric replies on the isolated control link.
ip netns exec "$PROBE" sysctl -qw     net.ipv4.conf.tpc-p-ctl.rp_filter=2

# Independent control route. No Internet fallback.
ip -n "$PROBE" route add table 24801 \
    10.245.249.8/30 dev tpc-p-ctl

ip -n "$PROBE" route add table 24801 \
    10.245.249.1/32 via 10.245.249.9 \
    dev tpc-p-ctl

ip -n "$PROBE" route add unreachable default \
    table 24801

ip -n "$PROBE" rule add priority 1000 \
    from 10.245.249.10/32 lookup 24801

# Disable IPv6 on dedicated transport links only.
for iface in tpc-g-host tpc-p-host; do
    sysctl -qw "net.ipv6.conf.$iface.disable_ipv6=1"
done

for iface in tpc-g-wan tpc-g-probe; do
    ip netns exec "$GATEWAY" sysctl -qw \
        "net.ipv6.conf.$iface.disable_ipv6=1"
done

for iface in tpc-p-gw tpc-p-ctl; do
    ip netns exec "$PROBE" sysctl -qw \
        "net.ipv6.conf.$iface.disable_ipv6=1"
done

printf '%s\n%s\n' \
    "$(identity "$GATEWAY")" \
    "$(identity "$PROBE")" > "$STATE"

echo "TEMPEST namespace topology created."
echo "Firewall, NAT and WG probe are not configured."
