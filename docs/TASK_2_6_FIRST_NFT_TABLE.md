# Task 2.6 · First Agent-owned nftables table

The owner authorized this lab slice on 2026-11-17 and extended it to transaction confirmation and recovery for the same table. Task 2.5 is `ACCEPTED` in its verified scope. Task 2.6 is `IN_PROGRESS`, Gate 2 is `OPEN`, and ADR-001 remains `PROPOSED`.

## Operation and contract

The lab Agent can create a single empty `inet` table at its configured `tempest-` selector in the operator-prepared `tempest-ns-lab`. The trial uses `inet tempest-first-nft-table`. The same Agent can roll back that generation by deleting the verified empty table. Table creation does not install hooks or configure forwarding.

The opt-in Agent listener shares AF_UNIX framing, SO_PEERCRED allowlists, replay protection and serialized request processing with the existing listener. It accepts a lab-only protocol v2, using the shared NetworkPlan generation and fingerprint contract. The v2 plan contains exactly one `create_nft_table` operation and must match the startup selector. It carries no journal path, shell text or ownership proof. The existing v1 listener and Control Plane stay on their existing protocol and permissions.

1. `stage` validates the exact operation, generation and complete Linux inventory. The Registry's unproven-claim check rejects any matching foreign object. The reply is a diff.
2. `apply` rechecks absence, records the intent and rechecks again under the Agent transaction lock. The fixed `nft create table` operation is exclusive in the kernel and adds a comment containing an Agent-generated transaction UUID.
3. The Agent reads back the table through Linux inventory and an exact table listing. It verifies the selector, kernel handle, transaction marker and empty contents, then stores a `pending_confirmation` receipt. The journal stores the boot/namespace identity, plan and generation IDs, transaction UUID, timestamps, handle and observation fingerprint. The Agent writes a private file, fsyncs it, atomically replaces the record, fsyncs the directory and validates the read-back.
4. `verify` takes the pending generation ID, checks the same receipt against a fresh kernel observation and writes `verified`. `confirm` requires that explicit step, checks the kernel again, checks the deadline and writes `confirmed` before reporting a last-known-good generation. The deadline starts after apply's kernel read-back. `confirmation_timeout_seconds` defaults to 60 and accepts integers from 5 through 300. Enforcement uses Linux `CLOCK_BOOTTIME` in the recorded boot. The epoch deadline in status is for display.
5. The listener checks for expiration at startup, between requests and when idle. Pending and verified receipts expire even without IPC. Timer work uses the serialized listener and may wait for bounded work already in progress. A stopped process resumes deadline enforcement when it restarts in the same boot. Fresh status or verification calls use a new request ID because an exact IPC retry can return its cached response.
6. Manual and deadline rollback both require a complete receipt and a fresh match to the kernel. The Agent records `rollback_pending`, verifies ownership again, then asks nf_tables to delete the matching `inet` handle with `NLM_F_NONREC`. The kernel checks emptiness in the same transaction as deletion, which protects a child added after the Agent's last read. The handle excludes a replacement at the same name. An absence check and a durable `rolled_back` transition complete rollback. [Linux nf_tables implementation](https://linux.googlesource.com/linux/kernel/git/torvalds/linux/+/cdb3033e191fd03da2d7da23b9cd448dfa180a8e/net/netfilter/nf_tables_api.c#1569)
7. `read_status` reports ownership only after a fresh kernel check. After a restart, `create_pending` stays degraded and preserves any possible orphan. Even an absent table leaves that intent for operator review because the pre-crash create outcome is unknown. A restart can finish `rollback_pending` after proving absence, without repeating the delete. A conflicting selector, incomplete receipt, changed boot/namespace or populated table stops cleanup and requires operator review.

The journal lives in an Agent-owned `0700` directory on persistent storage, uses a single Agent-only `0600` record and holds an exclusive process lock. Keep trusted application files and journal paths outside directories writable by the IPC peer. Ownership requires a complete durable receipt. A legacy v1 `owned` record remains usable for explicit rollback with fresh kernel proof. Its generation has no durable confirmation or deadline. This slice assumes a single trusted network operator in the prepared namespace. Boot changes require operator review. Recovery for forwarding policy remains work for the broader task.

## Disposable VM procedure

Use TYPHOON and `ssh lab`. Check the VM UUID, Ubuntu version, repo origin/branch/SHA, recovery image and independent console from the hypervisor before running the slice. Verify `sudo -n`, the boot ID, PID 1 netns device/inode, distinct `/run/netns/tempest-ns-lab` device/inode, only loopback, no other operators in that namespace, and absence of `inet tempest-first-nft-table`. Use the same checks immediately before rollback, with the expected table now present.

Enter the namespace **outside** the Agent with `sudo -n ip netns exec tempest-ns-lab`. Before starting the Agent, drop to a dedicated UID and set effective, permitted, bounding, inheritable and ambient capability masks to `CAP_NET_ADMIN` only. Set `NoNewPrivileges=1`, for example using `setpriv`. Run `python -m app.network_agent` with the existing socket and peer allowlist options and these operator-provisioned options.

- `--lab-table tempest-first-nft-table`
- `--lab-journal` and `--socket` with absolute, protected paths. The socket directory must be Agent-owned and accessible to the allowlisted client group.
- `--lab-boot-id`, `--lab-netns` and `--lab-host-netns` from the preflight. Device/inode pairs use the form `device:inode`. The reduced Agent identity cannot stat PID 1's namespace on the target VM, so the operator reads that identity and the Agent independently checks its own identity against the root-managed named mount.

From the unprivileged client, send the versioned plan with `stage`, then use the same generation ID for `apply`, `verify` and `confirm`. Capture all six inventory feeds before apply, after apply, and after rollback in the target namespace and PID 1 namespace. Require exactly one new empty target table. Check the journal permissions and `pending_confirmation → verified → confirmed` transitions. Restart the Agent in the same namespace, check the confirmed generation after its original deadline, and use explicit rollback. Check table absence and the `rolled_back` receipt. Preserve evidence privately. On ambiguity, preserve the table and the journal and stop for operator review. Do not use recursive table deletion or a ruleset flush as compensation.

Run the deadline trial with a fresh journal and verified selector absence. Stop issuing IPC requests after apply. Check that the table disappears and the receipt becomes `rolled_back` before sending another status request. In a separate approved run, insert one separately authorized inert child in the owned table before the deadline. Check that both the table and child survive and that status is degraded. For the two process-loss cases, use a disposable trial harness at the durable-intent and create-before-receipt boundaries. Compare kernel state and journal before and after process restart. Confirm the restore path independently before either process-loss case and preserve the journal and any orphan for operator review.

## Verified VM evidence · 2026-11-17

The code under test was `febf6a46f36aadb8afe0bf7df135b6f33b853132`, staged from its checked Git archive in a root-owned VM directory. The guest ran Ubuntu 26.04.1 LTS, kernel `7.0.0-43-generic`, nftables 1.1.6 and Python 3.11.18. The libvirt VM had one user-mode NIC. The named target contained only loopback and matched `5:4026532543`; PID 1 matched `5:4026531833`. A fresh full libvirt backup completed, the standalone QCOW2 passed `qemu-img check`, and the independent graphical console showed the guest TTY before apply and rollback. The backup was taken live and was checked for image integrity. No recovery boot was exercised for this image.

The first Agent process (PID 4804) accepted stage and apply from the unprivileged AF_UNIX peer `1000:1000`. The restarted Agent (PID 5027) verified the same generation `23b30b49-4b84-4d98-8e07-71e7e73db903` and completed rollback. Both Agent processes ran as UID 61112 with only `CAP_NET_ADMIN` in all five masks and `NoNewPrivs=1`. The client could not read the Agent journal as its ordinary UID. The confirmed table handle was 2 and the observation fingerprint was `89be3ac0e132783d5f0203a95f4d1568928ba67606b8444a2c2844d2588a3e93`.

| Capture | Target compared with baseline | PID 1 compared with baseline |
|---|---|---|
| After apply | Exactly one empty table added. Other five feeds byte-identical. | Six feeds byte-identical. |
| After restart and rollback | All six feeds byte-identical. Receipt was `rolled_back`. | Five feeds byte-identical. Only IPv6 RA expires changed, 65343→65318. |

Raw captures and logs remain private in the guest under `/var/tmp/tempest-task26-febf6a4-evidence`. The selected tests passed on both TYPHOON and the VM, 192 tests and 17 dependency deprecation warnings per run.

```bash
# From backend, with the selected Python 3.11 environment
python -m pytest -x -q tests/test_nft_table_agent.py tests/test_network_agent_ipc.py   tests/test_network_plan_contract.py tests/test_linux_inventory.py   tests/test_network_object_registry.py tests/test_agent_control_plane.py   tests/test_network_plan_builder.py
```

Tests simulated loss of a create acknowledgement, child insertion, receipt drift, and journal faults. The live run exercised successful creation and rollback across an Agent process restart. Actual VM reboot, live crash injection, and the kernel's rejection of rollback with a concurrently inserted child remain unverified. Production and the remaining Phase 2 tasks require separate review.

## Confirmation and failure recovery evidence · 2026-11-17

The tests use a kernel double. They exercise the authenticated Unix listener and private journal on TYPHOON. The earlier live result above applies to the first slice.

| Scenario | Targeted tests | New VM result |
|---|---|---|
| Process loss after durable intent | Degraded after restart, no creation or deletion | `BLOCKED` |
| Process loss after create and before receipt | Degraded after restart, possible orphan preserved | `BLOCKED` |
| Restart with confirmed receipt | Same generation reverified after its original deadline | `BLOCKED` |
| Deadline expires without confirmation | Rollback from an idle AF_UNIX listener and after restart | `BLOCKED` |
| Foreign child before or during rollback | Refusal before delete or simulated kernel refusal; child preserved | `BLOCKED` |

OpenSSH rejected `/etc/ssh/ssh_config.d/20-systemd-ssh-proxy.conf` when running `ssh -o BatchMode=yes -o ConnectTimeout=5 lab true`. Guest pytest, selector preflight, restore image and independent console verification did not run. No live failure-injection result is available for this change.
