# AGENTS.md — TEMPEST / Codex

**Read `docs/PROJECT_SPEC.md` fully before editing code.** That file is the source of truth for product goals, scope, safety rules, architecture, current phase, acceptance gates, and progress tracking.

## Current instructions

- Product name: **TEMPEST** (no version suffix). Fork base: `DaveBugg/PiTun` (`master`, BSD-3-Clause).
- **Phase 1: `ACCEPTED` by the owner on 2026-11-10**, based on successful GitHub Actions [run #5](https://github.com/GariestGary/tempest/actions/runs/42783278332) at commit `38fec1220c250b3b398e9160842e301d92d9d356`. **Current phase: PHASE 2 (`IN_PROGRESS`); tasks 2.1 and 2.2 are `ACCEPTED`; task 2.3 is `IN_PROGRESS`.** Only task 2.3 is authorized; do not start tasks 2.4–2.8 or phase 3, and do not mark phase 2 accepted before Gate 2 and owner review.
- **Git workflow:** `master` is the only working branch. Make changes directly on `master`; do not create feature branches or Pull Requests unless the user asks for them separately.
- Reuse PiTun architecture/tests and preserve upstream history/license. Do not rewrite the backend in .NET.
- **No automatic geo-content unblocking detection.** Geo-specific routing is user-managed via explicit rules. AUTO is limited to transport-level health/failover within approved candidates.
- **No root/host takeover from Web/API.** Do not run privileged PiTun Compose, change DNS/default routes/firewall, use host Docker socket, or modify host-wide networking while auditing.
- **Do not touch the existing TEMPEST 2 or other services on VORTEX** unless the user separately authorizes a tested migration stage.
- Development is now on TYPHOON/CachyOS Linux; Linux network integration only in an isolated Linux VM/test host, never on the development host or production by default.
- If uncertain about networking, protocol support, privileges, licenses or irreversible choices: inspect code, document alternatives and ask before implementation.
- Preserve an explanation and tests for every nontrivial change; update `docs/PROJECT_SPEC.md` checkboxes and the dated progress log **only for verified work**.

## Execution sequence for each phase

1. Read the current phase and its gate in `docs/PROJECT_SPEC.md`.
2. Inspect affected upstream files and tests. Produce a concise plan.
3. Implement small, reversible changes. Never edit outside scope solely for tidiness.
4. Run relevant backend/frontend tests. Use an isolated Linux VM for real nftables/TUN/WG tests.
5. Report changed files, commands/tests, pass/fail, risks, unverified scenarios, and commit SHA.
6. Stop and ask for approval before moving to the next phase.

## Accepted phase 0/1 baselines

The baseline SHA, host-operation audit, feature gap matrix, and proposed ADR-001 are recorded in `docs/`. Phase 0 and phase 1 are accepted. Gate 1 evidence is recorded in `docs/STATUS.md` and the Actions run above. Task 2.1 was accepted after review of `be061d953c52e25da8338d545d117fdbb1d9da24` and successful CI run #10. The owner accepted task 2.2 after reviewing `91fb2c63a5204b1a16fb960a718108aae5e4d3e9` and successful CI. Task 2.3 is authorized and in progress; do **not** start tasks 2.4–2.8, implement real nftables/WireGuard apply, deploy on VORTEX, or move beyond task 2.3 without separate user confirmation. ADR-001 remains `PROPOSED` until Linux architecture experiments are reviewed.
