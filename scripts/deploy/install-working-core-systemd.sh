#!/usr/bin/env bash
# Install and enable the Working Core boot units; never start services here.
set -euo pipefail
umask 077

usage() {
  printf 'Usage: %s --check|--install --deploy-dir ABSOLUTE_DIR\n' "$0" >&2
}

mode=''
deploy_dir=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check|--install)
      [[ -z $mode ]] || { usage; exit 2; }
      mode=$1; shift ;;
    --deploy-dir)
      [[ $# -ge 2 && -z $deploy_dir ]] || { usage; exit 2; }
      deploy_dir=$2; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done
[[ -n $mode && -n $deploy_dir ]] || { usage; exit 2; }
[[ $deploy_dir == /* && $deploy_dir != / && $deploy_dir != *[!A-Za-z0-9._/-]* ]] || {
  printf 'Deployment directory must be an absolute path without whitespace or shell metacharacters.\n' >&2
  exit 1
}
case "/$deploy_dir/" in */../*) printf 'Parent path components are not allowed.\n' >&2; exit 1 ;; esac
[[ -d $deploy_dir && ! -L $deploy_dir ]] || { printf 'Deployment directory must exist and must not be a symlink.\n' >&2; exit 1; }
[[ $(realpath -e -- "$deploy_dir") == "$deploy_dir" ]] || { printf 'Deployment path must be canonical (no symlink components).\n' >&2; exit 1; }
for file in \
  "$deploy_dir/.env" \
  "$deploy_dir/docker-compose.working-core.yml" \
  "$deploy_dir/deploy/working-core/vortex.compose.yml"; do
  [[ -f $file && ! -L $file ]] || { printf 'Required regular deployment file is missing: %s\n' "${file##*/}" >&2; exit 1; }
done

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
sources=(
  "$root/deploy/working-core/tempest-wc-network.service"
  "$root/deploy/systemd/tempest-xray-agent.service"
  "$root/deploy/working-core/tempest-wc-panel.service"
)
targets=(
  /etc/systemd/system/tempest-wc-network.service
  /etc/systemd/system/tempest-xray-agent.service
  /etc/systemd/system/tempest-wc-panel.service
)
helper_source="$root/scripts/deploy/wait-agent-socket.py"
helper_target=/opt/tempest/bin/wait-agent-socket.py
env_target=/etc/tempest/working-core-panel.env

for source in "${sources[@]}" "$helper_source"; do
  [[ -f $source && ! -L $source ]] || { printf 'Installer source is missing or unsafe: %s\n' "${source##*/}" >&2; exit 1; }
done

if [[ $mode == --check ]]; then
  printf 'Read-only preflight passed for %s; panel startup will wait for a connectable Agent socket.\n' "$deploy_dir"
  printf 'No services, containers, systemd files, or network state were changed.\n'
  exit 0
fi

[[ $EUID == 0 ]] || { printf '%s\n' '--install requires root' >&2; exit 1; }
[[ $(stat -c %u -- "$deploy_dir") == 0 ]] || { printf 'Deployment directory must be root-owned.\n' >&2; exit 1; }
for directory in /etc/systemd/system /etc/tempest /opt/tempest/bin; do
  [[ -d $directory && ! -L $directory && $(stat -c %u -- "$directory") == 0 ]] || {
    printf 'Expected root-owned real directory: %s\n' "$directory" >&2
    exit 1
  }
done
for file in "$deploy_dir/docker-compose.working-core.yml" "$deploy_dir/deploy/working-core/vortex.compose.yml"; do
  [[ $(stat -c %u -- "$file") == 0 ]] || { printf 'Compose manifests must be root-owned.\n' >&2; exit 1; }
  mode=$(stat -c %a -- "$file")
  (( (8#$mode & 0022) == 0 )) || { printf 'Compose manifests must not be group/world writable.\n' >&2; exit 1; }
done
[[ $(stat -c %u -- "$deploy_dir/.env") == 0 ]] || { printf 'Compose environment file must be root-owned.\n' >&2; exit 1; }
env_mode=$(stat -c %a -- "$deploy_dir/.env")
(( (8#$env_mode & 0077) == 0 )) || { printf 'Compose environment file must be private to root.\n' >&2; exit 1; }

expected_env=$(mktemp)
trap 'rm -f -- "$expected_env"' EXIT
printf 'TEMPEST_DEPLOY_DIR=%s\n' "$deploy_dir" >"$expected_env"

for index in "${!sources[@]}"; do
  target=${targets[index]}
  if [[ -e $target || -L $target ]]; then
    [[ -f $target && ! -L $target ]] && cmp -s -- "${sources[index]}" "$target" || {
      printf 'Refusing to replace a different or unsafe unit: %s\n' "${target##*/}" >&2
      exit 1
    }
  fi
done
if [[ -e $helper_target || -L $helper_target ]]; then
  [[ -f $helper_target && ! -L $helper_target ]] && cmp -s -- "$helper_source" "$helper_target" || {
    printf 'Refusing to replace a different or unsafe Agent readiness helper.\n' >&2
    exit 1
  }
fi
if [[ -e $env_target || -L $env_target ]]; then
  [[ -f $env_target && ! -L $env_target ]] && cmp -s -- "$expected_env" "$env_target" || {
    printf 'Refusing to replace existing Working Core panel environment configuration.\n' >&2
    exit 1
  }
fi

install_new_file() {
  local source=$1 target=$2 mode=$3 temp
  if [[ -e $target ]]; then return; fi
  temp=$(mktemp "${target%/*}/.tempest-install.XXXXXXXX")
  if [[ $source == @deploy-dir@ ]]; then
    printf 'TEMPEST_DEPLOY_DIR=%s\n' "$deploy_dir" >"$temp"
  else
    cat -- "$source" >"$temp"
  fi
  chown root:root -- "$temp"
  chmod "$mode" -- "$temp"
  mv -nT -- "$temp" "$target"
  if [[ -e $temp ]]; then rm -f -- "$temp"; fi
  [[ -f $target ]] || { printf 'Could not install %s\n' "${target##*/}" >&2; return 1; }
}

for index in "${!sources[@]}"; do
  install_new_file "${sources[index]}" "${targets[index]}" 0644
done
install_new_file "$helper_source" "$helper_target" 0755
install_new_file @deploy-dir@ "$env_target" 0644
systemctl daemon-reload
systemctl enable tempest-wc-network.service tempest-xray-agent.service tempest-wc-panel.service
printf 'Working Core units installed and enabled. They were not started; no Docker or network commands were run.\n'
