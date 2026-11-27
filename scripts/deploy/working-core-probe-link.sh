#!/usr/bin/env bash
set -euo pipefail

[[ $# == 1 ]] || exit 2
[[ $EUID == 0 && $(hostname) == vortex ]] || exit 1

NS=tempest-ns-xray-probe
DEV=tpc-wg

case "$1" in
    --remove)
        ip -n "$NS" link del "$DEV"
        exit 0
        ;;
    --create) ;;
    *) exit 2 ;;
esac

KEY=/etc/tempest/working-core-probe.key
PUB=/etc/tempest/working-core-gateway.pub

[[ -f $KEY && ! -L $KEY ]]
[[ -f $PUB && ! -L $PUB ]]
[[ $(stat -c '%u:%a' "$KEY") == 0:600 ]]
[[ $(stat -c %u "$PUB") == 0 ]]

if ip -n "$NS" link show "$DEV" >/dev/null 2>&1; then
    echo "Probe interface already exists."
    exit 1
fi

ip -n "$NS" link add "$DEV" type wireguard

rollback() {
    local rc=$?
    if (( rc != 0 )); then
        ip -n "$NS" link del "$DEV" || true
    fi
}

trap rollback EXIT

ip netns exec "$NS" wg set "$DEV" \
    private-key "$KEY" \
    peer "$(cat "$PUB")" \
    allowed-ips 0.0.0.0/0,::/0 \
    endpoint 10.245.249.2:51820 \
    persistent-keepalive 25

ip -n "$NS" addr add 10.245.248.2/32 dev "$DEV"
ip -n "$NS" -6 addr add fd42:245:248::2/128 \
    dev "$DEV" nodad

ip -n "$NS" link set "$DEV" mtu 1280 up

ip -n "$NS" route add default dev "$DEV"
ip -n "$NS" -6 route add default dev "$DEV"

echo "TEMPEST WireGuard probe interface created."
