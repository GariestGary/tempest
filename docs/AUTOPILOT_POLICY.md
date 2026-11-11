# Autopilot policy

## Development feedback loop

- Start with relevant local checks after a code change. Do not wait for GitHub Actions after every intermediate edit.
- Reuse `backend/.venv` managed by `uv` and the existing `frontend/node_modules`; install dependencies only for initial setup or when the environment/lockfile requires repair.
- Use `scripts/local-check.sh quick|backend|frontend|full`; setup and exact CachyOS commands are in [LOCAL_DEV.md](LOCAL_DEV.md).
- Run the full local suite before completing a substantial task or phase. Do not disable tests or lint to save time.
- GitHub Actions is an independent verification after push. A published commit that fails CI blocks further publication until the failure is understood and corrected.

## Safety boundaries

- Local scripts do not install dependencies, require `sudo`, start containers, or change system settings.
- Docker runtime smoke and real network integration are allowed only in a pre-approved isolated Linux VM/test host. Never run privileged Network Agent or network integration tests on TYPHOON; never touch VORTEX services without separate authorization.
- Before risky system changes, privileged operations, or system service changes, stop and ask the owner.
- Do not begin a later TEMPEST task or phase without owner authorization, even if the local workflow is ready.
