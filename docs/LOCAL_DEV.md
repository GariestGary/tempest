# Local development checks

Use the local project environments for fast feedback. GitHub Actions remains an independent check after a push to `master`; do not wait for CI between local edits. The local check script never installs packages, uses `sudo`, starts Compose, or applies network settings.

## CachyOS setup

Check tools before installing anything:

```bash
uname -a
git --version
uv --version
python3.11 --version
node --version
npm --version
docker compose version  # optional; required only for Compose config validation
```

For a new setup, install required operating-system tools with the CachyOS package manager after reviewing the package transaction. These package operations may require `sudo` and are intentionally not run by the project scripts:

```bash
sudo pacman -S --needed git base-devel openssl uv nodejs npm
```

Use Python 3.11 for parity with GitHub Actions. `uv python install 3.11` installs a user-managed interpreter without system-wide pip packages.

```bash
uv python install 3.11
uv venv --python 3.11 backend/.venv
uv pip install --python backend/.venv/bin/python \
  -r backend/requirements.txt -r backend/requirements-dev.txt
(cd frontend && npm ci --legacy-peer-deps --no-audit --no-fund)
```

Do not run `pip install` against the system Python. Keep `backend/.venv` and `frontend/node_modules` between iterations; recreate them only when missing, damaged, or when the lockfiles change. `npm ci` uses the checked-in `frontend/package-lock.json` and does not rewrite it.

The CI workflow currently uses Node.js 20. Check `node --version` before frontend work. On CachyOS where the system Node is newer, a user-space runtime can be installed without sudo and used by prepending its bin directory to `PATH`:

```bash
npm install --prefix "$HOME/.local/share/tempest-node20" \
  --no-save --no-package-lock --no-audit --no-fund node-linux-x64@20.20.0
PATH="$HOME/.local/share/tempest-node20/node_modules/node-linux-x64/bin:$PATH" \
  scripts/local-check.sh frontend
```

The binary can be checked with `"$HOME/.local/share/tempest-node20/node_modules/node-linux-x64/bin/node" --version`. Keep `package.json` and `package-lock.json` unchanged; use the pinned CI runtime until CI is deliberately upgraded.

## Run checks

From the repository root:

```bash
scripts/local-check.sh quick     # current Agent / NetworkPlan contract tests
scripts/local-check.sh backend   # complete backend pytest suite
scripts/local-check.sh frontend  # Vitest, ESLint, TypeScript/Vite production build
scripts/local-check.sh full      # backend + frontend + shell syntax + git diff --check + optional Compose config
```

The script reports each major check's elapsed seconds and exits nonzero on failure. It emits a clear diagnostic if a project environment is missing. Compose validation is skipped when Docker Compose v2 is unavailable. `full` only renders safe Compose configuration; it does not start containers. Runtime smoke and real nftables/WireGuard/TUN integration belong in a pre-approved isolated Linux VM/test host, never on TYPHOON or VORTEX by default.

Run relevant tests first after each code change. Run `full` before finishing a substantial task or phase. Do not disable checks or reinstall dependencies on every iteration. Push only after local checks pass; if a published commit fails GitHub Actions, stop further publication until the cause is understood and fixed.

## Environment comparison

The current GitHub Actions backend job uses Python 3.11. Its frontend job uses Node.js 20 and runs `npm ci --legacy-peer-deps`, lint, Vitest, and build. Keep local runtimes aligned when practical, while preserving the repository's pinned dependency files.

## TYPHOON audit snapshot (2026-11-11)

- CachyOS Linux, x86_64, kernel `7.3.3-1-cachyos`; Bash 5.3.20.
- Git 2.56.0; Codex CLI 0.167.0-alpha.3.2; uv 0.13.7; project Python 3.11.18. System `python3` is 3.14.9 and system pip is 26.2.2; do not use it for project installs.
- `backend/.venv` existed but lacked pip/dependencies. `uv pip install --python backend/.venv/bin/python -r backend/requirements.txt -r backend/requirements-dev.txt` completed from cache and made pytest 9.0.3 available.
- System Node.js 26.13.0 and npm 12.2.2 are present; CI uses Node.js 20. `frontend/node_modules` was absent. Initial sandboxed `npm ci` hit DNS `EAI_AGAIN`; the approved user-level `npm ci --legacy-peer-deps --no-audit --no-fund` then installed 384 locked packages in 44 s. The lockfile was not changed. Node 26 produced 136 passing and 6 failing Vitest tests due to missing jsdom `localStorage`; the same suite passed on Node 20.20.0. The temporary Node 20 binary used for verification is under `/tmp/tempest-node20`.
- Docker Engine and Docker Compose are absent. Compose config validation was skipped. GCC/cc, make, pkg-config, base-devel, OpenSSL 4.1.0, and glibc 2.44 are present. Bash, Git and Codex are available. No system service, firewall, DNS, route, or VPN settings were modified.
- Results: targeted Network Agent/NetworkPlan pytest: 52 passed in 1.42 s. Full backend `python -m pytest tests/ -q`: 1788 passed, 2 skipped, 32 warnings in 325.03 s. Node 20 `scripts/local-check.sh frontend`: Vitest 142 passed (14.09 s), ESLint pass, TypeScript/Vite build pass (Vite reported the existing >700 KB chunk warning). `bash -n scripts/*.sh` and `git diff --check`: pass. The script's `quick` mode: 52 passed in 1.39 s (reported total command time 3 s). A repeated run through `local-check.sh full` was stopped after 536 s at 1144 passed / 2 skipped because pytest stopped making visible progress; the independent full backend run above had already completed successfully. The aggregate `full` mode therefore was not observed completing end-to-end in this session.
- AF_UNIX tests required the approved test runner to execute outside its default restricted sandbox; this only enabled local test socket operations. No Agent service was started and no host networking was changed.
