# Proposed first TEMPEST-owned operation: one nftables table

**Status:** preparation for a possible first task 2.6 slice only. No task 2.6 code is authorized or implemented. This note does not accept task 2.5, Gate 2, or ADR-001.

## Narrow operation

Create one empty `inet` nftables table with a validated `tempest-` name inside the operator-prepared `tempest-ns-lab` namespace. Read it back from the kernel, persist an Agent-protected creation receipt, and support exact rollback of that empty table. No chains, hooks, rules, routes, marks, interfaces, DNS, default-route changes, or host namespace access are included.

The table name prefix is a naming convention only. The collector and registry must continue treating any pre-existing table, including `tempest-*`, as foreign unless a receipt proves creation continuity.

## Proposed Agent contract

1. **Preflight:** Agent resolves one configured namespace identity (boot ID, netns device/inode) established outside the request. Verify current process namespace equals that identity. Require the narrowly scoped network capability in that namespace; fail before mutation if identity or capability checks fail. Verify complete inventory and that the exact `(family=inet, table=name)` selector is absent. Re-run the check immediately before creation under a single Agent transaction lock.
2. **Record intent:** generate a transaction UUID and persist a pending record through the Agent-only journal before mutation. The record binds schema version, namespace identity, generation/base generation, exact table selector and canonical intended state (empty table). Control Plane cannot choose a filesystem path or provide a proof.
3. **Create:** issue one typed operation through a fixed nftables API/argv builder, with no shell or arbitrary nft text. Create only the exact table in the already-selected namespace. No base chain or hook is permitted.
4. **Verify:** enumerate the exact table from the kernel and require exactly one matching table with no chains, sets, maps, flowtables, or rules. Bind a canonical observed fingerprint to the pending transaction and namespace. Any mismatch enters degraded state; do not adopt or broadly delete.
5. **Receipt:** atomically persist an Agent-only receipt containing transaction/generation IDs, boot and namespace identity, family/name selector, canonical empty-state fingerprint, creation/verification timestamps, and receipt schema version. Restrict the journal directory and file ownership/modes to the Agent. Confirm only after the receipt is durable and read-back validated.
6. **Rollback:** take the transaction lock; verify current boot/namespace identity; read the table again; require the exact selector, matching receipt fingerprint, and still-empty contents. Delete by exact family/name only. Read back and confirm absence, then atomically mark the receipt rolled back. On any uncertainty or new child object, leave it untouched and report degraded; never use `flush ruleset` or delete a populated table.

## Crash and ownership limits

There is an unavoidable crash window between kernel creation and durable receipt commit. If restart finds a pending intent and a table exists without a complete verified receipt, it must classify it as ambiguous/foreign, preserve it, and refuse automatic deletion. This can leave an empty orphan table for operator recovery; safety takes priority over automatic cleanup. The design must not claim ownership from the `tempest-` name, matching fingerprint, netlink handle, or pending intent alone.

Rollback ownership is limited to a table that remains empty and matches the protected receipt. If another actor adds children, rollback must stop rather than deleting them with table-wide deletion. The first slice therefore proves Agent-created table continuity only under the Agent's exclusive-operation assumptions; it does not prove safe ownership of later-added child objects or system-wide network safety.

## Current implementation blockers

- `NetworkObjectRegistry` depends on `InMemoryNetworkInventory`; it has no production reader/mutator interface, durable receipt store, or kernel receipt verification.
- `linux_inventory.py` parses nft JSON for conflict checks, but `compare_agent_receipts()` deliberately rejects all nonempty receipt sets; the collector is an operator CLI and not an Agent backend.
- AF_UNIX `AgentServer` dispatches only to `MockNetworkAgent`. The IPC client deliberately raises `NetworkApplyUnsupported`; NetworkPlan v1 does not express a typed create-table operation.
- The shipped systemd unit has empty capabilities, only `/run/tempest-agent` writable, and `RestrictAddressFamilies=AF_UNIX`. The target namespace, minimal netlink/nft access, and persistent Agent-only state path are not yet provisioned or experimentally validated.
- No evidence yet establishes the prepared namespace's actual isolation, usable nft enumeration under the intended Agent identity, exact rollback behavior, or recovery semantics on the target Ubuntu VM. The separately authorized foreign-object experiment remains unrun in this sandbox.

## Smallest review/implementation boundary

Before writing 2.6 code, review this contract and authorize that slice. Its first implementation should add only a typed table-create operation, isolated real backend, Agent-only durable journal/receipt, read-back verification, and exact empty-table rollback, with fault injection at each journal/kernel boundary. It must run only in the pre-created disposable namespace on the isolated VM. Keep existing v1 IPC schemas stable unless the user explicitly approves a versioned extension. Do not extend it to chains/rules/routes or production deployment in the same slice.
