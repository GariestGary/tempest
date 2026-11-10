"""In-memory NetworkAgent contract implementation for tests and dev only."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Mapping
from uuid import UUID

from pydantic import ValidationError

from .models import (
    AgentStatus,
    ApplyRequest,
    ConfirmRequest,
    DiffRequest,
    EnsureInterface,
    IPCError,
    IPCRequest,
    IPCResponse,
    IPCProtocolError,
    NetworkDiff,
    NetworkPlan,
    ReadStatusRequest,
    RollbackRequest,
    SetEgressPolicy,
    StageRequest,
    ValidateRequest,
    ValidationResult,
    VerifyRequest,
    parse_ipc_request,
)


class NetworkAgentError(RuntimeError):
    code = "invalid_plan"


class PlanConflict(NetworkAgentError):
    code = "conflict"


class OwnershipConflict(NetworkAgentError):
    code = "ownership_conflict"


class GenerationNotFound(NetworkAgentError):
    code = "not_found"


@dataclass(frozen=True)
class _Snapshot:
    generation: int
    generation_id: UUID | None
    fingerprint: str | None
    objects_fingerprint: str
    objects: dict[str, dict[str, object]]


@dataclass(frozen=True)
class _Pending:
    snapshot: _Snapshot
    deadline_epoch_ms: int
    verified: bool = False


class MockNetworkAgent:
    """A privilege-free state machine; this class never calls the host OS."""

    def __init__(
        self,
        *,
        foreign_objects: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._foreign_objects = dict(foreign_objects or {})
        empty_objects: dict[str, dict[str, object]] = {}
        empty = _Snapshot(
            generation=0,
            generation_id=None,
            fingerprint=None,
            objects_fingerprint=_objects_fingerprint(empty_objects),
            objects=empty_objects,
        )
        self._active = empty
        self._last_known_good = empty
        self._pending: _Pending | None = None
        self._staged: dict[UUID, tuple[NetworkPlan, str]] = {}
        self._generation_claims: dict[int, tuple[UUID, str]] = {}
        self._highest_generation = 0
        self._rolled_back: set[UUID] = set()
        self._last_error: str | None = None
        self.audit_log: list[dict[str, object]] = []

    def validate(self, plan: NetworkPlan) -> ValidationResult:
        with self._lock:
            self._validate_references_and_ownership(plan)
            return ValidationResult(
                generation_id=plan.generation_id,
                fingerprint=plan.fingerprint(),
                operation_count=len(plan.operations),
                permitted_egress_count=sum(
                    1 for candidate in plan.egress_candidates if candidate.enabled
                ),
            )

    def diff(self, plan: NetworkPlan) -> NetworkDiff:
        with self._lock:
            self._expire_pending()
            self._validate_references_and_ownership(plan)
            target = plan.owned_objects()
            current = self._active.objects
            added = tuple(sorted(target.keys() - current.keys()))
            removed = tuple(sorted(current.keys() - target.keys()))
            changed = tuple(
                sorted(key for key in target.keys() & current.keys() if target[key] != current[key])
            )
            return NetworkDiff(
                generation_id=plan.generation_id,
                base_generation=plan.base_generation,
                target_generation=plan.generation,
                added=added,
                changed=changed,
                removed=removed,
            )

    def stage(self, plan: NetworkPlan) -> NetworkDiff:
        with self._lock:
            self._expire_pending()
            fingerprint = plan.fingerprint()
            self._validate_references_and_ownership(plan)

            if self._active.generation_id == plan.generation_id:
                if self._active.fingerprint == fingerprint:
                    return self.diff(plan)
                raise PlanConflict("generation ID was already applied with different content")
            if self._pending is not None:
                if self._pending.snapshot.generation_id == plan.generation_id:
                    if self._pending.snapshot.fingerprint == fingerprint:
                        return self.diff(plan)
                    raise PlanConflict("pending generation ID was reused with different content")
                raise PlanConflict("another generation awaits confirmation or rollback")
            if plan.base_generation != self._active.generation:
                raise PlanConflict("base_generation does not match the active generation")

            existing = self._generation_claims.get(plan.generation)
            if existing is not None:
                if existing == (plan.generation_id, fingerprint):
                    staged = self._staged.get(plan.generation_id)
                    if staged is not None and staged[1] == fingerprint:
                        return self.diff(plan)
                raise PlanConflict("generation number has already been reserved")
            if plan.generation <= self._highest_generation:
                raise PlanConflict("generation numbers must increase monotonically")

            self._generation_claims[plan.generation] = (plan.generation_id, fingerprint)
            self._highest_generation = plan.generation
            self._staged[plan.generation_id] = (plan, fingerprint)
            return self.diff(plan)

    def apply(self, generation_id: UUID) -> AgentStatus:
        with self._lock:
            self._expire_pending()
            staged = self._staged.get(generation_id)
            if staged is None:
                if self._active.generation_id == generation_id:
                    return self.read_status()
                raise GenerationNotFound("generation is not staged")
            plan, fingerprint = staged

            if self._active.generation_id == generation_id:
                if self._active.fingerprint == fingerprint:
                    return self.read_status()
                raise PlanConflict("generation ID was applied with different content")
            if self._pending is not None:
                if self._pending.snapshot.generation_id == generation_id:
                    return self.read_status()
                raise PlanConflict("another generation awaits confirmation or rollback")
            if plan.base_generation != self._active.generation:
                raise PlanConflict("active generation changed after stage")

            candidate = _Snapshot(
                generation=plan.generation,
                generation_id=plan.generation_id,
                fingerprint=fingerprint,
                objects_fingerprint=_objects_fingerprint(plan.owned_objects()),
                objects=plan.owned_objects(),
            )
            deadline = int((self._clock() + plan.confirmation_timeout_seconds) * 1000)
            self._active = candidate
            self._pending = _Pending(snapshot=candidate, deadline_epoch_ms=deadline)
            self._staged.pop(generation_id, None)
            self._last_error = None
            return self.read_status()

    def confirm(self, generation_id: UUID) -> AgentStatus:
        with self._lock:
            self._expire_pending()
            if self._pending is None:
                if self._active.generation_id == generation_id:
                    # Confirm is idempotent after this exact generation committed.
                    return self.read_status()
                raise GenerationNotFound("generation is not awaiting confirmation")
            if self._pending.snapshot.generation_id != generation_id:
                raise PlanConflict("confirmation generation does not match pending apply")
            if not self._pending.verified:
                raise PlanConflict("generation must pass Verify before Confirm")
            # Verify permission is only a point-in-time observation. Re-check
            # the immutable candidate at the commit boundary as well, so a
            # stale approval can never promote a changed active snapshot.
            if not self._snapshot_is_intact(self._active, self._pending.snapshot):
                self._pending = replace(self._pending, verified=False)
                raise PlanConflict("active snapshot differs from pending generation")
            self._last_known_good = self._pending.snapshot
            self._active = self._pending.snapshot
            self._pending = None
            self._last_error = None
            return self.read_status()

    def verify(self, generation_id: UUID) -> AgentStatus:
        """Check mock transaction consistency; this performs no network probe."""

        with self._lock:
            self._expire_pending()
            if self._pending is None or self._pending.snapshot.generation_id != generation_id:
                raise GenerationNotFound("generation is not awaiting verification")
            if not self._snapshot_is_intact(self._active, self._pending.snapshot):
                # A failed re-verification revokes this generation's prior approval.
                if self._pending.verified:
                    self._pending = replace(self._pending, verified=False)
                raise PlanConflict("active snapshot differs from pending generation")
            if not self._pending.verified:
                self._pending = replace(self._pending, verified=True)
            return self.read_status()

    def rollback(self, generation_id: UUID, reason: str) -> AgentStatus:
        del reason  # Recorded by a real Agent; the mock exposes state only.
        with self._lock:
            self._expire_pending()
            if self._pending is None:
                if generation_id in self._rolled_back:
                    return self.read_status()
                raise GenerationNotFound("generation is not awaiting rollback")
            if self._pending.snapshot.generation_id != generation_id:
                raise PlanConflict("rollback generation does not match pending apply")
            self._active = self._last_known_good
            self._rolled_back.add(generation_id)
            self._pending = None
            self._last_error = "rollback requested"
            return self.read_status()

    def read_status(self) -> AgentStatus:
        with self._lock:
            self._expire_pending()
            pending_id = self._pending.snapshot.generation_id if self._pending else None
            if pending_id is not None:
                state = "pending_confirmation"
            elif self._last_error:
                state = "degraded"
            else:
                state = "idle"
            return AgentStatus(
                state=state,
                active_generation=self._active.generation,
                active_generation_id=self._active.generation_id,
                last_known_good_generation=self._last_known_good.generation,
                pending_generation_id=pending_id,
                confirmation_deadline_epoch_ms=(
                    self._pending.deadline_epoch_ms if self._pending else None
                ),
                owned_object_ids=tuple(sorted(self._active.objects)),
                last_error=self._last_error,
            )

    def handle_request(self, raw: bytes | str | IPCRequest) -> IPCResponse:
        """Dispatch a typed protocol request without providing an OS socket.

        Transport authentication is intentionally separate and must supply a
        kernel-verified peer identity before production dispatch is enabled.
        """

        try:
            request = raw if not isinstance(raw, (bytes, str)) else parse_ipc_request(raw)
        except IPCProtocolError as exc:
            raise

        try:
            if isinstance(request, ValidateRequest):
                result = self.validate(request.plan)
            elif isinstance(request, StageRequest):
                result = self.stage(request.plan)
            elif isinstance(request, DiffRequest):
                result = self.diff(request.plan)
            elif isinstance(request, ApplyRequest):
                result = self.apply(request.generation_id)
            elif isinstance(request, VerifyRequest):
                result = self.verify(request.generation_id)
            elif isinstance(request, ConfirmRequest):
                result = self.confirm(request.generation_id)
            elif isinstance(request, RollbackRequest):
                result = self.rollback(request.generation_id, request.reason)
            elif isinstance(request, ReadStatusRequest):
                result = self.read_status()
            else:  # The discriminated protocol union makes this unreachable.
                raise NetworkAgentError("unsupported request type")
            self._audit(request, outcome="ok")
            return IPCResponse(request_id=request.request_id, ok=True, result=result)
        except NetworkAgentError as exc:
            self._audit(request, outcome=exc.code)
            return IPCResponse(
                request_id=request.request_id,
                ok=False,
                error=IPCError(code=exc.code, message=str(exc)[:300]),
            )
        except ValidationError as exc:
            self._audit(request, outcome="invalid_plan")
            return IPCResponse(
                request_id=request.request_id,
                ok=False,
                error=IPCError(code="invalid_plan", message=str(exc)[:300]),
            )

    def _validate_references_and_ownership(self, plan: NetworkPlan) -> None:
        interfaces = {
            operation.object.object_id
            for operation in plan.operations
            if isinstance(operation, EnsureInterface)
        }
        for operation in plan.operations:
            referenced_interface = getattr(operation, "interface_id", None)
            if referenced_interface and referenced_interface not in interfaces:
                raise NetworkAgentError("operation references an interface absent from the plan")
            if isinstance(operation, SetEgressPolicy) and operation.verdict == "allow":
                if operation.egress_id is None:
                    raise NetworkAgentError("allow policy has no egress")
            object_id = getattr(getattr(operation, "object", None), "object_id", None)
            if object_id in self._foreign_objects:
                owner = self._foreign_objects[object_id]
                raise OwnershipConflict(f"object ID conflicts with foreign owner {owner!r}")

    @staticmethod
    def _snapshot_is_intact(active: _Snapshot, pending: _Snapshot) -> bool:
        return (
            active.generation == pending.generation
            and active.generation_id == pending.generation_id
            and active.fingerprint == pending.fingerprint
            and active.objects_fingerprint == pending.objects_fingerprint
            and _objects_fingerprint(active.objects) == pending.objects_fingerprint
        )

    def _expire_pending(self) -> None:
        if self._pending is None:
            return
        if int(self._clock() * 1000) < self._pending.deadline_epoch_ms:
            return
        expired_id = self._pending.snapshot.generation_id
        self._active = self._last_known_good
        self._pending = None
        if expired_id is not None:
            self._rolled_back.add(expired_id)
        self._last_error = "confirmation deadline expired; last-known-good restored"

    def _audit(self, request: IPCRequest, *, outcome: str) -> None:
        generation_id = getattr(request, "generation_id", None)
        if generation_id is None and hasattr(request, "plan"):
            generation_id = request.plan.generation_id
        self.audit_log.append(
            {
                "request_id": str(request.request_id),
                "method": request.method,
                "generation_id": str(generation_id) if generation_id else None,
                "outcome": outcome,
                # No actor is accepted from IPC JSON; production transport must
                # populate the verified peer identity outside this mock.
                "peer_uid": None,
                "peer_pid": None,
            }
        )


def _objects_fingerprint(objects: dict[str, dict[str, object]]) -> str:
    payload = json.dumps(objects, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
