#!/usr/bin/env bash
# Download a pinned operator-supplied Python archive; never install it.
set -euo pipefail
umask 077
if [[ $# -ne 1 || $1 != /* ]]; then printf 'Usage: %s ABSOLUTE_NEW_ARCHIVE_PATH\n' "$0" >&2; exit 2; fi
[[ $EUID != 0 ]] || { printf 'Run as an unprivileged operator\n' >&2; exit 1; }
target=$1
[[ ! -e "$target" && ! -L "$target" ]] || { printf 'Target exists\n' >&2; exit 1; }
parent=$(dirname -- "$target")
[[ -d $parent && ! -L $parent ]] || { printf 'Existing non-symlink parent directory required\n' >&2; exit 1; }
tmp=$(mktemp -p "$parent" .tempest-python-XXXXXXXX)
cleanup() { rm -f -- "$tmp"; }
trap cleanup EXIT
curl -fsSL --retry 2 --retry-delay 2 --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 180 \
  'https://github.com/astral-sh/python-build-standalone/releases/download/20261124/cpython-3.11.18%2B20261124-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz' -o "$tmp"
printf '%s  %s\n' d873f435e58d396a18858d4f52a335f1abe8cea385cb9d411ec511f958249d54 "$tmp" | sha256sum -c -
# Hard-linking refuses to overwrite even if the destination appeared meanwhile.
ln -- "$tmp" "$target"
printf 'Verified Python archive prepared; no installation performed.\n'
