# TEMPEST Network Agent systemd template

`tempest-network-agent.service` runs the standalone Python IPC process. It is
not installed or enabled by this repository's tests. It currently dispatches
only to an in-memory mock and has no network apply implementation.

The target Linux host must provision a dedicated `tempest-agent` system user,
the shared `tempest-control` socket group, and a root-owned
`/etc/tempest/network-agent.env` copied from `network-agent.env.example` with
the actual Control Plane numeric UID/GID. The Control Plane must use the
allowlisted effective primary GID to connect to the socket. Do not guess
container UID/GID mappings. Keep the optional cgroup setting unset until
`SO_PEERCRED` PID and `/proc/<pid>/cgroup` mapping are verified on that host.

The unit has no capability grants (`CapabilityBoundingSet=` and
`AmbientCapabilities=` are empty), hides the common Docker socket/data paths
and selected host service configuration paths, and allows only `AF_UNIX`. No Linux test in
this task measured the effective capability masks of a running systemd unit;
the unit declaration was checked in the Linux CI test. Do not add
`CAP_NET_ADMIN` based on this template.

For a dedicated, pre-created network namespace, an operator may prepare a
systemd drop-in containing:

```ini
[Service]
NetworkNamespacePath=/run/netns/tempest-dataplane
```

The namespace must already exist and be prepared by an operator. This service
does not create namespaces, call `setns`, inspect host networking, or need
`CAP_SYS_ADMIN`. AF_UNIX remains the only allowed address family. Validate the
effective capabilities and namespace inode on an isolated Linux VM before any
future capability or network operation is considered. Never install this unit
on TORNADO or VORTEX as part of task 2.2.
