#!/usr/bin/env bash
# Download pinned wheels for review; never installs Python packages.
set -euo pipefail
umask 077
if [[ $# -ne 2 || $2 != /* ]]; then
  printf 'Usage: %s PYTHON3.11 ABSOLUTE_NEW_WHEELHOUSE\n' "$0" >&2
  exit 2
fi
python=$1; target=$2
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
[[ $("$python" -c 'import sys; print("%s.%s" % sys.version_info[:2])') == 3.11 ]] || { printf 'Python 3.11 required\n' >&2; exit 1; }
[[ ! -e "$target" && ! -L "$target" ]] || { printf 'Wheelhouse already exists\n' >&2; exit 1; }
mkdir -m 0700 -- "$target"
cleanup() {
  find "$target" -maxdepth 1 -type f -name '*.whl' -delete
  rm -f -- "$target/SHA256SUMS"
  rmdir -- "$target" 2>/dev/null || true
}
trap cleanup ERR
PIP_DISABLE_PIP_VERSION_CHECK=1 "$python" -m pip download --require-hashes --only-binary=:all: \
  --dest "$target" -r "$root/backend/requirements-agent-linux.lock"
(cd "$target" && sha256sum -- ./*.whl > SHA256SUMS && sha256sum -c SHA256SUMS >/dev/null)
trap - ERR
printf 'Pinned wheelhouse prepared; no packages were installed.\n'
