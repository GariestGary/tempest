#!/usr/bin/env bash
set -euo pipefail

[[ $# == 1 ]] || exit 2
[[ $EUID == 0 && $(hostname) == vortex ]] || exit 1

WAN=wlo2
HOST_IP=192.168.1.66
GW_IP=10.245.249.2
TAG=tempest-working-core-v1

ipt() { iptables -w 5 "$@"; }

chains=(
    filter/TPC_WC_FWD
    filter/TPC_WC_INPUT
    nat/TPC_WC_DNAT
    nat/TPC_WC_SNAT
)

chain_exists() {
    ipt -t "$1" -S "$2" >/dev/null 2>&1
}

hook() {
    local mode=$1 table=$2 parent=$3
    shift 3

    if [[ $mode == add ]]; then
        ipt -t "$table" -I "$parent" 1 "$@"
    elif ipt -t "$table" -C "$parent" "$@" \
            >/dev/null 2>&1; then
        ipt -t "$table" -D "$parent" "$@"
    fi
}

hooks() {
    local mode=$1

    for iface in tpc-g-host tpc-p-host; do
        hook "$mode" filter DOCKER-USER \
            -i "$iface" -m comment --comment "$TAG" \
            -j TPC_WC_FWD

        hook "$mode" filter DOCKER-USER \
            -o "$iface" -m comment --comment "$TAG" \
            -j TPC_WC_FWD

        hook "$mode" filter INPUT \
            -i "$iface" -m comment --comment "$TAG" \
            -j TPC_WC_INPUT
    done

    hook "$mode" nat PREROUTING \
        -i "$WAN" -d "$HOST_IP/32" \
        -p udp --dport 51820 \
        -m comment --comment "$TAG" \
        -j TPC_WC_DNAT

    hook "$mode" nat POSTROUTING \
        -o "$WAN" -s "$GW_IP/32" \
        -m comment --comment "$TAG" \
        -j TPC_WC_SNAT
}

remove_chains() {
    local spec table chain

    for spec in "$@"; do
        table=${spec%/*}
        chain=${spec#*/}

        if chain_exists "$table" "$chain"; then
            ipt -t "$table" -F "$chain"
            ipt -t "$table" -X "$chain"
        fi
    done
}

case "$1" in
    --remove)
        for spec in "${chains[@]}"; do
            table=${spec%/*}
            chain=${spec#*/}

            if chain_exists "$table" "$chain"; then
                ipt -t "$table" -C "$chain" \
                    -m comment --comment "$TAG" || {
                    echo "Foreign chain: $chain"
                    exit 1
                }
            fi
        done

        hooks remove
        remove_chains "${chains[@]}"
        echo "TEMPEST firewall removed."
        exit 0
        ;;
    --create) ;;
    *) exit 2 ;;
esac

# Preconditions.
[[ $(sysctl -n net.ipv4.ip_forward) == 1 ]]

ip -o -4 addr show dev "$WAN" |
    grep -q "inet $HOST_IP/24"

ip link show tpc-g-host >/dev/null
ip link show tpc-p-host >/dev/null
ipt -S DOCKER-USER >/dev/null

if [[ -n $(ss -H -lun 'sport = :51820') ]]; then
    echo "UDP 51820 is occupied."
    exit 1
fi

if systemctl is-active --quiet tempest-port-guard.timer; then
    echo "Legacy port guard is still active."
    exit 1
fi

for spec in "${chains[@]}"; do
    if chain_exists "${spec%/*}" "${spec#*/}"; then
        echo "Chain already exists: $spec"
        exit 1
    fi
done

created=()
hooks_started=0

rollback() {
    local rc=$?
    if (( rc != 0 )); then
        set +e
        if (( hooks_started )); then
            hooks remove
        fi
        remove_chains "${created[@]}"
    fi
    exit "$rc"
}

trap rollback EXIT

for spec in "${chains[@]}"; do
    ipt -t "${spec%/*}" -N "${spec#*/}"
    created+=("$spec")

    ipt -t "${spec%/*}" -A "${spec#*/}" \
        -m comment --comment "$TAG"
done

# Gateway outgoing traffic.
ipt -A TPC_WC_FWD \
    -i tpc-g-host -o "$WAN" \
    -s "$GW_IP/32" \
    -m conntrack --ctstate NEW,ESTABLISHED,RELATED \
    -j ACCEPT

# Incoming responses.
ipt -A TPC_WC_FWD \
    -i "$WAN" -o tpc-g-host \
    -d "$GW_IP/32" \
    -m conntrack --ctstate ESTABLISHED,RELATED \
    -j ACCEPT

# Published WireGuard ingress.
ipt -A TPC_WC_FWD \
    -i "$WAN" -o tpc-g-host \
    -d "$GW_IP/32" -p udp --dport 51820 \
    -m conntrack --ctstate NEW \
    --ctproto udp --ctorigdst "$HOST_IP" --ctorigdstport 51820 \
    --ctdir ORIGINAL \
    -j ACCEPT

ipt -A TPC_WC_FWD -j DROP

# Local trusted verification fixtures.
for proto in tcp udp; do
    ipt -A TPC_WC_INPUT \
        -i tpc-g-host \
        -s "$GW_IP/32" -d 10.245.249.1/32 \
        -p "$proto" \
        -m multiport --dports 18080,18081 \
        -j ACCEPT

    ipt -A TPC_WC_INPUT \
        -i tpc-p-host \
        -s 10.245.249.10/32 -d 10.245.249.1/32 \
        -p "$proto" --dport 18081 \
        -j ACCEPT
done

ipt -A TPC_WC_INPUT -j DROP

# NAT.
ipt -t nat -A TPC_WC_DNAT \
    -p udp -j DNAT --to-destination "$GW_IP:51820"

ipt -t nat -A TPC_WC_SNAT -j MASQUERADE

# Activate only the scoped hooks.
hooks_started=1
hooks add

echo "TEMPEST firewall created."
