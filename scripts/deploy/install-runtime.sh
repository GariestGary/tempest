#!/usr/bin/env bash
# Binary-only, initial install. Does not create users, start services or configure networking.
set -euo pipefail
umask 077
if [[ $# -ne 4 || ( $1 != --check && $1 != --install ) ]]; then
  printf 'Usage: %s --check|--install XRAY AWGPROXY EXTERNAL_SING_BOX\n' "$0" >&2
  exit 2
fi
mode=$1; xray=$2; awg=$3; singbox=$4
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
python3 "$root/scripts/deploy/verify-runtime.py" --xray "$xray" --awgproxy "$awg" --sing-box "$singbox"
[[ ! -e /opt/tempest && ! -L /opt/tempest ]] || { printf 'Refusing: /opt/tempest already exists\n' >&2; exit 1; }
printf 'Binary-only initial install target: /opt/tempest/bin/{xray,awgproxy,sing-box}; no services or network configured.\n'
if [[ $mode == --check ]]; then exit 0; fi
[[ $EUID == 0 ]] || { printf '%s\n' '--install requires root' >&2; exit 1; }
[[ -d /opt && ! -L /opt && $(stat -c %u /opt) == 0 ]] || { printf 'Unsafe /opt\n' >&2; exit 1; }
mkdir -m 0755 -- /opt/tempest
cleanup() {
  rm -f -- /opt/tempest/bin/xray /opt/tempest/bin/awgproxy /opt/tempest/bin/sing-box
  rmdir -- /opt/tempest/bin /opt/tempest 2>/dev/null || true
}
trap cleanup ERR
mkdir -m 0755 -- /opt/tempest/bin
install -o root -g root -m 0755 -- "$xray" /opt/tempest/bin/xray
install -o root -g root -m 0755 -- "$awg" /opt/tempest/bin/awgproxy
install -o root -g root -m 0755 -- "$singbox" /opt/tempest/bin/sing-box
python3 "$root/scripts/deploy/verify-runtime.py" --xray /opt/tempest/bin/xray --awgproxy /opt/tempest/bin/awgproxy --sing-box /opt/tempest/bin/sing-box
trap - ERR
printf 'Verified binaries installed. Gateway and panel are not installed or started.\n'
