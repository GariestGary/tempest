# Task 2.5 — isolated Linux VM test plan

**Status (2026-11-14):** the first VM-only read-only inventory trial passed on a disposable Ubuntu VM. The trial establishes repeatable collection and host-namespace refusal only. It does not establish atomicity, ownership, Agent systemd sandboxing, durable receipts, real apply/rollback, or ownership enforcement on changed kernel objects. Task 2.5 remains `IN_PROGRESS`; network-changing trials require separate explicit owner authorization. ADR-001 remains `PROPOSED`; Gate 2 is open.

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

## Recorded trial — 2026-11-14

The operator ran the read-only trial in disposable VM `tempest-lab-ubuntu`, under QEMU/KVM on TYPHOON. The VM ran Ubuntu 26.04.1 LTS, kernel `7.0.0-43-generic`, and Python 3.11.18. The tested checkout was `33bd5d79203537edb4854216b5bf621c00466464`. A QCOW2 backup was created and verified before the trial; `qemu-img check` reported no errors. No VORTEX or TYPHOON host network settings were changed.

The prepared target was `tempest-ns-lab`. Its network namespace identity was device 5, inode 4026532548; the Ubuntu main namespace was device 5, inode 4026531833. They differed as expected. The main namespace exposed `lo` and `enp1s0`; the lab namespace exposed only `lo`.

Two live collector runs each completed with six unproven objects. The six raw JSON feeds from the two runs were byte-for-byte identical; namespace identity and inventory revision also matched. Both manifests recorded `atomic: false` and `ownership_proof: false`. These results show repeatable read-only output for this prepared VM state. They do not prove an atomic cross-command snapshot, object creation continuity, or ownership.

A negative collector invocation using the main namespace and name `tempest-ns-host-check` failed with `capture failed: target is the host namespace`, exit code 1, and created no evidence directory. The operator did not run any network-changing operation. The automated suite already covered the identity guard; a CLI regression also checks exit code 1 and absence of the output directory.

### Confirmed facts and remaining unknowns

Confirmed by this trial: the collector ran in the prepared disposable VM; the target namespace differed from the main namespace; two captures produced identical six-feed bytes, identity and revision; each returned six unproven observations; both manifests denied atomicity and ownership proof; the collector refused the main namespace before creating evidence.

Still unverified: runtime Agent systemd sandbox enforcement and effective capability masks; durable ownership receipts and restart recovery; real nftables, route, `ip rule`, TUN or WireGuard apply/rollback; and ownership enforcement on network objects changed by TEMPEST. No such objects were created or changed during this trial. The trial does not accept task 2.5, Gate 2, or ADR-001.

## Next minimal non-mutating trial

Keep the same disposable-VM, snapshot, console and private-evidence controls. Do not create or modify nftables, routes, `ip rule`, fwmark, TUN or WireGuard objects. Use the live inventory only as observations, which remain unproven:

1. Capture the prepared target and record identity/revision. Select existing observations as foreign inputs; do not infer ownership from names, handles, marks, IDs or matching fingerprints.
2. Run a deliberately mismatched expected boot ID and a deliberately mismatched namespace device/inode against the collector. Each must fail closed before any evidence directory is created.
3. Feed a copied snapshot with a changed fingerprint/revision into the fixture-level registry/plan checks. Verify drift is reported and no proof is assigned. Use an observed foreign claim as a collision input and verify a desired object requesting that claim is rejected without adoption.
4. Capture the real target again and compare its six feeds and identity to the initial capture. Record that this scenario made no network changes.

This is a read-only check of identity rejection, foreign-claim conflict and unproven drift handling. It does not test enforcement during mutation or recovery after a real object was changed; those require a separately authorized network-changing experiment. Do not start task 2.6 as part of this plan.

## Read-only trial, after VM preparation

1. From the VM console, record `uname -a`, `ip -Version`, `nft --version`, `systemd --version`, `/proc/sys/kernel/random/boot_id`, and the host/target namespace links and `stat` device/inode. Confirm the target is not the host namespace. Check that the Agent process, if running, has the target namespace identity and no unexpected access to host netns, Docker socket or privileged devices.
2. Capture the six JSON outputs separately **inside the target namespace** with the collector recipe above. For host comparison, use operator-controlled read-only commands separately; never pass a host namespace identity to the collector. Save raw host and target outputs as private artifacts, noting capture start/end time, exit code and stderr for each command. A nonzero exit, missing feed, changed namespace identity, or parse error is a failed inventory. Compare both namespaces to ensure the target parser never reports host-only objects as target observations. The explicit `-4/-6` command context is required because ordinary JSON `ip rule` records, including standard `from all` rules, can omit `family`; an unqualified feed cannot safely assign family to them.
3. Run the fixture/parser backend against target outputs without loading a receipt. Expect every observation to remain `proof=None`. Compare a second full capture; changed content digest triggers a fresh review, and an identical digest does not establish atomicity or ownership. Check absence of network changes between before/after read-only captures.
4. Inspect the actual systemd unit with `systemctl cat/show`, its UID/GID, `NetworkNamespacePath`, `NoNewPrivileges`, filesystem/device restrictions, and `/proc/<pid>/status` `CapPrm/CapEff/CapBnd/CapAmb`. Compare against the expected empty-capability read-only profile. Verify socket and journal permissions through the operator account. Any capability or host namespace access beyond the agreed read-only profile fails this trial.
5. Record before/after host and target link, rule, route and nft dumps; diff exact objects. Foreign objects must remain byte-for-byte equivalent apart from known volatile counters. Do not infer ownership from `tempest-*`, nft handles, ifindexes, marks, table IDs or fingerprints. No apply/delete runs in this trial.

## Network-changing trial — separate owner approval required

After a fresh snapshot and console/rollback rehearsal, the owner may authorize a precise VM-only test matrix. Preflight that lab names, marks, priorities, table IDs and subnets are unused. The operator seeds foreign nft tables/chains/rules (including a `tempest-*` name and recycled handle), routes, rules and links in the target namespace. Compare inventory and protected Agent journal before diff, immediately before any future apply, and after. Simulate process restart with a missing receipt, then namespace/link recreation and handle/ifindex reuse. Each unproven or changed object must block adoption/mutation. If an exact owned-object mutation backend is later authorized, verify it never alters these foreign objects, the host namespace, default route, DNS, SSH/console path or Docker-managed state. Test failure cleanup deletes only explicitly created lab objects by exact identity; restore the VM snapshot if baseline cannot be proven. Never use `nft flush ruleset`, table-wide route flush, broad rule deletion or host route replacement.

No current TEMPEST code is authorized to run this network-changing trial. Record command log, before/after dumps, Agent capability masks, namespace identity, failed conflicts, preserved foreign objects, cleanup and snapshot restore result. Gate 2 needs later apply/rollback and control-channel evidence beyond this inventory slice.
