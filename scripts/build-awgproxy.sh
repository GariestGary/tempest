#!/usr/bin/env bash
# Build from an already verified checkout and pre-populated Go module cache.
set -euo pipefail
[[ $# == 2 ]] || { printf 'Usage: bash %s AWGPROXY_CHECKOUT OUTPUT\n' "$0" >&2; exit 2; }
source_dir=$(cd "$1" && pwd)
output=$2
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[[ "$output" = /* ]] || { printf 'OUTPUT must be absolute\n' >&2; exit 2; }
[[ $(git -C "$source_dir" rev-parse HEAD) == c87c35e40eddac260941ab02596e53d5eb601eae ]]
[[ -z $(git -C "$source_dir" status --porcelain --untracked-files=no) ]]
[[ $(go version | awk '{print $3}') == go1.27.3 ]]
build_dir=$(mktemp -d)
trap 'rm -rf -- "$build_dir"' EXIT
git -C "$source_dir" archive c87c35e40eddac260941ab02596e53d5eb601eae | tar -x -C "$build_dir"
patch --fuzz=0 -d "$build_dir" -p1 < "$script_dir/awgproxy-no-route-monitor.patch"
(cd "$build_dir" && env GOPROXY=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOAMD64=v1 \
  go build -trimpath -buildvcs=false -o "$output" ./cmd/awgproxy)
printf '%s  %s\n' 'e0fe256ad362221026a15a372a246b21026b0405e4dc32ab8f2c87c92543686e' "$output" | sha256sum -c -
