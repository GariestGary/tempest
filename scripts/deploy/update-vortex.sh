#!/usr/bin/env bash
# Fish-compatible entry point. Privilege escalation happens only after verification.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
exec python3 "$root/scripts/deploy/update_vortex.py" "$@"
