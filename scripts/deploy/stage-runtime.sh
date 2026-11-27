#!/usr/bin/env bash
# Prepare local operator-owned inputs; this does not install or start TEMPEST.
set -euo pipefail
umask 077
if [[ $# -ne 4 || $4 != /* ]]; then
  printf 'Usage: %s XRAY AWGPROXY EXTERNAL_SING_BOX ABSOLUTE_NEW_STAGE_DIR\n' "$0" >&2
  exit 2
fi
xray=$1
awg=$2
singbox=$3
stage=$4
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
python3 "$root/scripts/deploy/verify-runtime.py" --xray "$xray" --awgproxy "$awg" --sing-box "$singbox"
[[ ! -e "$stage" && ! -L "$stage" ]] || { printf 'Stage already exists\n' >&2; exit 1; }
mkdir -m 0700 -- "$stage"
cleanup() {
  rm -f -- "$stage/xray" "$stage/awgproxy" "$stage/PiTun-LICENSE.txt" "$stage/awgproxy-ISC.txt" "$stage/xray-MPL-2.0.txt"
  rm -rf -- "$stage/awg-dependencies"
  rmdir -- "$stage" 2>/dev/null || true
}
trap cleanup ERR
install -m 0755 -- "$xray" "$stage/xray"
install -m 0755 -- "$awg" "$stage/awgproxy"
install -m 0644 -- "$root/LICENSE" "$stage/PiTun-LICENSE.txt"
install -m 0644 -- "$root/docs/licenses/awgproxy-ISC.txt" "$stage/awgproxy-ISC.txt"
install -m 0644 -- "$root/docs/licenses/xray-MPL-2.0.txt" "$stage/xray-MPL-2.0.txt"
cp -a -- "$root/docs/licenses/awg-dependencies" "$stage/awg-dependencies"
python3 "$root/scripts/deploy/verify-runtime.py" --xray "$stage/xray" --awgproxy "$stage/awgproxy" --sing-box "$singbox"
trap - ERR
printf 'Local stage prepared at %s. External sing-box was verified but not copied.\n' "$stage"
printf 'Not a deployable release: namespace, probe, packaging and transitive notices remain open.\n'
