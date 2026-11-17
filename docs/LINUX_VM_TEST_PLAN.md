# Task 2.5 — isolated Linux VM test plan

**Status (2026-11-17).** The single authorized rerun passed in `tempest-lab-ubuntu` on `b3bb6399ff33524569c62feb234d8a80a3483667` after offline disk recovery. The trial detected three foreign objects, rejected their ownership claims and removed those objects by exact selectors. The target inventory returned to its 10-object baseline. Task 2.5 is `READY_FOR_REVIEW`. Gate 2 is `OPEN` and ADR-001 remains `PROPOSED`.

The trial created an **empty nft table only**; it did not create a chain or nft rule. It also created one temporary IPv4 fwmark `ip rule` and one blackhole route. Therefore the trial covers enumeration and foreign-claim rejection for an empty nft table, one rule selector, and one route slot. It does not cover nft chain/rule enumeration, hooked chains, expression parsing, counters, mutation by TEMPEST, or ownership receipts. The private evidence directory was `/tmp/tempest-foreign-f53e7e5e.U8Rsoc` inside the VM; raw feeds must not be copied into Git.

## VM and evidence boundary

Use a disposable Ubuntu Server x86_64 VM with 2 vCPU, 4 GiB RAM, 20 GiB disk, one virtual NIC on an isolated lab network, hypervisor console access independent of SSH, and a snapshot taken before each trial. Keep it off production bridges and VORTEX. Install pinned Python 3.11, `iproute2`, `nftables`, systemd and test dependencies through normal VM provisioning; record their exact versions, kernel version, VM ID and snapshot ID. The operator creates the target namespace before trials. The application and Agent never create or enter namespaces themselves. Before `--execute`, the operator must confirm **outside the guest VM** that the named backup/snapshot exists, restoration is possible (preferably rehearsed), and the independent hypervisor console is available. The guest cannot technically verify the hypervisor snapshot or console, so do not claim an automated snapshot check. Keep a known-good console path and recovery snapshot. Redact addresses/secrets before sharing reports.

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

## Recorded foreign-object trial — 2026-11-17

The operator ran `foreign-ownership-trial.sh --execute` in disposable Ubuntu VM `tempest-lab-ubuntu`, only inside `tempest-ns-lab`, against commit `405391dd3f96c0d5f6be05d71f94e0868cde1b2b`. PID 1 namespace identity was `5:4026531833`; target namespace identity was `5:4026532548`. Raw evidence remains private inside the VM at `/tmp/tempest-foreign-f53e7e5e.U8Rsoc` and must not be copied into Git.

Before seed the target inventory had 6 unproven objects; after seed it had 9. The parser discovered all three additions and rejected all three foreign claims. Cleanup removed the temporary nft table, IPv4 rule and route. The target inventory returned to six objects and all six target feeds matched baseline byte-for-byte. Host feeds matched except `route6.json`: its IPv6 RA default route `expires` changed from 65329 to 65328. The initial bytewise check reported `PID 1 namespace changed: route6`; it was a dynamic timer countdown, not a namespace change. The semantic comparator now permits only a decreasing `expires` value on IPv6 RA routes, bounded by measured capture duration plus 2 seconds, and reports each accepted drift separately. Destination, gateway, interface, table, metric, protocol, flags, and every other route field remain strict.

**Actual trial scope:** it created an empty nft table, with no chain or nft rule, plus one IPv4 fwmark rule and one blackhole route. It therefore confirms detection and foreign-claim rejection only for those object shapes. It does not test nft chain/rule enumeration, apply by TEMPEST, proof/receipt continuity, or rollback. Do not infer that those checks passed.

## Failed tracked trial, recovery and single rerun — 2026-11-17

- **FAIL on `be5eab67b76edcc3f5f01881a99d15617ea2c033`.** The baseline had 10 unproven objects. The diagnostic capture after failure contained the baseline plus the three trial objects. The selector validator refused the blackhole route because Linux included `"flags": []`. Cleanup stopped and Registry rejection was never reached. The PID 1 RA route's `expires` increased from 65000 to 65413 over 180 s. The snapshots establish the timer change and leave its cause unverified. The evidence was archived privately on TYPHOON before shutdown.
- **Recovery.** Verified the offline QCOW2 and domain XML SHA-256 against the manifest, confirmed the defined active disk path and free space, and waited for graceful `shut off`. Saved the failed disk separately. Verified the installed copy by SHA-256 and `qemu-img check` before booting. Verified Ubuntu over SSH and an interactive login prompt on the independent hypervisor graphical console. Recreated `tempest-ns-lab` with only `lo` and no default routes. The target identity `5:4026532543` differed from PID 1 `5:4026531833`. Old trial selectors were absent from both namespaces.
- **PASS on `b3bb6399ff33524569c62feb234d8a80a3483667`.** Targeted pytest returned 134 passed on both TYPHOON and the VM. `bash -n` and `--dry-run` passed. After the typed confirmation, one `--execute` detected all three additions and the Registry rejected all three claims. Exact cleanup removed the route, rule and empty table. Inventory was 10 before, 13 during and 10 after, with all six target feeds restored byte-for-byte. Five PID 1 feeds were byte-identical. In `route6`, all stable attributes matched and the only difference was the IPv6 RA countdown from 65362 to 65358 over 4.380 s. The compared snapshots showed no stable PID 1 network changes attributable to the trial. The raw captures, their manifests and the terminal log were kept outside Git. The tested commit passed [GitHub CI](https://github.com/GariestGary/tempest/actions/runs/44157909294) and [CodeQL](https://github.com/GariestGary/tempest/actions/runs/44157909311).

The route guard accepts an omitted `flags` field or an empty array. The host route comparator matches complete route records, except for a valid IPv6 RA `expires` value. It reports a countdown bounded by elapsed time plus 2 s, or a separate refresh-compatible increase. Any added or removed route, changed stable attribute, missing timer counterpart or ambiguous match fails the comparison. Independent evidence is required to attribute a timer increase to a Router Advertisement. The rerun observed only a countdown. The refresh-compatible result was verified with regression tests and offline replay of the failed trial's host snapshots.

## Remaining foreign-object coverage

Task 2.5 review should still add fixture/live inventory coverage for nft chains and rules. Unit tests prove the cleanup guard rejects changed table identity, duplicate table records, and unknown child objects; parser/registry regressions exercise the foreign nft/rule/table/route conflicts. `validate_trial_selectors()` is exercised against realistic Linux JSON fixtures for states `000`, `100`, `110`, `111` and rollback `110 → 100 → 000`. Negative tests cover a foreign rule referencing the trial table ID, priority collision, overlapping fwmark, foreign route, occupied route slot, nft table name/child collision, and duplicate trial rule. A discovered validator bug compared the trial ip rule with route objects, causing the valid `110` state to fail; the validator now compares rules only with matching trial rules. The runner's `--dry-run` and placeholder rejection also have non-mutating regressions. Before any manual rerun, use a fresh VM backup and independently verify outside the guest that the snapshot exists, can be restored, and that the hypervisor console works. The guest cannot verify hypervisor snapshots or console access. Keep all evidence private. Do not copy raw feeds into Git.

The updated tracked runner is `scripts/lab/foreign-ownership-trial.sh`. Its default `--dry-run` performs no state-changing commands. Before a manual `--execute`, verify the snapshot exists, recovery is possible, and independent hypervisor console is available outside the guest; then set the attestation environment variables as shown below. The script cannot technically inspect the hypervisor snapshot or console from inside Ubuntu.

## Manual final VM trial command

Run only from the VM's main namespace after independently confirming snapshot, restore path, isolated network and virt-manager console. Use the actual snapshot identifier; empty values and obvious placeholder strings are rejected. Do not run `--execute` as part of local CI or from TYPHOON/VORTEX.

```bash
cd ~/tempest
sudo -n env TEMPEST_VM_ID=tempest-lab-ubuntu \
TEMPEST_SNAPSHOT_ID='REPLACE_WITH_REAL_SNAPSHOT_ID' \
TEMPEST_SNAPSHOT_VERIFIED=YES \
TEMPEST_CONSOLE_VERIFIED=YES \
TEMPEST_ISOLATED_NETWORK_VERIFIED=YES \
bash scripts/lab/foreign-ownership-trial.sh --execute
```

Replace `REPLACE_WITH_REAL_SNAPSHOT_ID` with the operator-verified identifier before executing. The script prompts for a typed run confirmation, checks target and PID 1 namespace identity, verifies selectors in both namespaces, and then seeds only the empty nft table, IPv4 `ip rule`, and blackhole route. Cleanup runs in order `route → rule → nft table` and requires the table JSON to contain only that table object before deletion. If unknown descendants appear, cleanup stops and directs the operator to restore the snapshot. The output identifies IPv6 RA countdown and refresh-compatible timer increases separately.

## Read-only trial, after VM preparation

1. From the VM console, record `uname -a`, `ip -Version`, `nft --version`, `systemd --version`, `/proc/sys/kernel/random/boot_id`, and the host/target namespace links and `stat` device/inode. Confirm the target is not the host namespace. Check that the Agent process, if running, has the target namespace identity and no unexpected access to host netns, Docker socket or privileged devices.
2. Capture the six JSON outputs separately **inside the target namespace** with the collector recipe above. For host comparison, use operator-controlled read-only commands separately; never pass a host namespace identity to the collector. Save raw host and target outputs as private artifacts, noting capture start/end time, exit code and stderr for each command. A nonzero exit, missing feed, changed namespace identity, or parse error is a failed inventory. Compare both namespaces to ensure the target parser never reports host-only objects as target observations. The explicit `-4/-6` command context is required because ordinary JSON `ip rule` records, including standard `from all` rules, can omit `family`; an unqualified feed cannot safely assign family to them.
3. Run the fixture/parser backend against target outputs without loading a receipt. Expect every observation to remain `proof=None`. Compare a second full capture; changed content digest triggers a fresh review, and an identical digest does not establish atomicity or ownership. Check absence of network changes between before/after read-only captures.
4. Inspect the actual systemd unit with `systemctl cat/show`, its UID/GID, `NetworkNamespacePath`, `NoNewPrivileges`, filesystem/device restrictions, and `/proc/<pid>/status` `CapPrm/CapEff/CapBnd/CapAmb`. Compare against the expected empty-capability read-only profile. Verify socket and journal permissions through the operator account. Any capability or host namespace access beyond the agreed read-only profile fails this trial.
5. Record before/after host and target link, rule, route and nft dumps; diff exact objects. Foreign objects must remain byte-for-byte equivalent apart from known volatile counters. Do not infer ownership from `tempest-*`, nft handles, ifindexes, marks, table IDs or fingerprints. No apply/delete runs in this trial.

## Network-changing trial — separate owner approval required

After a fresh snapshot and console/rollback rehearsal, the owner may authorize a precise VM-only test matrix. Preflight that lab names, marks, priorities, table IDs and subnets are unused. The operator seeds foreign nft tables/chains/rules (including a `tempest-*` name and recycled handle), routes, rules and links in the target namespace. Compare inventory and protected Agent journal before diff, immediately before any future apply, and after. Simulate process restart with a missing receipt, then namespace/link recreation and handle/ifindex reuse. Each unproven or changed object must block adoption/mutation. If an exact owned-object mutation backend is later authorized, verify it never alters these foreign objects, the host namespace, default route, DNS, SSH/console path or Docker-managed state. Test failure cleanup deletes only explicitly created lab objects by exact identity; restore the VM snapshot if baseline cannot be proven. Never use `nft flush ruleset`, table-wide route flush, broad rule deletion or host route replacement.

No current TEMPEST code is authorized to run this network-changing trial. Record command log, before/after dumps, Agent capability masks, namespace identity, failed conflicts, preserved foreign objects, cleanup and snapshot restore result. Gate 2 needs later apply/rollback and control-channel evidence beyond this inventory slice.
