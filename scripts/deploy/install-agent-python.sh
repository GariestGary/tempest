#!/usr/bin/env bash
# Initial Python/venv only. Does not install app code, units or networking.
set -euo pipefail
umask 022
if [[ $# -ne 3 || ( $1 != --check && $1 != --install ) ]]; then
  printf 'Usage: %s --check|--install PYTHON_ARCHIVE WHEELHOUSE\n' "$0" >&2
  exit 2
fi
mode=$1; archive=$2; wheels=$3
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
[[ -f $archive && ! -L $archive ]] || { printf 'Python archive must be a regular file\n' >&2; exit 1; }
[[ -d $wheels && ! -L $wheels ]] || { printf 'Wheelhouse must be a directory\n' >&2; exit 1; }
printf '%s  %s\n' d873f435e58d396a18858d4f52a335f1abe8cea385cb9d411ec511f958249d54 "$archive" | sha256sum -c -
[[ -z $(find "$wheels" -maxdepth 1 -type l -print -quit) ]] || { printf 'Wheelhouse contains symlinks\n' >&2; exit 1; }
(cd "$wheels" && sha256sum -c "$root/deploy/working-core/agent-wheels-linux-amd64.sha256" >/dev/null)
[[ ! -e /opt/tempest/python311 && ! -L /opt/tempest/python311 && ! -e /opt/tempest/backend && ! -L /opt/tempest/backend ]] || { printf 'Refusing existing Python/backend paths\n' >&2; exit 1; }
printf 'Verified archive and 60 wheels; target /opt/tempest/python311 and /opt/tempest/backend/.venv.\n'
if [[ $mode == --check ]]; then exit 0; fi
[[ $EUID == 0 ]] || { printf '%s\n' '--install requires root' >&2; exit 1; }
[[ -d /opt/tempest && ! -L /opt/tempest && $(stat -c %u /opt/tempest) == 0 && $(stat -c %a /opt/tempest) == 755 ]] || { printf 'Protected /opt/tempest required\n' >&2; exit 1; }
staging=$(mktemp -d -p /opt/tempest .python-prep-XXXXXXXX)
created_python=0
created_backend=0
cleanup() {
  rm -rf -- "$staging"
  # Never remove a path whose creation by this invocation did not succeed.
  if (( created_python )); then rm -rf -- /opt/tempest/python311; fi
  if (( created_backend )); then rm -rf -- /opt/tempest/backend; fi
}
trap cleanup EXIT
printf '%s  %s\n' d873f435e58d396a18858d4f52a335f1abe8cea385cb9d411ec511f958249d54 "$archive" | sha256sum -c - >/dev/null
mkdir -m 0755 -- /opt/tempest/python311
created_python=1
tar --no-same-owner --strip-components=1 -xzf "$archive" -C /opt/tempest/python311
[[ -x /opt/tempest/python311/bin/python3.11 ]] || { printf 'Archive lacks Python 3.11\n' >&2; exit 1; }
mkdir -m 0755 -- /opt/tempest/backend
created_backend=1
/opt/tempest/python311/bin/python3.11 -m venv /opt/tempest/backend/.venv
/opt/tempest/backend/.venv/bin/python -m pip install --no-index --find-links "$wheels" --only-binary=:all: --require-hashes -r "$root/backend/requirements-agent-linux.lock"
/opt/tempest/backend/.venv/bin/python -m pip check
rmdir -- "$staging"
trap - EXIT
printf 'Python and Agent dependencies installed; application and services were not installed or started.\n'
