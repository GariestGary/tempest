#!/usr/bin/env bash
# Fetch fixed upstream programs into a new operator-owned directory; no installation.
set -euo pipefail
umask 077
if [[ $# -ne 1 || $1 != /* ]]; then printf 'Usage: %s ABSOLUTE_NEW_DIRECTORY\n' "$0" >&2; exit 2; fi
[[ $EUID != 0 ]] || { printf 'Run as an unprivileged operator\n' >&2; exit 1; }
out=$1
[[ ! -e "$out" && ! -L "$out" ]] || { printf 'Destination already exists\n' >&2; exit 1; }
tmp=$(mktemp -d)
cleanup() { rm -rf -- "$tmp"; }
trap cleanup EXIT
curl -fsSL --retry 2 --retry-delay 2 --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 180 \
  https://github.com/XTLS/Xray-core/releases/download/v26.10.31/Xray-linux-64.zip -o "$tmp/xray.zip"
unzip -p "$tmp/xray.zip" xray > "$tmp/xray"
printf '%s  %s\n' cc4b84d3069569f297a61f04b914f2bb37e9d202fd7b84cb50def1fc30fd40e1 "$tmp/xray" | sha256sum -c -
curl -fsSL --retry 2 --retry-delay 2 --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 180 \
  https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-amd64.tar.gz -o "$tmp/sing-box.tar.gz"
printf '%s  %s\n' a684484d7477d1437282ee411f4d131d0340aaad60a7868841ebd5d87dd8a0c6 "$tmp/sing-box.tar.gz" | sha256sum -c -
tar -xOzf "$tmp/sing-box.tar.gz" sing-box-1.14.2-linux-amd64/sing-box > "$tmp/sing-box"
printf '%s  %s\n' fc9c6e6ab345f045b16a0ed10d1ff28d68e8e56e7749fca30738d1406e98d7b8 "$tmp/sing-box" | sha256sum -c -
mkdir -m 0700 -- "$out"
if ! install -m 0755 -- "$tmp/xray" "$out/xray" || ! install -m 0755 -- "$tmp/sing-box" "$out/sing-box"; then
  rm -f -- "$out/xray" "$out/sing-box"; rmdir -- "$out" 2>/dev/null || true; exit 1
fi
printf 'Official files prepared. sing-box is operator-supplied and must remain outside TEMPEST bundles/images.\n'
