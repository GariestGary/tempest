#!/usr/bin/env bash
# Export only committed public source; never copy a working tree or secrets.
set -euo pipefail
[[ $# == 2 && $2 == /* ]] || { echo 'Usage: prepare-build-context.sh COMMIT ABSOLUTE_NEW_DIRECTORY' >&2; exit 2; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
revision=$(git -C "$root" rev-parse --verify "$1^{commit}")
target=$2
[[ ! -e $target && ! -L $target ]] || { echo 'Target already exists' >&2; exit 1; }
umask 022
mkdir -m 0700 -- "$target"
cleanup() { rm -rf -- "$target"; }
trap cleanup ERR
git -C "$root" archive "$revision" backend frontend | tar -x -C "$target" --no-same-owner
# Git archives preserve only executable vs non-executable modes. The export
# excludes untracked files and its private parent is not part of either context.
find "$target/backend" "$target/frontend" -type d -exec chmod 0755 {} +
find "$target/backend" "$target/frontend" -type f ! -perm /111 -exec chmod 0644 {} +
find "$target/backend" "$target/frontend" -type f -perm /111 -exec chmod 0755 {} +
chmod 0755 -- "$target/backend/entrypoint.sh"
trap - ERR
printf 'Public build contexts exported from %s to %s\n' "$revision" "$target"
