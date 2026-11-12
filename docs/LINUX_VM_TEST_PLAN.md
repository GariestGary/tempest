# Task 2.5 — isolated Linux VM test plan

**Status:** read-only collector prepared for review; VM not created and no live capture executed. Read-only trials require an operator-prepared disposable VM. Network-changing trials require a separate explicit owner authorization. ADR-001 remains `PROPOSED`; Gate 2 is open.

## VM and evidence boundary

Use a disposable Ubuntu Server x86_64 VM with 2 vCPU, 4 GiB RAM, 20 GiB disk, one virtual NIC on an isolated lab network, hypervisor console access independent of SSH, and a snapshot taken before each trial. Keep it off production bridges and VORTEX. Install pinned Python 3.11, `iproute2`, `nftables`, systemd and test dependencies through normal VM provisioning; record their exact versions, kernel version, VM ID and snapshot ID. The operator creates the target namespace before trials. The application and Agent never create or enter namespaces themselves. Preserve a known-good console path and recovery snapshot. Redact addresses/secrets before sharing reports.

The first read-only run uses a pre-created dedicated namespace, e.g. `tempest-ns-lab`. Confirm that its `/proc/.../ns/net` device/inode and boot ID match the prepared target record, and that both differ appropriately from the host namespace. Record the target process PID/start time and namespace identity before and after each capture; a changed identity invalidates the capture. A name or symlink path alone is insufficient. The collector checks boot ID and namespace device/inode before and after every command; it does not prove cross-command atomicity or namespace lifetime after capture.

### Collector recipe for the future disposable VM

The operator first confirms the VM, snapshot and isolated network from the preceding section. From the VM console, prepare a dedicated namespace outside the application, record its identity, and verify it differs from PID 1's namespace. Do not run this recipe on VORTEX or TYPHOON. The following commands are a **future VM recipe**, not a record of commands executed for this task:

```bash
cat /proc/sys/kernel/random/boot_id
stat -Lc '%d %i' /proc/1/ns/net
stat -Lc '%d %i' /run/netns/tempest-ns-lab
ip -Version
nft --version
# Use the recorded boot ID and target device/inode as exact arguments.
ip netns exec tempest-ns-lab .venv/bin/python -m app.network_agent.linux_inventory_capture \
  --name tempest-ns-lab --boot-id '<recorded-boot-uuid>' \
  --netns-device '<recorded-device>' --netns-inode '<recorded-inode>' \
  --output-dir '<new-private-evidence-directory>'
```

Run the Python module from the repository's `backend` directory using its Python 3.11 environment, with operator-controlled `ip netns exec`. Entering a namespace and reading nftables may require elevated operator privileges even though the commands only read state. Record the actual UID and capability masks for this capture separately from the proposed empty-capability Agent service; do not grant the Agent capabilities merely to make this trial pass. If the read-only profile cannot enumerate nftables, record a failed/incomplete inventory and review a separate VM-only collection identity. The collector itself neither enters nor creates a namespace. It only resolves `ip` and `nft` from fixed system paths and invokes `ip -j link`, separate `ip -4/-6 -j rule`, separate `ip -4/-6 -j route show table all`, and `nft -j list ruleset` without a shell. Each command has a ten-second timeout; nonzero exit, stderr, empty output, invalid UTF-8, oversized feed, parse error or changed identity fails the whole capture. A successful run creates a new mode-0700 evidence directory containing six mode-0600 raw JSON feeds and `manifest.json` with exact command, timestamps, digests and `ownership_proof: false`. Treat a directory without a complete manifest as failed evidence. Raw inventory can reveal addresses and rules: keep it private and redact copies shared for review. The operator must record PID/start time separately. If the prepared namespace disappears and its inode is later reused, discard the capture.

## Read-only trial, after VM preparation

1. From the VM console, record `uname -a`, `ip -Version`, `nft --version`, `systemd --version`, `/proc/sys/kernel/random/boot_id`, and the host/target namespace links and `stat` device/inode. Confirm the target is not the host namespace. Check that the Agent process, if running, has the target namespace identity and no unexpected access to host netns, Docker socket or privileged devices.
2. Capture the six JSON outputs separately **inside the target namespace** with the collector recipe above. For host comparison, use operator-controlled read-only commands separately; never pass a host namespace identity to the collector. Save raw host and target outputs as private artifacts, noting capture start/end time, exit code and stderr for each command. A nonzero exit, missing feed, changed namespace identity, or parse error is a failed inventory. Compare both namespaces to ensure the target parser never reports host-only objects as target observations. The explicit `-4/-6` command context is required because ordinary JSON `ip rule` records, including standard `from all` rules, can omit `family`; an unqualified feed cannot safely assign family to them.
3. Run the fixture/parser backend against target outputs without loading a receipt. Expect every observation to remain `proof=None`. Compare a second full capture; changed content digest triggers a fresh review, and an identical digest does not establish atomicity or ownership. Check absence of network changes between before/after read-only captures.
4. Inspect the actual systemd unit with `systemctl cat/show`, its UID/GID, `NetworkNamespacePath`, `NoNewPrivileges`, filesystem/device restrictions, and `/proc/<pid>/status` `CapPrm/CapEff/CapBnd/CapAmb`. Compare against the expected empty-capability read-only profile. Verify socket and journal permissions through the operator account. Any capability or host namespace access beyond the agreed read-only profile fails this trial.
5. Record before/after host and target link, rule, route and nft dumps; diff exact objects. Foreign objects must remain byte-for-byte equivalent apart from known volatile counters. Do not infer ownership from `tempest-*`, nft handles, ifindexes, marks, table IDs or fingerprints. No apply/delete runs in this trial.

## Network-changing trial — separate owner approval required

After a fresh snapshot and console/rollback rehearsal, the owner may authorize a precise VM-only test matrix. Preflight that lab names, marks, priorities, table IDs and subnets are unused. The operator seeds foreign nft tables/chains/rules (including a `tempest-*` name and recycled handle), routes, rules and links in the target namespace. Compare inventory and protected Agent journal before diff, immediately before any future apply, and after. Simulate process restart with a missing receipt, then namespace/link recreation and handle/ifindex reuse. Each unproven or changed object must block adoption/mutation. If an exact owned-object mutation backend is later authorized, verify it never alters these foreign objects, the host namespace, default route, DNS, SSH/console path or Docker-managed state. Test failure cleanup deletes only explicitly created lab objects by exact identity; restore the VM snapshot if baseline cannot be proven. Never use `nft flush ruleset`, table-wide route flush, broad rule deletion or host route replacement.

No current TEMPEST code is authorized to run this network-changing trial. Record command log, before/after dumps, Agent capability masks, namespace identity, failed conflicts, preserved foreign objects, cleanup and snapshot restore result. Gate 2 needs later apply/rollback and control-channel evidence beyond this inventory slice.
