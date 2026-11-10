#!/usr/bin/env bash
# Reproducible runtime/security smoke for the safe Compose profile.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.dev.safe.yml"
RUN_TAG="$(date -u +%Y%m%d%H%M%S)-$$"
export COMPOSE_PROJECT_NAME="tempest-smoke-$RUN_TAG"
export TEMPEST_DEV_DB_VOLUME="tempest-smoke-db-$RUN_TAG"
export TEMPEST_DEV_API_PORT="${TEMPEST_DEV_API_PORT:-18000}"
export TEMPEST_DEV_UI_PORT="${TEMPEST_DEV_UI_PORT:-13000}"
API="http://127.0.0.1:$TEMPEST_DEV_API_PORT"
UI="http://127.0.0.1:$TEMPEST_DEV_UI_PORT"
REPORT_DIR="${TEMPEST_SMOKE_REPORT_DIR:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/tempest-safe-compose-smoke-$RUN_TAG}"
REPORT_SALT="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
WORK_DIR=""
API_TOKEN=""
NODE_ID=""
mkdir -p "$REPORT_DIR"
chmod 700 "$REPORT_DIR"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tempest-smoke.XXXXXX")"

compose() {
  docker compose --project-name "$COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

snapshot_network() {
  local label="$1"
  local destination="$WORK_DIR/$label"
  mkdir -p "$destination"
  ip -brief address > "$destination/addresses.txt"
  ip route show table all > "$destination/routes-v4.txt"
  ip -6 route show table all > "$destination/routes-v6.txt"
  ip rule show > "$destination/rules-v4.txt"
  ip -6 rule show > "$destination/rules-v6.txt"
  nft list ruleset > "$destination/nftables.txt"
  if [[ -r /etc/resolv.conf ]]; then
    sha256sum /etc/resolv.conf > "$destination/resolver.sha256"
  else
    printf 'unavailable\n' > "$destination/resolver.sha256"
  fi
}

show_network_diff() {
  local left="$1"
  local right="$2"
  printf '\n--- Network state: %s -> %s ---\n' "$left" "$right"
  for file in "$WORK_DIR/$left"/*.txt "$WORK_DIR/$left"/*.sha256; do
    [[ -e "$file" ]] || continue
    local name="${file##*/}"
    diff -u --label "$left/$name" --label "$right/$name" \
      "$WORK_DIR/$left/$name" "$WORK_DIR/$right/$name" || true
  done
}

sanitize_report_stream() {
  TEMPEST_REPORT_SALT="$REPORT_SALT" python3 -c '
import hashlib
import ipaddress
import os
import re
import sys

salt = os.environ["TEMPEST_REPORT_SALT"].encode()
text = sys.stdin.read()
patterns = [
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/-]+=*"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\"?(?:access[_-]?token|refresh[_-]?token|password|passwd|secret|api[_-]?key)\"?\s*[:=]\s*)(\"?)[^,\s;\"]+"), r"\1[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[REDACTED_JWT]"),
    (re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+@"), r"\1[REDACTED]@"),
]
for pattern, replacement in patterns:
    text = pattern.sub(replacement, text)

def hashed_ip(match):
    value = match.group(0).strip("[](),;{}")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return match.group(0)
    token = hashlib.sha256(salt + value.encode()).hexdigest()[:12]
    return match.group(0).replace(value, f"<IP:{token}>")

text = re.sub(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])", hashed_ip, text)
text = re.sub(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])", hashed_ip, text)
sys.stdout.write(text)
'
}

capture_safe_diagnostics() {
  local diagnostics="$REPORT_DIR/diagnostics"
  mkdir -p "$diagnostics"
  printf '%s\n' \
    'Only bounded service logs and allowlisted container/network metadata are included.' \
    'Credentials and IP addresses are redacted; login responses and raw environment are excluded.' \
    > "$diagnostics/README.txt"

  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    compose ps --all 2>&1 | sanitize_report_stream > "$diagnostics/compose-ps.txt" || true
    compose logs --no-color --timestamps --tail=300 2>&1 \
      | sanitize_report_stream > "$diagnostics/compose-logs.redacted.txt" || true
    for service in api ui; do
      local container_id
      container_id="$(compose ps -aq "$service" 2>/dev/null | head -n 1)"
      [[ -n "$container_id" ]] || continue
      if docker inspect "$container_id" > "$WORK_DIR/$service.inspect.raw.json" 2>/dev/null; then
        python3 - "$WORK_DIR/$service.inspect.raw.json" "$diagnostics/$service-inspect-summary.json" <<'PY'
import json
import sys

source_path, output_path = sys.argv[1:]
with open(source_path, encoding="utf-8") as source:
    container = json.load(source)[0]
host = container.get("HostConfig") or {}
state = container.get("State") or {}
config = container.get("Config") or {}
summary = {
    "service": container.get("Name", "").lstrip("/"),
    "image": container.get("Config", {}).get("Image"),
    "state": state.get("Status"),
    "health": (state.get("Health") or {}).get("Status"),
    "user": config.get("User"),
    "privileged": host.get("Privileged"),
    "network_mode": host.get("NetworkMode"),
    "pid_mode": host.get("PidMode"),
    "cap_add": host.get("CapAdd"),
    "cap_drop": host.get("CapDrop"),
    "read_only_rootfs": host.get("ReadonlyRootfs"),
    "security_options": host.get("SecurityOpt"),
    "devices_present": bool(host.get("Devices")),
    "ports": {
        target: [
            {"host_ip": item.get("HostIp"), "host_port": item.get("HostPort")}
            for item in bindings or []
        ]
        for target, bindings in (host.get("PortBindings") or {}).items()
    },
    "mounts": [
        {"type": mount.get("Type"), "target": mount.get("Destination"), "read_write": mount.get("RW")}
        for mount in container.get("Mounts") or []
    ],
}
with open(output_path, "w", encoding="utf-8") as output:
    json.dump(summary, output, indent=2)
    output.write("\n")
PY
        sanitize_report_stream < "$diagnostics/$service-inspect-summary.json" \
          > "$diagnostics/$service-inspect-summary.redacted.json"
        rm -f -- "$diagnostics/$service-inspect-summary.json"
      fi
    done
  else
    printf 'Docker daemon unavailable while collecting diagnostics.\n' \
      > "$diagnostics/compose-unavailable.txt"
  fi
}

write_network_report() {
  if [[ ! -d "$WORK_DIR/before" ]]; then
    printf 'Network baseline was not captured.\n' > "$REPORT_DIR/network-diff.redacted.txt"
    return
  fi

  if [[ -d "$WORK_DIR/after-up" ]]; then
    {
      printf '%s\n' 'Interpretation: before -> after-up includes expected Docker-managed bridge/NAT/firewall changes.'
      printf '%s\n' 'Interpretation: after-up -> after-teardown should remove Compose-managed state; investigate residual changes.'
      printf '%s\n' 'Any network configuration change caused by TEMPEST itself is a defect.'
      show_network_diff before after-up
      if [[ -d "$WORK_DIR/after-teardown" ]]; then
        show_network_diff after-up after-teardown
      else
        printf 'Post-teardown network snapshot unavailable.\n'
      fi
    } | sanitize_report_stream > "$REPORT_DIR/network-diff.redacted.txt"
  elif [[ -d "$WORK_DIR/after-teardown" ]]; then
    {
      printf '%s\n' 'Smoke failed before Compose became healthy; this compares the baseline to the state after cleanup.'
      show_network_diff before after-teardown
    } | sanitize_report_stream > "$REPORT_DIR/network-diff.redacted.txt"
  else
    printf 'Post-run network state was not captured.\n' > "$REPORT_DIR/network-diff.redacted.txt"
  fi
}

record_failure() {
  local code="$1"
  local line="$2"
  if [[ ! -f "$REPORT_DIR/failure.txt" ]]; then
    printf 'exit_code=%s\nline=%s\n' "$code" "$line" > "$REPORT_DIR/failure.txt"
  fi
}

finish() {
  local result=$?
  trap - EXIT ERR INT TERM HUP
  set +e
  if [[ $result -ne 0 && ! -f "$REPORT_DIR/failure.txt" ]]; then
    record_failure "$result" 0
  fi

  capture_safe_diagnostics
  local diagnostics_result=$?

  compose down --rmi local --volumes --remove-orphans \
    > "$WORK_DIR/teardown.raw.log" 2>&1
  local teardown_result=$?
  if [[ -f "$WORK_DIR/teardown.raw.log" ]]; then
    sanitize_report_stream < "$WORK_DIR/teardown.raw.log" > "$REPORT_DIR/teardown.redacted.log"
  fi

  if [[ -d "$WORK_DIR/before" ]]; then
    snapshot_network after-teardown > "$WORK_DIR/post-teardown-snapshot.log" 2>&1
    if [[ $? -ne 0 ]]; then
      teardown_result=1
      printf 'Could not capture network state after teardown.\n' > "$REPORT_DIR/network-snapshot-error.txt"
    fi
  fi

  write_network_report
  if [[ $result -eq 0 && $teardown_result -ne 0 ]]; then result=$teardown_result; fi
  if [[ $result -eq 0 && $diagnostics_result -ne 0 ]]; then result=$diagnostics_result; fi
  if [[ $result -eq 0 && $EUID -eq 0 && -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
    chown -R "$SUDO_UID:$SUDO_GID" "$REPORT_DIR" || result=$?
  fi
  if [[ $result -ne 0 && ! -f "$REPORT_DIR/failure.txt" ]]; then
    record_failure "$result" 0
  fi
  {
    printf 'result=%s\n' "$result"
    printf 'teardown_result=%s\n' "$teardown_result"
    printf 'diagnostics_result=%s\n' "$diagnostics_result"
    printf 'compose_project=%s\n' "$COMPOSE_PROJECT_NAME"
    printf 'api_loopback_port=%s\n' "$TEMPEST_DEV_API_PORT"
    printf 'ui_loopback_port=%s\n' "$TEMPEST_DEV_UI_PORT"
    printf 'api_token_saved=false\n'
    printf 'database_volume=%s\n' "$TEMPEST_DEV_DB_VOLUME"
    printf '%s\n' 'Cleanup command: docker compose down --rmi local --volumes --remove-orphans'
  } > "$REPORT_DIR/summary.txt"

  if [[ -f "$REPORT_DIR/failure.txt" ]]; then
    printf 'Smoke failed. Safe diagnostics were preserved at: %s\n' "$REPORT_DIR" >&2
  else
    printf 'Safe diagnostics were preserved at: %s\n' "$REPORT_DIR"
  fi

  if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
    rm -rf -- "$WORK_DIR"
  fi

  exit "$result"
}

trap 'record_failure "$?" "$LINENO"' ERR
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

for tool in docker curl python3 ip nft diff; do
  command -v "$tool" >/dev/null || {
    printf 'Required command not found: %s\n' "$tool" >&2
    exit 2
  }
done
docker compose version >/dev/null
docker info >/dev/null
ip -brief address >/dev/null
nft list ruleset >/dev/null || {
  printf 'Cannot read nftables state. Run this audit with read access to the VM ruleset.\n' >&2
  exit 2
}

printf 'Compose project: %s\n' "$COMPOSE_PROJECT_NAME"
printf 'Temporary DB volume: %s\n' "$TEMPEST_DEV_DB_VOLUME"
printf 'Loopback ports: API=%s UI=%s\n' "$TEMPEST_DEV_API_PORT" "$TEMPEST_DEV_UI_PORT"
snapshot_network before

compose build
compose up -d

wait_healthy() {
  local service="$1"
  local container_id
  container_id="$(compose ps -q "$service")"
  [[ -n "$container_id" ]] || {
    printf 'Compose did not create the %s container.\n' "$service" >&2
    return 1
  }
  for _ in $(seq 1 60); do
    local status
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_id")"
    [[ "$status" == healthy ]] && return 0
    if [[ "$status" == unhealthy || "$status" == missing ]]; then
      docker inspect --format '{{json .State.Health}}' "$container_id" >&2
      return 1
    fi
    sleep 2
  done
  printf 'Timed out waiting for %s healthcheck.\n' "$service" >&2
  docker inspect --format '{{json .State.Health}}' "$container_id" >&2
  return 1
}

wait_healthy api
wait_healthy ui
curl --fail --silent --show-error "$API/health" > "$WORK_DIR/health.json"
python3 - "$WORK_DIR/health.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    health = json.load(source)
assert health.get("dev_safe_mode") is True, health
assert health.get("dataplane") == "mock-noop", health
assert health.get("vpn_traffic_available") is False, health
PY
curl --fail --silent --show-error "$UI/" | grep -Fq '<title>TEMPEST</title>'

for service in api ui; do
  container_id="$(compose ps -q "$service")"
  docker inspect "$container_id" > "$WORK_DIR/$service.inspect.json"
  python3 - "$WORK_DIR/$service.inspect.json" "$service" <<'PY'
import json
import sys

path, service = sys.argv[1:]
with open(path, encoding="utf-8") as source:
    container = json.load(source)[0]
host = container["HostConfig"]
config = container["Config"]
assert host.get("Privileged") is False, service
assert host.get("NetworkMode") != "host", (service, host.get("NetworkMode"))
assert host.get("PidMode") != "host", (service, host.get("PidMode"))
assert not host.get("CapAdd"), (service, host.get("CapAdd"))
assert "ALL" in (host.get("CapDrop") or []), (service, host.get("CapDrop"))
assert host.get("ReadonlyRootfs") is True, service
assert "no-new-privileges:true" in (host.get("SecurityOpt") or []), service
assert not host.get("Devices"), (service, host.get("Devices"))
user = (config.get("User") or "").split(":", 1)[0]
assert user not in {"", "0", "root"}, (service, config.get("User"))

allowed_mounts = {
    "api": {"/app/data", "/app/app", "/app/alembic", "/app/alembic.ini", "/tmp"},
    "ui": {"/app/src", "/tmp"},
}[service]
mounts = container.get("Mounts") or []
destinations = {mount["Destination"] for mount in mounts}
assert destinations <= allowed_mounts, (service, destinations)
assert "/tmp" in destinations, (service, destinations)
if service == "api":
    assert "/app/data" in destinations, destinations
for mount in mounts:
    destination = mount["Destination"]
    source = mount.get("Source", "")
    assert "docker.sock" not in destination and "docker.sock" not in source, mount
    if destination == "/tmp":
        assert mount.get("Type") == "tmpfs", mount
    elif destination == "/app/data":
        assert mount.get("Type") == "volume" and mount.get("RW") is True, mount
    else:
        assert mount.get("Type") == "bind" and mount.get("RW") is False, mount

expected_port = "8000/tcp" if service == "api" else "5173/tcp"
bindings = host.get("PortBindings") or {}
assert set(bindings) == {expected_port}, (service, bindings)
for binding in bindings[expected_port] or []:
    assert binding.get("HostIp") == "127.0.0.1", (service, binding)
print(f"{service}: runtime namespace, capability, mount, and port checks passed")
PY
done

snapshot_network after-up

SMOKE_USERNAME="${TEMPEST_SMOKE_USERNAME:-admin}"
SMOKE_PASSWORD="${TEMPEST_SMOKE_PASSWORD:-password}"
LOGIN_PAYLOAD="$(SMOKE_USERNAME="$SMOKE_USERNAME" SMOKE_PASSWORD="$SMOKE_PASSWORD" \
  python3 -c 'import json,os; print(json.dumps({"username":os.environ["SMOKE_USERNAME"],"password":os.environ["SMOKE_PASSWORD"]}))')"
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -d "$LOGIN_PAYLOAD" \
  "$API/api/auth/login" > "$WORK_DIR/login.json"
API_TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["access_token"])' "$WORK_DIR/login.json")"
[[ -n "$API_TOKEN" ]]

curl --fail --silent --show-error \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"TEMPEST VM smoke node","protocol":"naive","address":"example.invalid","port":443}' \
  "$API/api/nodes" > "$WORK_DIR/node-created.json"
NODE_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["id"])' "$WORK_DIR/node-created.json")"
curl --fail --silent --show-error -H "Authorization: Bearer $API_TOKEN" \
  "$API/api/nodes/$NODE_ID" > "$WORK_DIR/node-read.json"
python3 - "$WORK_DIR/node-read.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as source:
    node = json.load(source)
assert node["name"] == "TEMPEST VM smoke node", node
PY
curl --fail --silent --show-error -X PATCH \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"TEMPEST VM smoke node updated"}' \
  "$API/api/nodes/$NODE_ID" > "$WORK_DIR/node-updated.json"
python3 - "$WORK_DIR/node-updated.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as source:
    node = json.load(source)
assert node["name"] == "TEMPEST VM smoke node updated", node
PY

expect_locked() {
  local method="$1"
  local path="$2"
  local status
  local args=(--silent --show-error -o "$WORK_DIR/denied.json" -w '%{http_code}'
    -X "$method" -H "Authorization: Bearer $API_TOKEN")
  if [[ "$method" != GET && "$method" != HEAD ]]; then
    args+=(-H 'Content-Type: application/json' -d '{}')
  fi
  status="$(curl "${args[@]}" "$API$path")"
  [[ "$status" == 423 ]] || {
    printf 'Expected safe-mode denial 423 for %s %s, got %s\n' "$method" "$path" "$status" >&2
    cat "$WORK_DIR/denied.json" >&2
    return 1
  }
  python3 - "$WORK_DIR/denied.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as source:
    body = json.load(source)
assert body.get("code") == "DEV_SAFE_MODE_NETWORK_OPERATION_DISABLED", body
PY
}

expect_locked POST /api/system/start
expect_locked POST /api/system/restart
expect_locked POST /api/network/apply
expect_locked POST /api/system/backup/restore
expect_locked POST /api/subscriptions/1/refresh
expect_locked POST /api/nodes/1/check
expect_locked GET /api/diagnostics/network

compose restart
wait_healthy api
wait_healthy ui
curl --fail --silent --show-error -H "Authorization: Bearer $API_TOKEN" \
  "$API/api/nodes/$NODE_ID" > "$WORK_DIR/node-after-restart.json"
python3 - "$WORK_DIR/node-after-restart.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as source:
    node = json.load(source)
assert node["name"] == "TEMPEST VM smoke node updated", node
PY
curl --fail --silent --show-error -X DELETE \
  -H "Authorization: Bearer $API_TOKEN" "$API/api/nodes/$NODE_ID" >/dev/null
NODE_ID=""

printf '\nSmoke passed: build, up, health, UI, login, DB-only CRUD, deny boundary, restart, and teardown.\n'
printf 'Network snapshots are diffed in the EXIT report. Docker-managed bridge/NAT/firewall deltas are expected; any change attributable to TEMPEST itself is a defect.\n'
