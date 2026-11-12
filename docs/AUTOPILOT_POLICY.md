# Autopilot policy

## Development feedback loop

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
- Docker runtime smoke and real network integration are allowed only in a pre-approved isolated Linux VM/test host. Never run privileged Network Agent or network integration tests on TYPHOON; never touch VORTEX services without separate authorization.
- Before risky system changes, privileged operations, or system service changes, stop and ask the owner.
- Do not begin a later TEMPEST task or phase without owner authorization, even if the local workflow is ready.
