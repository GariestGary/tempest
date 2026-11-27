#!/usr/bin/env bash
# Install only Agent source from the accepted checkpoint, never local uncommitted files.
set -euo pipefail
umask 022
if [[ $# -ne 1 || ( $1 != --check && $1 != --install ) ]]; then
  printf 'Usage: %s --check|--install\n' "$0" >&2; exit 2
fi
mode=$1
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
checkpoint=92fec95a5f1057cbe74066e671f855d5daad6856
export GIT_NO_REPLACE_OBJECTS=1
[[ $(git -c safe.directory="$root" -C "$root" rev-parse "$checkpoint^{commit}") == "$checkpoint" ]] || { printf 'Accepted checkpoint unavailable\n' >&2; exit 1; }
[[ ! -e /opt/tempest/backend/app && ! -L /opt/tempest/backend/app ]] || { printf 'Agent source target already exists\n' >&2; exit 1; }
printf 'Source: accepted commit %s, backend/app only.\n' "$checkpoint"
if [[ $mode == --check ]]; then exit 0; fi
[[ $EUID == 0 ]] || { printf '%s\n' '--install requires root' >&2; exit 1; }
[[ -d /opt/tempest/backend && ! -L /opt/tempest/backend && $(stat -c %u /opt/tempest/backend) == 0 && $(stat -c %a /opt/tempest/backend) == 755 ]] || { printf 'Protected backend directory required\n' >&2; exit 1; }
[[ -x /opt/tempest/backend/.venv/bin/python ]] || { printf 'Prepared Agent Python environment required\n' >&2; exit 1; }
staging=$(mktemp -d -p /opt/tempest/backend .agent-prep-XXXXXXXX)
cleanup() { rm -rf -- "$staging"; }
trap cleanup EXIT
git -c safe.directory="$root" -C "$root" archive "$checkpoint" backend/app | tar -x -C "$staging" --strip-components=1 --no-same-owner
[[ -f $staging/app/network_agent/xray_agent.py && -f $staging/app/working_core.py ]] || { printf 'Incomplete source archive\n' >&2; exit 1; }
chown -R root:tempest-control -- "$staging/app"
find "$staging/app" -type d -exec chmod 0750 {} +
find "$staging/app" -type f -exec chmod 0640 {} +
mv -T -n -- "$staging/app" /opt/tempest/backend/app
[[ ! -e $staging/app && -d /opt/tempest/backend/app ]] || { printf 'Source target collision\n' >&2; exit 1; }
printf 'Agent source installed; no services, users or network were configured.\n'
