#!/usr/bin/env bash
# Local-only access to the two explicitly approved userspace WG sockets.
set -euo pipefail
[[ $EUID == 0 && $(hostname) == tempest-lab-ubuntu ]] || exit 1
[[ $(cat /sys/class/dmi/id/product_uuid) == ce5fdfe6-0c5d-4487-82d8-f158cdb89609 ]] || exit 1
profile=/etc/apparmor.d/wg
local_rule=/etc/apparmor.d/local/wg
backup=/var/lib/tempest-live-acceptance-20261126/apparmor-wg-original
expected=$'  /run/wireguard/ r,\n  /run/wireguard/tpab-wg.sock rw,\n  /run/wireguard/tpab-user.sock rw,'
case ${1:-} in
  --install)
    [[ $# == 1 && ! -e $local_rule && ! -e $backup ]] || { echo 'Existing rules or backup; refusing overwrite' >&2; exit 1; }
    grep -q 'include if exists <local/wg>' "$profile"
    grep -qx 'wg (enforce)' /sys/kernel/security/apparmor/profiles
    cp -- "$profile" "$backup"
    chmod 0600 "$backup"
    printf '%s\n' "$expected" > "$local_rule"
    chmod 0644 "$local_rule"
    apparmor_parser -Q -K "$profile"
    apparmor_parser -r -K "$profile"
    grep -qx 'wg (enforce)' /sys/kernel/security/apparmor/profiles
    cmp -s "$profile" "$backup"
    echo 'Scoped local socket rules loaded; base profile unchanged and enforcing.'
    ;;
  --rollback)
    [[ $# == 1 && -f $backup && -f $local_rule ]] || exit 1
    cmp -s "$profile" "$backup" || { echo 'Base profile changed; manual review required' >&2; exit 1; }
    [[ $(cat "$local_rule") == "$expected" ]] || { echo 'Local rules changed; manual review required' >&2; exit 1; }
    rm -- "$local_rule"
    apparmor_parser -Q -K "$profile"
    apparmor_parser -r -K "$profile"
    grep -qx 'wg (enforce)' /sys/kernel/security/apparmor/profiles
    echo 'Scoped local socket rules removed; base profile enforcing.'
    ;;
  *) echo 'Use --install or --rollback' >&2; exit 2 ;;
esac
