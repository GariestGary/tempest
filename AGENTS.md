# AGENTS.md — TEMPEST / Codex

**Read `docs/PROJECT_SPEC.md` fully before editing code.** That file is the source of truth for product goals, scope, safety rules, architecture, current phase, acceptance gates, and progress tracking.

## Current instructions

- Product name: **TEMPEST** (no version suffix). Fork base: `DaveBugg/PiTun` (`master`, BSD-3-Clause).
- **Phase 1: `ACCEPTED` by the owner on 2026-11-10**, based on successful GitHub Actions [run #5](https://github.com/GariestGary/tempest/actions/runs/42783278332) at commit `38fec1220c250b3b398e9160842e301d92d9d356`. **Current phase: PHASE 2 (`IN_PROGRESS`); tasks 2.1–2.5 are `ACCEPTED`.** The owner accepted task 2.5 on 2026-11-17 based on VM trial `b3bb6399ff33524569c62feb234d8a80a3483667`, documentation `97bc1ca2892d014b26de04261f97be65fb708bbd`, CI #40 and CodeQL #41. Live coverage comprises an empty nft table, an IPv4 rule and a blackhole route. The owner accepted the [first task 2.6 slice](docs/TASK_2_6_FIRST_NFT_TABLE.md) in its verified scope on 2026-11-18. The owner accepted the [userspace A/B](docs/ENGINE_VS_KERNEL_DECISION.md) at `b35c5de` in its verified scope and selected Xray 26.10.31 for the MVP. Task 2.6 is the Xray configuration lifecycle and remains `IN_PROGRESS`, Gate 2 `OPEN`.
- **Git workflow:** `master` is the only working branch. Make changes directly on `master`; do not create feature branches or Pull Requests unless the user asks for them separately.
- Reuse PiTun architecture/tests and preserve upstream history/license. Do not rewrite the backend in .NET. Keep kernel WireGuard, TUN/TPROXY, universal nftables/IP route apply and expansion of NetworkObjectRegistry off the MVP critical path. Preserve the accepted code/tests. sing-box is an alternative outside the current MVP.
- **No automatic geo-content unblocking detection.** Geo-specific routing is user-managed via explicit rules. AUTO is limited to transport-level health/failover within approved candidates.
- **No root/host takeover from Web/API.** Do not run privileged PiTun Compose, change DNS/default routes/firewall, use host Docker socket, or modify host-wide networking while auditing.
- **Do not touch the existing TEMPEST 2 or other services on VORTEX** unless the user separately authorizes a tested migration stage.
- TORNADO/Windows 11 is the primary development host. TYPHOON/CachyOS remains a development host. VORTEX is authorized only as a remote, unprivileged Linux pytest runner through SSH. Real network integration remains limited to an isolated disposable Linux VM/test host, never VORTEX.
- On TYPHOON, `ssh lab` may reach the disposable Ubuntu VM `tempest-lab-ubuntu` (user `gary`, checkout `~/tempest`). Before using it, verify hostname, Ubuntu identity, repository origin/branch/SHA, namespace identities, VM snapshot and independent virt-manager console. The SSH key does not imply passwordless sudo. Network-changing experiments require explicit owner authorization for the exact VM and namespace, exact selectors, and verified snapshot/console recovery; never run them in the guest host namespace or on TYPHOON/VORTEX. `lab` may be unavailable from TORNADO or sandboxed Codex sessions; report that as a blocker rather than routing around it.
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

- **Feedback sequence:** TORNADO editing and targeted Windows pytest → selected safe Linux pytest on VORTEX with `scripts/remote-check.ps1` at an explicit reviewed commit → independent complete GitHub CI after an authorized push → disposable isolated Linux VM only for real network experiments.
- On VORTEX use only the allowlisted NetworkPlan, NetworkObjectRegistry, fixture-only Linux inventory, AF_UNIX/SO_PEERCRED, and Agent mode boundary tests. The runner uses `/home/gary/Docker/tempest`, its own Python 3.11 venv, temporary directories, `nice`, and a hard timeout. Check resources, origin, clean `master`, and exact SHA before testing. Do not start services, containers, dataplanes, privileged Agent, or network integration there.

- **TARGETED LOCAL FEEDBACK POLICY:** after each code change, run only tests covering changed modules or behavior. Targeted pytest uses `-x` so the first failure returns quickly; do not repeat the full backend suite for small edits.
- Full backend pytest is optional, at most once before task completion, with a hard 15-minute limit. If it reaches that limit, interrupt it and record `INCOMPLETE`, never as a pass; rely on GitHub Linux CI for the complete backend suite. Default `full` local mode means targeted backend + frontend + static checks.
- Reuse `backend/.venv` (managed with `uv`) and `frontend/node_modules`; do not reinstall dependencies on every iteration. Match Python 3.11 and Node.js 20 with CI.
- Detect the current OS. On Windows use `scripts/local-check.ps1`; on Linux use `scripts/local-check.sh`. Never use a Windows venv in Linux or a Linux venv in Windows.
- Report Linux-only AF_UNIX/SO_PEERCRED/systemd checks that cannot run on this OS as `NOT_RUN_ON_WINDOWS` (or the corresponding OS); never count them as passed. `full` must print every platform-specific exclusion.
- GitHub Actions provides the complete Linux compatibility check after an authorized push. It is not an intermediate edit/test loop and should not be triggered after each local edit. A published commit with failed CI blocks further publication until the cause is understood and fixed.
- GitHub's Linux runner remains responsible for Linux-only checks excluded on Windows; keep those CI checks enabled.
- Keep platform skips at individual Linux-dependent tests, never module-wide where a file also contains portable tests. Report unavailable AF_UNIX/SO_PEERCRED/systemd checks as `NOT_RUN_ON_WINDOWS` (or the corresponding OS), never as passed; all Linux-only tests must remain active in Linux CI.
- Do not disable checks to make the workflow faster. Local checks never install dependencies, require `sudo`, start containers, or change system settings. Ordinary development does not require Docker Engine or privileged containers.
- Docker runtime smoke and real network integration tests may run only in a pre-approved isolated Linux VM/test host. Do not run privileged Network Agent or network integration tests on TYPHOON or VORTEX.
- Stop and consult the owner before risky system changes, privileged operations, system service changes, or changes outside the workspace.

## Accepted phase 0/1 baselines

The baseline SHA, host-operation audit, feature gap matrix, and proposed ADR-001 are recorded in `docs/`. Phase 0 and phase 1 are accepted. Gate 1 evidence is recorded in `docs/STATUS.md` and the Actions run above. Tasks 2.1–2.3 were accepted after owner review and successful CI as recorded in `docs/PROJECT_SPEC.md`. The owner accepted task 2.4 after review of `6cbb73b22146ccba1ad123183e4e147dc8f59f2b` and successful CI #26. The owner accepted task 2.5 within its verified scope and authorized the first 2.6 trial in the prepared `tempest-ns-lab` namespace on disposable VM `tempest-lab-ubuntu`. Use TYPHOON for development and `ssh lab` with `sudo -n` for that trial. Reverify the recovery backup, independent console, namespace identity and exact selector absence before mutation. Further 2.6 operation trials require an agreed executable plan and exact selectors. Use the [Xray configuration lifecycle](docs/XRAY_MVP_LIFECYCLE.md) for the current slice and retain the separate kernel contracts for their accepted scope. Tasks 2.7–2.8 and deployment require separate authorization. ADR-001 remains `PROPOSED`.
