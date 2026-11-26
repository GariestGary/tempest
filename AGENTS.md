# AGENTS.md — TEMPEST / Codex

Read [docs/PRODUCT_GOALS.md](docs/PRODUCT_GOALS.md) at the start of every new task. Its G01–G14 goals guide architectural and functional decisions, and may be changed only by an explicit decision of the owner. If work advances none of these goals, addresses no real risk and removes no specific blocker, reconsider whether it is worthwhile.

Read [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md) fully before editing code. It remains the technical specification; [docs/STATUS.md](docs/STATUS.md) records actual progress.

## Product and workflow

- TEMPEST is a self-hosted VPN gateway with a Web panel. Keep PiTun as the BSD-3-Clause fork base and preserve its history, licensing, working code and tests. Use FastAPI, React, SQLite and the existing generators and protocol implementations.
- Block **A — Working Core** at `93feeab084e2a66e1371e7f503baede6d1f32c6d` is **ACCEPTED_WITH_LIMITATIONS**. The immediate priority is practical AWG egress and the first device connection. The [roadmap](docs/PROJECT_SPEC.md#15-действующий-roadmap) governs current work. Phases 0–11 and their statuses are historical evidence.
- Work directly on `master`. Create a branch or pull request only on separate request. Use an authorized functional checkpoint for publication and full hosted CI/CodeQL.
- Own the assigned functional outcome. Plan, implement reversible changes, run tests and fix defects autonomously. Continue through intermediate slices and record noncritical items in the backlog. Implement and verify the necessary user journey, then continue to the next priority without formal acceptance after each slice. Keep one compact technical-debt list with reasons and closure conditions.
- Before building a subsystem, check PiTun; Xray, sing-box, WireGuard, AmneziaWG or another existing component; the old vortex-network-panel source; and standard Linux, Compose and systemd features. Use a small integration adapter when sufficient. Build a new subsystem only for a demonstrated product need.
- Preserve the accepted Agent IPC, authorization, isolation, transactions and configuration protection. Freeze expansion of universal kernel NetworkPlan, NetworkObjectRegistry, kernel WG, TUN/TPROXY, nftables and IP route managers until a specific product need requires it.
- Stop for blockers that prevent safe progress, missing mandatory resources, production changes, data loss, or a fundamental product decision. Finish independent work before requesting the specific authorization or resource needed.

## Safety

- Keep existing TEMPEST 2 and other services on VORTEX untouched. VORTEX is available only for the established unprivileged allowlisted pytest runner with `scripts/remote-check.ps1` on an explicit reviewed commit. No services, containers, dataplanes or privileged tests there.
- No host network takeover, privileged PiTun Compose, host Docker socket, broad Web/API privileges or host-wide DNS, route or firewall changes. Keep secrets out of logs and ordinary API responses, validate before apply, keep a protected last-known-good configuration (LKG) and fail closed if safe rollback is impossible.
- DIRECT requires explicit rules. Geo-specific routing is manual. AUTO uses transport health and explicitly approved candidates. Do not implement automatic detection of content geoblocking.
- Real network experiments run only in an explicitly approved disposable Linux VM and namespace. On TYPHOON, `ssh lab` may reach Ubuntu VM `tempest-lab-ubuntu` (user `gary`, checkout `~/tempest`). Before any experiment verify guest identity, origin/branch/SHA, target and PID 1 namespaces, recovery snapshot and independent console. Risky mutations require exact selectors and owner authorization. Never mutate the guest host namespace or bypass unavailable lab access. Risky system changes, irreversible operations and production deployment require separate authorization.

## Verification

- Detect the OS and use `scripts/local-check.ps1` on Windows or `scripts/local-check.sh` on Linux. Reuse the native `backend/.venv` and `frontend/node_modules`, targeting Python 3.11 and Node.js 20. Local checks use no installations, sudo, Compose startup, or system changes.
- Use targeted pytest with `-x` and relevant frontend checks while editing. Full local backend pytest is optional once per checkpoint, with a hard 15-minute limit. Record a timeout as `INCOMPLETE`. Keep portable tests active, skip only individually unavailable platform cases, and report exclusions explicitly. Preserve significant tests and all Linux CI coverage.
- Use the minimum sufficient real end-to-end test for network behavior; combine external-client and UDP-ingress publication verification with the first practical device connection, without creating a second VM solely for acceptance. Prepare deployment as features become ready. Verify user journeys end to end. A mock, a valid config, and a bound listener each provide distinct evidence. Claim a working dataplane only with real traffic evidence. Repeat accepted network tests when the applicable behavior or packaging changes.
- Update the dated progress log and spec checkboxes for verified work only. Report the user-visible result, tests and limits, affected safety boundaries, SHA, and CI/CodeQL status at the functional checkpoint.
