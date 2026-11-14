# Task 2.5 — isolated Linux VM test plan

**Status (2026-11-14):** the first VM-only read-only inventory trial, a live namespace-identity mismatch check, and an offline replay of the real capture passed. These establish repeatable collection and fail-closed parsing/conflict behavior only. They do not establish atomicity, ownership, Agent systemd sandboxing, durable receipts, real apply/rollback, or ownership enforcement on changed kernel objects. Task 2.5 remains `IN_PROGRESS`; any VM test that creates network objects requires separate explicit owner authorization. ADR-001 remains `PROPOSED`; Gate 2 is open.

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

### Additional checks — 2026-11-14

**Live identity mismatch:** the collector was run inside `tempest-ns-lab` with the expected inode incremented by one. It failed with `capture failed: target namespace identity changed`, exit code 1, and created no evidence directory. This was a read-only live check. It did not create or alter kernel objects.

**Offline replay:** the operator used the private real capture at `/tmp/tempest-capture-1`; no raw feed, address, key, or private artifact was copied into the repository. All six feed files were checked against the SHA-256 values in their manifest. The parsed snapshot was used only in memory. A desired `MANAGED_INTERFACE` named `lo` with the observed object's fingerprint was rejected with `desired resource collides with unproven observation`. Then the `lo` MTU was incremented only in a copied JSON feed and reparsed in memory: object ID stayed the same, fingerprint and inventory revision changed, and proof remained `None`. The replay ended with `All offline checks passed`.

The offline replay validates parser/registry behavior against a real read-only snapshot. It does not load an ownership receipt or test any mutation path. The manifest digests establish feed integrity relative to that manifest, not authenticity or ownership.

### Confirmed facts and remaining unknowns

Confirmed by this trial: the collector ran in the prepared disposable VM; the target namespace differed from the main namespace; two captures produced identical six-feed bytes, identity and revision; each returned six unproven observations; both manifests denied atomicity and ownership proof; the collector refused the main namespace before creating evidence.

Still unverified: runtime Agent systemd sandbox enforcement and effective capability masks; durable ownership receipts and restart recovery; real nftables, route, `ip rule`, TUN or WireGuard apply/rollback; and ownership enforcement on network objects changed by TEMPEST. No such objects were created or changed during this trial. The trial does not accept task 2.5, Gate 2, or ADR-001.

## Next VM-only foreign-object experiment — plan only; not run

This experiment will create temporary nftables, `ip rule`, and route objects inside `tempest-ns-lab`. **Do not execute it until separately authorized.** Use the disposable VM's verified snapshot and independent hypervisor console; the target namespace must have no external veth or default route. Do not run on TYPHOON, VORTEX, or the VM's main namespace.

### Preflight and seed

1. Record the VM snapshot ID, boot ID, target and main namespace identities, and fresh target/main link, rule, route, and nft JSON snapshots. Confirm the target namespace still has only the expected lab links and no external path.
2. Generate a unique run suffix. Before creating anything, verify that the proposed nft table/chain names, nft selectors, `ip rule` priority and fwmark, routing table ID, and destination/table route slot are absent from complete target snapshots. Check both namespaces. If any proposed claim is present or the inventory is incomplete, stop, choose a new unique claim, and repeat preflight.
3. Seed exactly one foreign nft table named with a `tempest-foreign-<run>` prefix, a regular unhooked chain and a simple rule in that chain; one `ip rule` at a unique priority matching a run-specific fwmark and looking up a dedicated table; and one blackhole route for `198.18.0.0/15` in that table. Use no base chain, hook, default route, veth, TUN, WireGuard interface, or packet mark. The mark must not be applied to traffic. Record the exact commands and object selectors in a private log.
4. Read the inventory again. Confirm only the three planned foreign resource groups appeared in the target namespace, all observations have `proof=None`, and no corresponding object appeared in the main namespace. Keep raw snapshots private.

### Offline conflict checks and rollback

5. Feed the read-only target snapshot to the parser and conflict checker. Prepare desired copies that collide separately with the foreign nft table/chain/rule selector, `ip rule` priority/mark, and route destination/table slot. Each must fail closed as an unproven/foreign conflict; none may receive a proof. Do not call Agent apply or delete code.
6. Compare the seeded objects byte-for-byte across a second read-only capture (allowing only fields already proven volatile). Verify host/main namespace snapshots remain unchanged and no packets or routes were redirected.
7. Remove only the exact test route, exact `ip rule` selector, and exact run-specific nft table that this procedure created, in reverse order. Never use `flush`, broad priority/table deletion, or wildcard cleanup. Re-capture and verify the target returns to its saved baseline and the main namespace is unchanged. If an exact cleanup cannot be verified, stop and restore the VM snapshot from the hypervisor console; do not guess at additional deletes.

This would test foreign-object enumeration, claim conflict and safe cleanup in a disposable namespace. It still would not establish TEMPEST ownership enforcement on changed kernel objects because no TEMPEST mutation backend exists in this slice. It does not start task 2.6 or accept task 2.5, Gate 2, or ADR-001.

## Read-only trial, after VM preparation

1. From the VM console, record `uname -a`, `ip -Version`, `nft --version`, `systemd --version`, `/proc/sys/kernel/random/boot_id`, and the host/target namespace links and `stat` device/inode. Confirm the target is not the host namespace. Check that the Agent process, if running, has the target namespace identity and no unexpected access to host netns, Docker socket or privileged devices.
2. Capture the six JSON outputs separately **inside the target namespace** with the collector recipe above. For host comparison, use operator-controlled read-only commands separately; never pass a host namespace identity to the collector. Save raw host and target outputs as private artifacts, noting capture start/end time, exit code and stderr for each command. A nonzero exit, missing feed, changed namespace identity, or parse error is a failed inventory. Compare both namespaces to ensure the target parser never reports host-only objects as target observations. The explicit `-4/-6` command context is required because ordinary JSON `ip rule` records, including standard `from all` rules, can omit `family`; an unqualified feed cannot safely assign family to them.
3. Run the fixture/parser backend against target outputs without loading a receipt. Expect every observation to remain `proof=None`. Compare a second full capture; changed content digest triggers a fresh review, and an identical digest does not establish atomicity or ownership. Check absence of network changes between before/after read-only captures.
4. Inspect the actual systemd unit with `systemctl cat/show`, its UID/GID, `NetworkNamespacePath`, `NoNewPrivileges`, filesystem/device restrictions, and `/proc/<pid>/status` `CapPrm/CapEff/CapBnd/CapAmb`. Compare against the expected empty-capability read-only profile. Verify socket and journal permissions through the operator account. Any capability or host namespace access beyond the agreed read-only profile fails this trial.
5. Record before/after host and target link, rule, route and nft dumps; diff exact objects. Foreign objects must remain byte-for-byte equivalent apart from known volatile counters. Do not infer ownership from `tempest-*`, nft handles, ifindexes, marks, table IDs or fingerprints. No apply/delete runs in this trial.

## Network-changing trial — separate owner approval required

After a fresh snapshot and console/rollback rehearsal, the owner may authorize a precise VM-only test matrix. Preflight that lab names, marks, priorities, table IDs and subnets are unused. The operator seeds foreign nft tables/chains/rules (including a `tempest-*` name and recycled handle), routes, rules and links in the target namespace. Compare inventory and protected Agent journal before diff, immediately before any future apply, and after. Simulate process restart with a missing receipt, then namespace/link recreation and handle/ifindex reuse. Each unproven or changed object must block adoption/mutation. If an exact owned-object mutation backend is later authorized, verify it never alters these foreign objects, the host namespace, default route, DNS, SSH/console path or Docker-managed state. Test failure cleanup deletes only explicitly created lab objects by exact identity; restore the VM snapshot if baseline cannot be proven. Never use `nft flush ruleset`, table-wide route flush, broad rule deletion or host route replacement.

No current TEMPEST code is authorized to run this network-changing trial. Record command log, before/after dumps, Agent capability masks, namespace identity, failed conflicts, preserved foreign objects, cleanup and snapshot restore result. Gate 2 needs later apply/rollback and control-channel evidence beyond this inventory slice.
