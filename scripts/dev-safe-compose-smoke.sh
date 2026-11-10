#!/usr/bin/env bash
# Reproducible runtime/security smoke for the isolated Linux VM profile.
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
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tempest-smoke.XXXXXX")"
API_TOKEN=""
NODE_ID=""

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

finish() {
  local result=$?
  trap - EXIT
  set +e
  compose down --rmi local --volumes --remove-orphans
  local teardown_result=$?
  if [[ -d "$WORK_DIR/before" && -d "$WORK_DIR/after-up" ]]; then
    snapshot_network after-teardown
    if [[ $? -ne 0 ]]; then
      teardown_result=1
      printf 'Could not capture network state after teardown.\n' >&2
    else
      show_network_diff before after-up
      show_network_diff after-up after-teardown
    fi
  fi
  rm -rf "$WORK_DIR"
  if [[ $result -eq 0 && $teardown_result -ne 0 ]]; then
    result=$teardown_result
  fi
  exit "$result"
}
trap finish EXIT

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
