# Contributing to TEMPEST

Thanks for considering a contribution. TEMPEST is a fork of
[DaveBugg/PiTun](https://github.com/DaveBugg/PiTun), and preserves its
upstream history and BSD-3-Clause notices. This repository is currently in
phase 1; local application work uses only the safe no-op profile.

Contributions intended for TEMPEST are licensed under the project's BSD
3-Clause License (see [LICENSE](LICENSE)).

## TEMPEST development workflow

- `master` is the only working branch. Make changes directly on `master` and
  commit them there. Do not create feature branches or Pull Requests unless
  the user explicitly asks for them.
- Work only on the phase currently authorized in `docs/PROJECT_SPEC.md`.
  A phase remains unaccepted until the user confirms its acceptance; do not
  begin the next phase before that confirmation.
- Run the relevant checks before committing and push verified commits to
  `origin/master`. GitHub Actions runs on every push to `master`.

## Quick start

Use the dedicated safe Compose file. Do not start FastAPI directly without
`DEV_SAFE_MODE=1`; its normal lifecycle retains upstream networking behavior.

```powershell
docker compose -f docker-compose.dev.safe.yml config --quiet
docker compose -f docker-compose.dev.safe.yml up --build
```

UI is at <http://localhost:3000>, API at <http://localhost:8000>. Stop with
`Ctrl+C` or run `docker compose -f docker-compose.dev.safe.yml down
--remove-orphans`.

Run tests separately from the application runtime:

```bash
cd backend && python -m pytest tests/ -q
cd frontend && npm ci && npm run test:ci && npm run build && npm run lint
```

For a Linux VM runtime/security check, follow
[`docs/dev/LINUX_VM.md`](docs/dev/LINUX_VM.md). Do not use the upstream
production Compose or installer for TEMPEST development.

## Reporting bugs

Open an issue with:
1. TEMPEST version (sidebar → version popover → "Copy" — paste the JSON dump).
2. What you expected to happen.
3. What actually happened.
4. Last ~50 lines of `docker compose -f docker-compose.dev.safe.yml logs api` if relevant.

## Changes and review

- Keep changes focused — one commit per concern where practical.
- Add a test for any new backend logic that has branching behaviour
  (CRUD endpoints, scheduler decisions, parse routines). UI changes
  don't need tests unless you're adding non-trivial logic.
- Run the relevant backend/frontend checks and safe Compose smoke before
  pushing when the environment supports them; report any unavailable checks.
- Match the existing comment style: prefer "why this is the way it is"
  over "what this line does". Rule of thumb: if a line of code can
  generate the comment, the comment isn't earning its keep.
- Don't bump `APP_VERSION` in a development commit — releases are tagged by the
  maintainer.

## Code style

- **Python**: black-ish formatting, type hints on public functions, no
  hard line limit. Imports grouped stdlib / 3rd-party / local.
- **TypeScript**: existing patterns in `frontend/src/` — function
  components, React Query for server state, Zustand for global UI state.
  Tailwind utility classes; new shared classes go in `index.css`.
- No comment-bloat. The codebase already has plenty of context comments;
  a new file should match that density, not 3× it.

## Things to keep out of the repo

- Personal LAN IPs, SSH keys, deploy hostnames. Use `~/.ssh/config`
  aliases locally. There's a `notes.md` and `deploy_2nd.py` slot already
  reserved in `.gitignore` for maintainer-specific tooling.
- `.env` files. Use `.env.example` to document new variables.
- Build artifacts, `node_modules/`, geo databases.
- AI-agent memory directories (`.claude/`, `.serena/`, `.cursor/`).
  These store local conversation history and per-developer scratch
  context — never commit them.

## Releases

Tagged `vX.Y.Z` triggers `.github/workflows/release.yml`, which builds
both `linux/amd64` and `linux/arm64` Docker images, exports them as
loadable `.tar.gz` files, and uploads them as workflow artifacts. The
maintainer attaches the artifacts to the GitHub Release manually —
this is intentional, see comments at the top of the workflow file.
