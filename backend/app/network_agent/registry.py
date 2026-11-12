"""Agent-owned network object registry and an in-memory test backend.

This module has no kernel, subprocess, socket, or filesystem operations. The
in-memory backend models atomic compare-and-swap; a future Linux backend must
provide its own independently verified inventory and mutation boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class RegistryConflict(RuntimeError):
    """A resource, generation, or observed state cannot be safely adopted."""


class ObjectKind(StrEnum):
    NFT_TABLE = "nft_table"
    NFT_CHAIN = "nft_chain"
    NFT_RULE = "nft_rule"
    IP_RULE = "ip_rule"
    ROUTING_TABLE = "routing_table"
    ROUTE = "route"
    WIREGUARD_INTERFACE = "wireguard_interface"
    TUN_INTERFACE = "tun_interface"
    MANAGED_INTERFACE = "managed_interface"


class ClaimKind(StrEnum):
    NFT_TABLE_NAME = "nft_table_name"
    NFT_CHAIN_KEY = "nft_chain_key"
    NFT_RULE_KEY = "nft_rule_key"
    IP_RULE_PRIORITY = "ip_rule_priority"
    ROUTING_TABLE_ID = "routing_table_id"
    ROUTE_KEY = "route_key"
    FWMARK = "fwmark"
    FWMARK_MASK = "fwmark_mask"
    IP_RULE_MATCH = "ip_rule_match"
    INTERFACE_NAME = "interface_name"
    INTERFACE_INDEX = "interface_index"


_REQUIRED: dict[ObjectKind, frozenset[ClaimKind]] = {
    ObjectKind.NFT_TABLE: frozenset({ClaimKind.NFT_TABLE_NAME}),
    ObjectKind.NFT_CHAIN: frozenset({ClaimKind.NFT_CHAIN_KEY}),
    ObjectKind.NFT_RULE: frozenset({ClaimKind.NFT_RULE_KEY}),
    ObjectKind.IP_RULE: frozenset({ClaimKind.IP_RULE_PRIORITY, ClaimKind.FWMARK}),
    ObjectKind.ROUTING_TABLE: frozenset({ClaimKind.ROUTING_TABLE_ID}),
    ObjectKind.ROUTE: frozenset({ClaimKind.ROUTE_KEY}),
    ObjectKind.WIREGUARD_INTERFACE: frozenset({ClaimKind.INTERFACE_NAME}),
    ObjectKind.TUN_INTERFACE: frozenset({ClaimKind.INTERFACE_NAME}),
    ObjectKind.MANAGED_INTERFACE: frozenset({ClaimKind.INTERFACE_NAME}),
}
_EXCLUSIVE_CLAIMS = frozenset(ClaimKind) - {ClaimKind.FWMARK_MASK, ClaimKind.IP_RULE_MATCH}
_OBJECT_ID = re.compile(r"^tempest-[a-z0-9][a-z0-9_.-]{0,62}$")
_NAMESPACE_ID = re.compile(r"^tempest-ns-[a-z0-9][a-z0-9_.-]{0,56}$")


@dataclass(frozen=True, order=True)
class ResourceClaim:
    kind: ClaimKind
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ClaimKind) or not self.value or self.value.strip() != self.value:
            raise ValueError("resource claim must have a typed kind and nonempty canonical value")
        if self.kind in {ClaimKind.IP_RULE_PRIORITY, ClaimKind.ROUTING_TABLE_ID, ClaimKind.FWMARK, ClaimKind.FWMARK_MASK, ClaimKind.INTERFACE_INDEX}:
            if not re.fullmatch(r"0|[1-9][0-9]*", self.value) or int(self.value) > 2**32 - 1:
                raise ValueError("numeric resource claim must be canonical")
            if self.kind in {ClaimKind.ROUTING_TABLE_ID, ClaimKind.FWMARK_MASK, ClaimKind.INTERFACE_INDEX} and self.value == "0":
                raise ValueError("table, mask and interface index must be nonzero")


@dataclass(frozen=True)
class NetworkObject:
    object_id: str
    kind: ObjectKind
    namespace_id: str
    owner: str
    generation_id: UUID
    generation: int
    claims: tuple[ResourceClaim, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not _OBJECT_ID.fullmatch(self.object_id) or not _NAMESPACE_ID.fullmatch(self.namespace_id):
            raise ValueError("object ID or namespace is outside the TEMPEST contract")
        if self.owner != "tempest" or not isinstance(self.kind, ObjectKind):
            raise ValueError("only typed TEMPEST intents may enter the registry")
        if not isinstance(self.generation_id, UUID) or self.generation < 1:
            raise ValueError("object requires a valid positive generation")
        if not re.fullmatch(r"[0-9a-f]{64}", self.fingerprint):
            raise ValueError("fingerprint must be a canonical SHA-256 digest")
        if len(set(self.claims)) != len(self.claims):
            raise ValueError("duplicate resource claim")
        object.__setattr__(self, "claims", tuple(sorted(self.claims)))
        required = _REQUIRED[self.kind]
        kinds = {claim.kind for claim in self.claims}
        optional = {ClaimKind.INTERFACE_INDEX} if self.kind in {ObjectKind.WIREGUARD_INTERFACE, ObjectKind.TUN_INTERFACE, ObjectKind.MANAGED_INTERFACE} else {ClaimKind.FWMARK_MASK, ClaimKind.IP_RULE_MATCH} if self.kind == ObjectKind.IP_RULE else frozenset()
        if not required <= kinds or kinds - required - optional:
            raise ValueError("object has missing or unsupported resource claims")


@dataclass(frozen=True)
class ObservedObject:
    object_id: str
    kind: ObjectKind
    namespace_id: str
    claims: tuple[ResourceClaim, ...]
    fingerprint: str
    proof: str | None  # Minted and retained by Agent backend; never supplied by Control Plane.
    kernel_key: str | None = None  # Parser-derived selectors, never an ownership claim.


@dataclass(frozen=True)
class InventorySnapshot:
    revision: int
    complete: bool
    objects: tuple[ObservedObject, ...]
    namespace_identity: tuple[str, int, int] | None = None


@dataclass(frozen=True)
class RegistryDiff:
    generation_id: UUID
    base_generation: int
    generation: int
    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]


class InMemoryNetworkInventory:
    """Mock kernel inventory with atomic revision checks and Agent-only proofs."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._objects: dict[str, ObservedObject] = {}
        self._revision = 0
        self._complete = True

    def snapshot(self) -> InventorySnapshot:
        with self._lock:
            return InventorySnapshot(self._revision, self._complete, tuple(self._objects.values()))

    def mark_incomplete(self, incomplete: bool = True) -> None:
        with self._lock:
            self._complete = not incomplete
            self._revision += 1

    def seed_foreign(self, obj: NetworkObject) -> None:
        """Simulate an independently discovered pre-existing foreign resource."""
        with self._lock:
            self._objects[obj.object_id] = _observed(obj, None)
            self._revision += 1

    def alter_externally(self, object_id: str, *, fingerprint: str | None = None, lose_proof: bool = False) -> None:
        with self._lock:
            old = self._objects[object_id]
            self._objects[object_id] = ObservedObject(
                old.object_id, old.kind, old.namespace_id, old.claims,
                fingerprint or old.fingerprint, None if lose_proof else old.proof,
            )
            self._revision += 1

    def apply_mock(
        self,
        expected_revision: int,
        desired: dict[str, NetworkObject],
        receipts: dict[str, ObservedObject],
    ) -> dict[str, ObservedObject]:
        """Atomic in-memory mutation; intentionally no Linux implementation."""
        with self._lock:
            if not self._complete or self._revision != expected_revision:
                raise RegistryConflict("inventory changed or is incomplete")
            current = InventorySnapshot(self._revision, self._complete, tuple(self._objects.values()))
            _validate_inventory(current, desired, receipts)
            new_objects: dict[str, ObservedObject] = {}
            for object_id, obj in desired.items():
                old = receipts.get(object_id)
                proof = old.proof if old is not None else secrets.token_hex(32)
                new_objects[object_id] = _observed(obj, proof)
            # Foreign objects are retained exactly; the registry cannot delete them.
            for object_id, observed in self._objects.items():
                if object_id not in receipts and object_id not in desired:
                    new_objects[object_id] = observed
            self._objects = new_objects
            self._revision += 1
            return {key: self._objects[key] for key in desired}


class NetworkObjectRegistry:
    """Agent-local desired/confirmed records; no IPC or NetworkPlan v1 change."""

    def __init__(self, namespace_id: str, inventory: InMemoryNetworkInventory) -> None:
        if not _NAMESPACE_ID.fullmatch(namespace_id):
            raise ValueError("invalid prepared namespace ID")
        self.namespace_id = namespace_id
        self._inventory = inventory
        self._lock = threading.RLock()
        self._generation = 0
        self._base_generation: int | None = None
        self._generation_id: UUID | None = None
        self._fingerprint: str | None = None
        self._desired: dict[str, NetworkObject] = {}
        self._confirmed: dict[str, ObservedObject] = {}

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def desired(self) -> tuple[NetworkObject, ...]:
        return tuple(self._desired.values())

    @property
    def confirmed(self) -> tuple[ObservedObject, ...]:
        return tuple(self._confirmed.values())

    def diff(
        self, generation_id: UUID, generation: int, base_generation: int,
        objects: tuple[NetworkObject, ...],
    ) -> RegistryDiff:
        with self._lock:
            desired, fingerprint = self._check_generation(generation_id, generation, base_generation, objects)
            _validate_inventory(self._inventory.snapshot(), desired, self._confirmed)
            if self._generation_id == generation_id and self._fingerprint == fingerprint:
                return RegistryDiff(generation_id, base_generation, generation, (), (), ())
            old = self._confirmed
            return RegistryDiff(
                generation_id, base_generation, generation,
                tuple(sorted(desired.keys() - old.keys())),
                tuple(sorted(key for key in desired.keys() & old.keys()
                             if desired[key].fingerprint != old[key].fingerprint)),
                tuple(sorted(old.keys() - desired.keys())),
            )

    def reconcile_mock(
        self, generation_id: UUID, generation: int, base_generation: int,
        objects: tuple[NetworkObject, ...],
    ) -> RegistryDiff:
        """Test-only registration against in-memory state; no network apply."""
        with self._lock:
            diff = self.diff(generation_id, generation, base_generation, objects)
            if self._generation_id == generation_id:
                return diff
            desired = {obj.object_id: obj for obj in objects}
            snapshot = self._inventory.snapshot()
            _validate_inventory(snapshot, desired, self._confirmed)
            receipts = self._inventory.apply_mock(snapshot.revision, desired, self._confirmed)
            self._desired = desired
            self._confirmed = receipts
            self._generation = generation
            self._base_generation = base_generation
            self._generation_id = generation_id
            self._fingerprint = _intent_fingerprint(desired)
            return diff

    def _check_generation(
        self, generation_id: UUID, generation: int, base_generation: int,
        objects: tuple[NetworkObject, ...],
    ) -> tuple[dict[str, NetworkObject], str]:
        if not isinstance(generation_id, UUID) or generation < 1 or base_generation < 0:
            raise RegistryConflict("invalid generation")
        if len({obj.object_id for obj in objects}) != len(objects):
            raise RegistryConflict("duplicate stable object ID")
        desired = {obj.object_id: obj for obj in objects}
        for obj in objects:
            if obj.namespace_id != self.namespace_id or obj.generation_id != generation_id or obj.generation != generation:
                raise RegistryConflict("object escaped namespace or generation")
        fingerprint = _intent_fingerprint(desired)
        if self._generation_id == generation_id:
            if (self._generation != generation or self._base_generation != base_generation
                    or self._fingerprint != fingerprint):
                raise RegistryConflict("generation ID reused with different intent")
        elif base_generation != self._generation or generation <= self._generation:
            raise RegistryConflict("stale or competing generation")
        return desired, fingerprint


def _observed(obj: NetworkObject, proof: str | None) -> ObservedObject:
    return ObservedObject(obj.object_id, obj.kind, obj.namespace_id, obj.claims, obj.fingerprint, proof)


def _intent_fingerprint(desired: dict[str, NetworkObject]) -> str:
    payload = [
        (key, obj.kind.value, obj.namespace_id,
         sorted((claim.kind.value, claim.value) for claim in obj.claims), obj.fingerprint)
        for key, obj in sorted(desired.items())
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _validate_inventory(
    snapshot: InventorySnapshot,
    desired: dict[str, NetworkObject],
    receipts: dict[str, ObservedObject],
) -> None:
    if not snapshot.complete:
        raise RegistryConflict("inventory is incomplete")
    observed = {obj.object_id: obj for obj in snapshot.objects}
    if len(observed) != len(snapshot.objects):
        raise RegistryConflict("inventory has duplicate object IDs")
    for object_id, receipt in receipts.items():
        current = observed.get(object_id)
        if current != receipt or current.proof is None:
            raise RegistryConflict("confirmed object lost proof or drifted")
    occupied: dict[tuple[str, ResourceClaim], str] = {}
    for obj in snapshot.objects:
        for claim in obj.claims:
            if claim.kind not in _EXCLUSIVE_CLAIMS:
                continue
            key = (obj.namespace_id, claim)
            if key in occupied and occupied[key] != obj.object_id:
                raise RegistryConflict("inventory has overlapping resource claims")
            occupied[key] = obj.object_id
    wanted: dict[tuple[str, ResourceClaim], str] = {}
    for obj in desired.values():
        current = observed.get(obj.object_id)
        if current is not None and obj.object_id not in receipts:
            raise RegistryConflict("stable ID already belongs to an unproven object")
        if obj.object_id in receipts:
            prior = receipts[obj.object_id]
            if obj.kind != prior.kind or obj.namespace_id != prior.namespace_id or obj.claims != prior.claims:
                raise RegistryConflict("resource identity cannot change under a stable ID")
        for claim in obj.claims:
            if claim.kind not in _EXCLUSIVE_CLAIMS:
                continue
            key = (obj.namespace_id, claim)
            if key in wanted and wanted[key] != obj.object_id:
                raise RegistryConflict("desired objects claim the same resource")
            if key in occupied and occupied[key] != obj.object_id:
                raise RegistryConflict("resource claim collides with an observed object")
            wanted[key] = obj.object_id
