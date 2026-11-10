# AGENTS.md — TEMPEST / Codex

**Read `docs/PROJECT_SPEC.md` fully before editing code.** That file is the source of truth for product goals, scope, safety rules, architecture, current phase, acceptance gates, and progress tracking.

## Current instructions

- Product name: **TEMPEST** (no version suffix). Fork base: `DaveBugg/PiTun` (`master`, BSD-3-Clause).
- **Current phase: PHASE 1 (`READY_FOR_REVIEW`). PHASE 0 was accepted by the user on 2026-11-10.** Continue only phase 1 until review/acceptance; do not begin phase 2 without explicit approval.
- Reuse PiTun architecture/tests and preserve upstream history/license. Do not rewrite the backend in .NET.
- **No automatic geo-content unblocking detection.** Geo-specific routing is user-managed via explicit rules. AUTO is limited to transport-level health/failover within approved candidates.
- **No root/host takeover from Web/API.** Do not run privileged PiTun Compose, change DNS/default routes/firewall, use host Docker socket, or modify host-wide networking while auditing.
- **Do not touch the existing TEMPEST 2 or other services on VORTEX** unless the user separately authorizes a tested migration stage.
- Development on TORNADO/Windows; Linux network integration only in an isolated Linux VM/test host, never on production by default.
- If uncertain about networking, protocol support, privileges, licenses or irreversible choices: inspect code, document alternatives and ask before implementation.
- Preserve an explanation and tests for every nontrivial change; update `docs/PROJECT_SPEC.md` checkboxes and the dated progress log **only for verified work**.

## Execution sequence for each phase

1. Read the current phase and its gate in `docs/PROJECT_SPEC.md`.
2. Inspect affected upstream files and tests. Produce a concise plan.
3. Implement small, reversible changes. Never edit outside scope solely for tidiness.
4. Run relevant backend/frontend tests. Use an isolated Linux VM for real nftables/TUN/WG tests.
5. Report changed files, commands/tests, pass/fail, risks, unverified scenarios, and commit SHA.
6. Stop and ask for approval before moving to the next phase.

## Accepted phase 0 baseline

The baseline SHA, host-operation audit, feature gap matrix, test commands, and proposed ADR-001 are recorded in `docs/`. Phase 0 is accepted. Current authorized work is phase 1 only; do **not** start feature implementation beyond its dev/test scope, deploy on VORTEX, or move to phase 2 without explicit user approval.
