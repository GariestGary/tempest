# Local development checks

Use the local project environments for fast feedback. GitHub Actions remains an independent check after a push to `master`; do not wait for CI between local edits. The local check scripts never install packages, use `sudo`, start Compose, or apply network settings. Ordinary development checks do not require Docker Engine.

## Feedback sequence

1. Develop on TORNADO and run targeted Windows pytest for each changed behavior.
2. At a reviewed commit, run selected safe Linux pytest on VORTEX via SSH with `scripts/remote-check.ps1`.
3. After an authorized push, use GitHub CI as the independent complete check.
4. Use a disposable isolated Linux VM only for real nftables, TUN, WireGuard, namespace, and rollback experiments. VORTEX is never a privileged network integration host.

## VORTEX remote Linux runner

VORTEX is a working production/home server. Only unprivileged tests of NetworkPlan contracts, NetworkObjectRegistry, fixture-only Linux inventory, AF_UNIX/SO_PEERCRED, and the Agent mode security boundary are allowed. Those tests were inspected for host network mutation and production-service access. AF_UNIX tests use only temporary local Unix sockets and the mock Agent; Agent mode tests mock or reject host operations. The runner does not start TEMPEST, Docker Compose, a systemd unit, or a dataplane. Do not run the full pytest suite there by default.

The repository is `/home/gary/Docker/tempest` on remote `master`. Its `origin` must be `https://github.com/GariestGary/tempest.git`. First-time setup uses a clone and an isolated `backend/.venv` with Python 3.11. If Python 3.11 or `uv` is absent, install them in `/home/gary/.local/share/tempest-tools/` without `sudo`, global Python packages, or persistent PATH changes. The check script performs no dependency installation. Keep the venv between runs.

The initial setup on VORTEX used these user-level commands after checking the parent directory, disk, RAM, load, and Git. Run them only when the clone or venv is missing; inspect an existing checkout and use `git pull --ff-only` instead of cloning over it:

```bash
git clone --branch master --single-branch https://github.com/GariestGary/tempest.git /home/gary/Docker/tempest
curl -LsSf https://astral.sh/uv/install.sh | \
  UV_INSTALL_DIR=/home/gary/.local/share/tempest-tools/bin UV_NO_MODIFY_PATH=1 sh
UV_PYTHON_INSTALL_DIR=/home/gary/.local/share/tempest-tools/python \
  /home/gary/.local/share/tempest-tools/bin/uv python install 3.11.18
cd /home/gary/Docker/tempest
UV_PYTHON_INSTALL_DIR=/home/gary/.local/share/tempest-tools/python \
  /home/gary/.local/share/tempest-tools/bin/uv venv --python 3.11.18 backend/.venv
nice -n 10 timeout 300 /home/gary/.local/share/tempest-tools/bin/uv pip install \
  --python backend/.venv/bin/python -r backend/requirements.txt -r backend/requirements-dev.txt
```

From TORNADO, pass the **full local `master` HEAD SHA** to the script. The runner requires a clean local tree. Remotely it checks free disk/RAM/CPU load before touching Git, verifies a clean `master` and expected origin, fetches `master`, requires the fetched SHA to match the requested SHA, then fast-forwards to that same fetched commit with Git hooks disabled. It refuses a mismatch; it never resets or deletes repository files. Suites can be combined, but select only tests relevant to the changed modules:

```powershell
$sha = (git rev-parse HEAD).Trim()
pwsh -File scripts/remote-check.ps1 -Commit $sha -Suite Inventory
pwsh -File scripts/remote-check.ps1 -Commit $sha -Suite Plan,Registry,Ipc,Boundary -TimeoutSeconds 180
```

The runner uses a unique `/tmp` directory, `nice -n 10`, a remote `timeout`, and a local SSH deadline. It reports passed/failed/skipped/errors, pytest exit code, tested SHA, elapsed time, and peak RSS. Exit `124` means incomplete timeout, not a pass. Resource or SSH failures stop before Git update. The remote checkout can only test a commit already published to its `origin`; unpublished local edits are not included.

Never change DNS, firewall, routes, IP rules, nftables, namespaces, existing containers or services on VORTEX; never use `sudo` for network operations, start TUN/WireGuard/Xray, or install the Agent as a service. Any resource-heavy run requires separate agreement. Real network tests remain in a disposable isolated Linux VM.

## TORNADO / Windows setup

Use a user-managed Python runtime and the repository venv; do not use WindowsApps Python aliases or system `pip`. If `uv` is missing, install it without changing persistent PATH:

```powershell
$uvDir = Join-Path $env:LOCALAPPDATA 'TempestTools'
$env:UV_INSTALL_DIR = $uvDir
$env:UV_UNMANAGED_INSTALL = $uvDir
$env:UV_NO_MODIFY_PATH = '1'
Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
$uv = Join-Path $uvDir 'uv.exe'
& $uv python install 3.11
& $uv venv --python 3.11 backend/.venv
& $uv pip install --python backend/.venv/Scripts/python.exe `
  -r backend/requirements.txt -r backend/requirements-dev.txt
```

When the system Node version is not 20, install the user-local Node 20 runtime:

```powershell
npm install --prefix "$env:LOCALAPPDATA\TempestNode20" `
  --no-save --no-package-lock --no-audit --no-fund node-win-x64@20.20.0
```

The Windows script detects that runtime and invokes npm through it; persistent PATH is unchanged. Use `quick` for focused Agent/NetworkPlan checks, `backend` for changed backend modules, `frontend` for frontend checks, or `full` for targeted backend + frontend + static checks. Targeted pytest uses `-x`. `backend-full` is optional and may run no more than once per task; it stops after 15 minutes and reports `INCOMPLETE`, never a pass. Prefer Linux CI for the complete backend suite. AF_UNIX/SO_PEERCRED and runtime systemd checks unavailable on Windows are reported as `NOT_RUN_ON_WINDOWS` and remain active in Linux CI. Bash subprocess tests run if a working Bash is available; only those specific tests skip when Bash is absent or unusable. Docker runtime/network integration is not attempted. If Docker Compose v2 is installed, `full` renders Agent Compose configuration without starting services.

## TORNADO audit snapshot (2026-11-12)

- Windows 11; PowerShell 7.6.6; Git 2.55.0; npm 11.9.0. The system Node.js is 24.14.0; user-local Node.js 20.20.0 is installed separately for checks.
- The `python.exe` / `python3.11.exe` WindowsApps aliases and `py -3.11` launcher failed to start. `uv` 0.13.7 installed standalone CPython 3.11.18 in the user profile; `backend/.venv` now uses that interpreter and pytest 9.0.3. No system `pip` was used.
- `frontend/node_modules` already existed. Backend project and dev requirements were installed once into `backend/.venv`; the lockfiles and global PATH were not changed.
- Docker Compose v2.40.3 is available for config rendering; no Docker Engine operation or container start was performed. Compose emitted an access warning for the existing user Docker config, but static rendering succeeded.
- No system PATH, App Execution Alias, Windows service, WSL, Docker runtime, DNS, route, firewall, or VPN setting was changed.
- 2026-11-12 feedback-loop update: targeted pytest `-x` for the changed backend set completed with 68 passed / 71 skipped in 4.83 s. The skips were AF_UNIX/SO_PEERCRED tests that require Linux and Bash subprocess cases because the available WindowsApps `bash.exe` alias did not pass `bash --version`; portable installer/updater source assertions ran. The Ubuntu workflow runs the entire backend test tree, so Linux and Bash cases remain enabled.
- Frontend on Node.js 20.20.0: Vitest 142 passed, ESLint passed, and TypeScript/Vite build passed (the existing >700 KB chunk warning remains). Agent Compose `config --quiet`, PowerShell parser check, and `git diff --check` passed. Bash syntax could not be checked locally on Windows.
- One full backend pytest was started before the 15-minute policy was adopted; after exceeding 15 minutes it was interrupted around 39%. Record as **INCOMPLETE**, not passed or failed. Do not repeat it locally in this task; GitHub Linux CI is responsible for full backend compatibility verification.

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

From the repository root on Linux:

```bash
scripts/local-check.sh quick     # current Agent / NetworkPlan contract tests
scripts/local-check.sh backend   # changed backend modules; stop at first failure
scripts/local-check.sh backend-full # optional full suite, max 15 minutes, once per task
scripts/local-check.sh frontend  # Vitest, ESLint, TypeScript/Vite production build
scripts/local-check.sh full      # targeted backend + frontend + shell syntax + diff + optional Compose config
```

On Windows use the PowerShell counterpart:

```powershell
pwsh -File scripts/local-check.ps1 quick
pwsh -File scripts/local-check.ps1 backend
pwsh -File scripts/local-check.ps1 backend-full # optional, max 15 minutes, once per task
pwsh -File scripts/local-check.ps1 frontend
pwsh -File scripts/local-check.ps1 full
```

Both scripts report check timings and fail on errors. `quick`, `backend`, and `full` use targeted pytest with `-x`; `full` does not run every backend test. `backend-full` is opt-in, limited to 15 minutes, and may run once at most before task completion. A timeout is `INCOMPLETE`; Linux CI runs the complete suite. Platform skips belong only on tests requiring those facilities, and skipped tests are never passes. Windows `full` runs targeted backend/frontend, optional Agent Compose config rendering, and `git diff --check`; Linux `full` also includes shell syntax and safe Compose config rendering. Neither script starts containers or applies network settings. Runtime smoke and real nftables/WireGuard/TUN integration belong in a pre-approved isolated Linux VM/test host, never on TYPHOON or VORTEX by default.

Run tests for changed modules after each code change; use `-x` for fast first-failure feedback. Run `full` before finishing substantial work. Avoid repeat full backend runs; the optional full backend test is capped at 15 minutes and may run once only. Do not disable checks or reinstall dependencies on every iteration. After authorized publication, use GitHub Actions for complete Linux verification; if CI fails, stop further publication until the cause is understood and fixed.

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
