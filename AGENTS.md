# AGENTS.md — TEMPEST / Codex

**Read `docs/PROJECT_SPEC.md` fully before editing code.** That file is the source of truth for product goals, scope, safety rules, architecture, current phase, acceptance gates, and progress tracking.

## Current instructions

- Product name: **TEMPEST** (no version suffix). Fork base: `DaveBugg/PiTun` (`master`, BSD-3-Clause).
- **Phase 1: `ACCEPTED` by the owner on 2026-11-10**, based on successful GitHub Actions [run #5](https://github.com/GariestGary/tempest/actions/runs/42783278332) at commit `38fec1220c250b3b398e9160842e301d92d9d356`. **Current phase: PHASE 2 (`IN_PROGRESS`); tasks 2.1–2.4 are `ACCEPTED`; task 2.5 is `IN_PROGRESS`.** Work only on the privilege-free ownership registry slice of 2.5. Do not start 2.6–2.8 or phase 3, and do not mark phase 2 accepted before Gate 2 and owner review.
- **Git workflow:** `master` is the only working branch. Make changes directly on `master`; do not create feature branches or Pull Requests unless the user asks for them separately.
- Reuse PiTun architecture/tests and preserve upstream history/license. Do not rewrite the backend in .NET.
- **No automatic geo-content unblocking detection.** Geo-specific routing is user-managed via explicit rules. AUTO is limited to transport-level health/failover within approved candidates.
- **No root/host takeover from Web/API.** Do not run privileged PiTun Compose, change DNS/default routes/firewall, use host Docker socket, or modify host-wide networking while auditing.
- **Do not touch the existing TEMPEST 2 or other services on VORTEX** unless the user separately authorizes a tested migration stage.
- Development uses both TORNADO/Windows 11 and TYPHOON/CachyOS Linux; Linux network integration only in an isolated Linux VM/test host, never on either development host or production by default.
- If uncertain about networking, protocol support, privileges, licenses or irreversible choices: inspect code, document alternatives and ask before implementation.
- Preserve an explanation and tests for every nontrivial change; update `docs/PROJECT_SPEC.md` checkboxes and the dated progress log **only for verified work**.

## Execution sequence for each phase

1. Read the current phase and its gate in `docs/PROJECT_SPEC.md`.
2. Inspect affected upstream files and tests. Produce a concise plan.
3. Implement small, reversible changes. Never edit outside scope solely for tidiness.
4. Run relevant backend/frontend tests. Use an isolated Linux VM for real nftables/TUN/WG tests.
5. Report changed files, commands/tests, pass/fail, risks, unverified scenarios, and commit SHA.
6. Stop and ask for approval before moving to the next phase.

## Local development checks

- **TARGETED LOCAL FEEDBACK POLICY:** after each code change, run only tests covering changed modules or behavior. Targeted pytest uses `-x` so the first failure returns quickly; do not repeat the full backend suite for small edits.
- Full backend pytest is optional, at most once before task completion, with a hard 15-minute limit. If it reaches that limit, interrupt it and record `INCOMPLETE`, never as a pass; rely on GitHub Linux CI for the complete backend suite. Default `full` local mode means targeted backend + frontend + static checks.
- Reuse `backend/.venv` (managed with `uv`) and `frontend/node_modules`; do not reinstall dependencies on every iteration. Match Python 3.11 and Node.js 20 with CI.
- Detect the current OS. On Windows use `scripts/local-check.ps1`; on Linux use `scripts/local-check.sh`. Never use a Windows venv in Linux or a Linux venv in Windows.
- Report Linux-only AF_UNIX/SO_PEERCRED/systemd checks that cannot run on this OS as `NOT_RUN_ON_WINDOWS` (or the corresponding OS); never count them as passed. `full` must print every platform-specific exclusion.
- GitHub Actions provides the complete Linux compatibility check after an authorized push. It is not an intermediate edit/test loop and should not be triggered after each local edit. A published commit with failed CI blocks further publication until the cause is understood and fixed.
- GitHub's Linux runner remains responsible for Linux-only checks excluded on Windows; keep those CI checks enabled.
- Keep platform skips at individual Linux-dependent tests, never module-wide where a file also contains portable tests. Report unavailable AF_UNIX/SO_PEERCRED/systemd checks as `NOT_RUN_ON_WINDOWS` (or the corresponding OS), never as passed; all Linux-only tests must remain active in Linux CI.
- Do not disable checks to make the workflow faster. Local checks never install dependencies, require `sudo`, start containers, or change system settings. Ordinary development does not require Docker Engine or privileged containers.
- Docker runtime smoke and real network integration tests may run only in a pre-approved isolated Linux VM/test host. Do not run privileged Network Agent or network integration tests on TYPHOON.
- Stop and consult the owner before risky system changes, privileged operations, system service changes, or changes outside the workspace.

## Accepted phase 0/1 baselines

The baseline SHA, host-operation audit, feature gap matrix, and proposed ADR-001 are recorded in `docs/`. Phase 0 and phase 1 are accepted. Gate 1 evidence is recorded in `docs/STATUS.md` and the Actions run above. Tasks 2.1–2.3 were accepted after owner review and successful CI as recorded in `docs/PROJECT_SPEC.md`. The owner accepted task 2.4 after review of `6cbb73b22146ccba1ad123183e4e147dc8f59f2b` and successful CI #26. Task 2.5 is authorized only for a pure ownership registry and mock backend; do **not** start 2.6–2.8, implement real nftables/WireGuard apply, deploy on VORTEX, or move beyond this 2.5 slice without separate user confirmation. ADR-001 remains `PROPOSED` until Linux architecture experiments are reviewed.
