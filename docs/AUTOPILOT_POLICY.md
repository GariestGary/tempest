# Autopilot policy

## Development feedback loop

- Use the active development environment and its native targeted pytest as the fast edit/test cycle. VORTEX is the TEMPEST production host and may be inspected over SSH for read-only audit, TEMPEST diagnostics, logs, and service state. Installation, updates, or maintenance on VORTEX require explicit owner authorization for the operation. GitHub CI independently runs the complete checks after an authorized push. Experimental development and potentially dangerous network tests belong only in a disposable isolated Linux VM.
- When using `scripts/remote-check.ps1`, the pytest runner must verify SSH, available resources, clean `master`, expected `origin`, exact commit SHA, and the project Python 3.11 venv before testing. Use only allowlisted tests, temporary directories, `nice`, and a timeout; report passed/failed/skipped and SHA. Connectivity or resource failure ends before any repository update.

- **TARGETED LOCAL FEEDBACK POLICY:** run only tests for changed modules or behavior after each code change. Targeted pytest uses `-x` and stops at the first failure. Do not repeat full backend pytest for small edits.
- Full backend pytest is optional and may run at most once before completing a task, with a hard 15-minute timeout. If interrupted by the limit, record `INCOMPLETE`, never as a pass; use Linux CI for the full backend check. The default `full` local mode is targeted backend + frontend + static checks. Do not disable tests or lint.
- Reuse `backend/.venv` managed by `uv` and the existing `frontend/node_modules`; install dependencies only during setup or when the environment/lockfile needs repair.
- Detect the operating system. Run `scripts/local-check.ps1` on Windows and `scripts/local-check.sh` on Linux. Never cross-use platform-specific virtual environments.
- Match CI with Python 3.11 and Node.js 20 where available.
- Keep skips at individual tests that truly require Linux facilities; do not skip a whole module containing portable checks. Report unavailable checks as `NOT_RUN_ON_WINDOWS`, never as passes. Linux-specific tests must remain enabled in Linux CI.
- GitHub Actions is the complete Linux compatibility check after an authorized push, not an intermediate edit/test loop and not a required check after each edit. A published commit that fails CI blocks further publication until the failure is understood and corrected.
- Linux-only checks excluded on Windows continue to run in GitHub's Linux workflow; keep those checks enabled.
- Local checks do not install dependencies, require `sudo`, start containers, or change system settings. Ordinary development does not require Docker Engine or privileged containers.

## Safety boundaries

- Local scripts do not install dependencies, require `sudo`, start containers, or change system settings.
- Docker runtime smoke and real network integration are allowed only in a pre-approved disposable Linux VM/test host. Never run experimental privileged Network Agent or network integration tests on TYPHOON or VORTEX.
- Before any authorized production mutation, first create protected backups and prepare a concrete rollback procedure. Once the owner has authorized the operation, perform its necessary scoped steps autonomously without requesting approval for each intermediate step. Keep secrets, private keys, and user data out of Git and logs.
- VORTEX maintenance authorization does not authorize reboot, changes to third-party services, host firewall, system routing, or a running dataplane; each requires separate owner authorization. Avoid Apply and dataplane restart unless needed and separately authorized.
- Continue autonomously through the ordered product priorities: implement, verify the necessary user journey, fix defects, and proceed. Do not introduce new gates or repeat successful tests without a relevant change. Keep noncritical debt in the compact list in PROJECT_SPEC §15, with a reason and closure condition.
- A is ACCEPTED_WITH_LIMITATIONS at `93feeab084e2a66e1371e7f503baede6d1f32c6d`. Combine external-client and UDP ingress checks with the first practical device connection; no second VM solely for formal acceptance. Prepare practical deployment as capabilities become ready. Production deployment requires explicit authorization for the operation; changes to host networking, third-party services, dataplane, and reboot remain separately authorized actions.
