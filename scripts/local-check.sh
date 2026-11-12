#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
MODE="${1:-quick}"

usage() {
  cat <<'EOF'
Usage: scripts/local-check.sh [quick|backend|frontend|full]

  quick     Current Network Agent / NetworkPlan contract tests
  backend   Complete backend pytest suite
  frontend  Vitest, ESLint, and TypeScript/Vite production build
  full      Backend + frontend + shell syntax + diff check + optional Compose config
EOF
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 2
}

run_timed() {
  local label="$1"
  shift
  local start=$SECONDS status
  printf '\n==> %s\n' "$label"
  "$@"
  status=$?
  printf '<== %s: exit %s, %s sec\n' "$label" "$status" "$((SECONDS - start))"
  return "$status"
}

require_backend() {
  [[ -x "$BACKEND_DIR/.venv/bin/python" ]] || fail "backend/.venv is missing; create it with the uv commands in docs/LOCAL_DEV.md"
  "$BACKEND_DIR/.venv/bin/python" -c 'import pytest' >/dev/null 2>&1 || fail "pytest is missing from backend/.venv; install requirements-dev.txt with uv as documented in docs/LOCAL_DEV.md"
}

require_frontend() {
  [[ -x "$FRONTEND_DIR/node_modules/.bin/vitest" ]] || fail "frontend/node_modules is missing or incomplete; run npm ci --legacy-peer-deps in frontend/"
  [[ -x "$FRONTEND_DIR/node_modules/.bin/eslint" ]] || fail "ESLint is missing from frontend/node_modules; run npm ci --legacy-peer-deps in frontend/"
}

run_backend() {
  require_backend
  (cd "$BACKEND_DIR" && run_timed "Backend pytest suite" .venv/bin/python -m pytest tests/ -q)
}

run_quick() {
  require_backend
  (cd "$BACKEND_DIR" && run_timed "Network Agent / NetworkPlan contract tests" \
    .venv/bin/python -m pytest tests/test_network_agent_ipc.py tests/test_network_plan_contract.py tests/test_agent_control_plane.py tests/test_network_plan_builder.py -q)
}

run_frontend() {
  require_frontend
  (cd "$FRONTEND_DIR" && run_timed "Frontend Vitest" npm run test:ci) || return $?
  (cd "$FRONTEND_DIR" && run_timed "Frontend ESLint" npm run lint) || return $?
  (cd "$FRONTEND_DIR" && run_timed "Frontend TypeScript/Vite build" npm run build)
}

run_shell_syntax() {
  local -a files=()
  mapfile -d '' files < <(find "$ROOT_DIR/scripts" -type f -name '*.sh' -print0)
  run_timed "Shell script syntax (${#files[@]} files)" bash -n "${files[@]}"
}

run_compose_config() {
  if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    printf 'SKIP: Docker Compose v2 is unavailable; safe Compose config was not checked.\n'
    return 0
  fi
  run_timed "Safe dev Compose config" docker compose -f "$ROOT_DIR/docker-compose.dev.safe.yml" config --quiet
}

case "$MODE" in
  quick) run_quick ;;
  backend) run_backend ;;
  frontend) run_frontend ;;
  full)
    run_backend || exit $?
    run_frontend || exit $?
    run_shell_syntax || exit $?
    run_timed "Git whitespace/error check" git -C "$ROOT_DIR" diff --check || exit $?
    run_compose_config
    ;;
  -h|--help|help) usage ;;
  *) usage >&2; fail "unknown mode: $MODE" ;;
esac
