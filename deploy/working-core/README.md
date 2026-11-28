# TEMPEST Working Core deployment units

`tempest-wc-network.service` creates the owned topology. The Xray Agent is
ordered after that unit and stops with it. `tempest-wc-panel.service` requires
Docker, the topology and the Agent; its pre-start uses
`scripts/deploy/wait-agent-socket.py` to wait for both an active Agent and a
connectable Unix socket before calling Compose.

## Install the boot units

First prepare a persistent, root-owned deployment directory containing all of
these files: `.env`, `docker-compose.working-core.yml`, and
`deploy/working-core/vortex.compose.yml`. Keep private settings in the host
`.env` or protected external files; never commit them. The deployment directory
must not be a symlink and its path must not contain whitespace.

Run the installer from a trusted TEMPEST checkout:

```sh
sudo bash scripts/deploy/install-working-core-systemd.sh \
  --check --deploy-dir /opt/tempest-deploy
sudo bash scripts/deploy/install-working-core-systemd.sh \
  --install --deploy-dir /opt/tempest-deploy
```

`--check` is read-only. `--install` installs the network, Agent and panel unit
files, the readiness helper and a path-only root-owned environment file. It
enables boot startup but deliberately does not start units, Docker, or network
operations. It refuses to overwrite differing existing units or configuration;
review such differences manually. The operator still prepares `/opt/tempest`
runtime binaries, Agent Python/source, owned network helper scripts, the
private provisioning directory and the Compose environment. This installer
does not configure Caddy, modify the old panel, or perform a production
cutover.

The panel unit uses `docker compose start --wait`; it assumes the containers
were created from the persistent deployment directory. Keep those Compose
manifests available for future service starts. This is required for dependable
boot recovery and is not proved by `systemctl enable` or a currently active
oneshot unit.

## VORTEX audit, 2026-11-28

The manually installed `tempest-wc-panel.service` is active and enabled, but
was absent from the repository at audit time. It waits for socket-path
existence with an inline Fish loop, then starts Compose from a checkpoint-named
directory. The repo helper is stricter: it verifies that the Xray Agent unit is
active and that the socket accepts a connection. The current panel/API
containers carry Compose labels for that directory, but the Compose manifests
were absent there during the read-only audit and the directory has no Git
metadata. Reboot recovery therefore remains unverified and the current bundle
is not reproducible from that host directory. No VORTEX files or services were
changed during the audit.

The top-level `install.sh` remains the inherited PiTun installer. The existing
`scripts/deploy/install-runtime.sh` installs verified runtime binaries only;
the new systemd installer is the separate boot-unit step.
