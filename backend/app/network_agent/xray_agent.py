"""Durable apply and recovery for one isolated Xray configuration."""

import hashlib
import time
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .models import AgentStatus, ContractModel, IPCError, IPCResponse, NetworkDiff, ValidationResult
from .table_journal import PrivateJournal
from .xray_profile import WireGuardKey, XrayPlan, XrayPlanRequest, XrayRequest, XrayRevokeRequest
from .xray_runtime import SystemdXray, XrayRuntimeError

_JOURNAL_LIMIT = 256 * 1024
_MAX_REVOKED = 2048


class ProfileConflict(ValueError):
    pass


class Snapshot(ContractModel):
    plan: XrayPlan
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(cls, plan: XrayPlan) -> "Snapshot":
        return cls(plan=plan, fingerprint=hashlib.sha256(plan.config_bytes()).hexdigest())

    def payload(self) -> bytes:
        data = self.plan.config_bytes()
        if hashlib.sha256(data).hexdigest() != self.fingerprint:
            raise ValueError("stored Xray configuration fingerprint cannot be reproduced")
        return data

    def keys(self) -> set[str]:
        return {peer.public_key for peer in self.plan.runtime_peers()}


class XrayRecord(ContractModel):
    version: Literal[1] = 1
    high_water: int = Field(default=0, ge=0)
    last_attempt_id: UUID | None = None
    lkg: Snapshot | None = None
    pending: Snapshot | None = None
    revoked: frozenset[WireGuardKey] = Field(default_factory=frozenset, max_length=_MAX_REVOKED)

    @model_validator(mode="after")
    def check_generations(self) -> "XrayRecord":
        if (self.high_water == 0) != (self.last_attempt_id is None):
            raise ValueError("journal is missing its generation watermark")
        if self.lkg is not None and self.lkg.plan.generation > self.high_water:
            raise ValueError("LKG exceeds its generation watermark")
        if self.pending is not None and (
            self.pending.plan.generation != self.high_water
            or self.pending.plan.generation_id != self.last_attempt_id
            or (self.lkg is not None and self.lkg.plan.generation >= self.high_water)
            or self.pending.keys() & self.revoked
        ):
            raise ValueError("journal stages disagree or the pending generation restores a revoked peer")
        return self


class XrayJournal(PrivateJournal):
    def read(self) -> XrayRecord:
        content = self.read_bytes("xray.json", _JOURNAL_LIMIT)
        return XrayRecord() if content is None else XrayRecord.model_validate_json(content)

    def write(self, record: XrayRecord) -> None:
        data = record.model_dump_json().encode()
        XrayRecord.model_validate_json(data)
        self.write_bytes("xray.json", data, _JOURNAL_LIMIT)


def _boot_time() -> float:
    return time.clock_gettime(time.CLOCK_BOOTTIME)


class XrayAgent:
    """AgentServer serializes access. Pending generations never survive Agent startup."""

    def __init__(
        self, runtime: SystemdXray, journal: XrayJournal,
        *, namespace: str, listener: tuple[str, int], clock=_boot_time,
    ) -> None:
        self._runtime = runtime
        self._journal = journal
        self._namespace = namespace
        self._listener = listener
        self._clock = clock
        self._record = XrayRecord()
        self._staged: Snapshot | None = None
        self._active: Snapshot | None = None
        self._invocation: str | None = None
        self._verified = False
        self._deadline = 0.0
        self._deadline_epoch_ms: int | None = None
        self._last_error: str | None = None
        self._journal_failed = False
        self._fatal = False
        try:
            self._stop()
            self._record = journal.read()
            for record in (self._record.lkg, self._record.pending):
                if record is not None:
                    self._check_deployment(record.plan)
            self._restore()
        except (OSError, ValueError, XrayRuntimeError):
            self._journal_failed = True
            self._close("Xray startup could not verify the deployment or journal")

    def _stop(self) -> None:
        self._runtime.stop()
        self._active = None
        self._invocation = None
        self._verified = False

    def _close(self, reason: str) -> None:
        self._last_error = reason
        try:
            self._stop()
        except (OSError, ValueError, XrayRuntimeError):
            # Systemd BindsTo stops the Xray unit when the Agent exits.
            self._fatal = True
            self._last_error = "Xray stop was not verified; Agent is exiting to stop its bound unit"

    def _write(self, record: XrayRecord) -> None:
        try:
            self._journal.write(record)
        except (OSError, ValueError):
            self._journal_failed = True
            raise
        self._record = record

    def _check_deployment(self, plan: XrayPlan) -> None:
        if plan.target_namespace_id != self._namespace or (
            str(plan.listen_address), plan.listen_port
        ) != self._listener:
            raise ProfileConflict("plan does not match the provisioned namespace and listener")

    def _safe_lkg(self) -> Snapshot | None:
        lkg = self._record.lkg
        return lkg if lkg is not None and not lkg.keys() & self._record.revoked else None

    @staticmethod
    def _runtime_payload(snapshot: Snapshot) -> bytes | None:
        return snapshot.payload() if snapshot.plan.runtime_peers() else None

    def _activate(self, snapshot: Snapshot) -> None:
        payload = self._runtime_payload(snapshot)
        invocation = self._runtime.activate(payload)
        self._runtime.verify(payload, invocation)
        self._active = snapshot
        self._invocation = invocation
        self._last_error = None

    def _restore(self) -> None:
        if self._record.pending is not None:
            self._write(self._record.model_copy(update={"pending": None}))
        lkg = self._safe_lkg()
        if lkg is None:
            if self._record.lkg is not None:
                self._last_error = "confirmed configuration contains a revoked peer; ingress is closed"
            return
        try:
            self._runtime.validate(lkg.payload())
            self._activate(lkg)
        except (OSError, ValueError, XrayRuntimeError):
            self._close("confirmed configuration could not be restored; ingress is closed")

    def _status(self) -> AgentStatus:
        active = self._active.plan if self._active else None
        pending = self._record.pending
        lkg = self._safe_lkg()
        return AgentStatus(
            state="degraded" if self._last_error else "pending_confirmation" if pending else "idle",
            active_generation=active.generation if active else 0,
            active_generation_id=active.generation_id if active else None,
            last_known_good_generation=lkg.plan.generation if lkg else 0,
            pending_generation_id=pending.plan.generation_id if pending else None,
            confirmation_deadline_epoch_ms=self._deadline_epoch_ms if pending and active else None,
            last_error=self._last_error,
            generation_watermark=self._record.high_water,
        )

    def _candidate(self, plan: XrayPlan) -> Snapshot:
        self._check_deployment(plan)
        candidate = Snapshot.create(plan)
        base = self._active.plan.generation if self._active else 0
        if self._record.pending is not None or plan.base_generation != base:
            raise ProfileConflict("base generation changed or an apply is already pending")
        if plan.generation <= self._record.high_water or plan.generation_id == self._record.last_attempt_id:
            raise ProfileConflict("candidate generation has already been used")
        if candidate.keys() & self._record.revoked:
            raise ProfileConflict("candidate restores a revoked WireGuard peer")
        return candidate

    def _diff(self, candidate: Snapshot) -> NetworkDiff:
        plan = candidate.plan
        old = self._active.plan if self._active else None
        fields = {
            "wireguard": ("tunnel_addresses", "private_key", "peers", "probe"),
            "vless": ("vless",),
            "dns": ("resolvers",),
            "destination-policy": ("rules",),
        }
        return NetworkDiff(
            generation_id=plan.generation_id, target_generation=plan.generation,
            base_generation=plan.base_generation, added=tuple(fields) if old is None else (),
            changed=tuple(
                group for group, names in fields.items()
                if old is not None and any(getattr(old, name) != getattr(plan, name) for name in names)
            ),
        )

    def _apply(self, generation: UUID) -> AgentStatus:
        if self._active is not None and self._active.plan.generation_id == generation:
            self._runtime.assert_current(self._runtime_payload(self._active), self._invocation)
            return self._status()
        if self._staged is None or self._staged.plan.generation_id != generation:
            raise ProfileConflict("apply requires a staged generation")
        candidate = self._candidate(self._staged.plan)
        try:
            self._runtime.validate(candidate.payload())
        except XrayRuntimeError:
            raise ProfileConflict("Xray rejected the configuration before apply") from None
        lkg = self._record.lkg
        removed = (lkg.keys() if lkg is not None else set()) - candidate.keys()
        intent = XrayRecord(
            high_water=candidate.plan.generation, last_attempt_id=generation, lkg=lkg,
            pending=candidate, revoked=self._record.revoked | removed,
        )
        self._stop()
        self._write(intent)
        self._staged = None
        timeout = candidate.plan.confirmation_timeout_seconds
        self._deadline = self._clock() + timeout
        self._deadline_epoch_ms = time.time_ns() // 1_000_000 + timeout * 1000
        try:
            self._activate(candidate)
        except (OSError, ValueError, XrayRuntimeError):
            self._stop()
            self._restore()
            raise ProfileConflict("candidate did not become ready; rollback was attempted") from None
        self.maintain()
        if self._record.pending is None:
            raise ProfileConflict("confirmation deadline expired during apply")
        return self._status()

    def _transition(self, method: str, generation: UUID) -> AgentStatus:
        if self._active is None or self._active.plan.generation_id != generation:
            raise ProfileConflict("request requires the active generation")
        pending = self._record.pending
        if method == "rollback":
            if pending is None:
                raise ProfileConflict("rollback requires a pending generation")
            self._stop()
            self._restore()
            return self._status()

        if method == "confirm" and pending is not None and not self._verified:
            raise ProfileConflict("generation must pass Verify before Confirm")
        if method == "verify" or (method == "confirm" and pending is not None):
            self._verified = False
            self._runtime.verify(self._runtime_payload(self._active), self._invocation)
        else:
            self._runtime.assert_current(self._runtime_payload(self._active), self._invocation)
        self.maintain()
        if not self._active or self._active.plan.generation_id != generation:
            raise ProfileConflict("confirmation deadline expired")
        if method == "confirm" and pending is not None:
            self._write(self._record.model_copy(update={"lkg": pending, "pending": None}))
        self._verified = method == "verify"
        return self._status()

    def _revoke_peer(self, request: XrayRevokeRequest) -> AgentStatus:
        """Revoke against LKG independently of any unconfirmed policy edits."""
        if self._record.pending is not None:
            raise ProfileConflict("finish or roll back the pending configuration first")
        if request.public_key in self._record.revoked:
            return self._status()
        lkg = self._record.lkg
        revoked = self._record.revoked | {request.public_key}
        if len(revoked) > _MAX_REVOKED:
            raise ProfileConflict("the revocation journal is full")
        if lkg is None or request.public_key not in lkg.keys():
            self._write(self._record.model_copy(update={"revoked": revoked}))
            return self._status()
        if lkg.plan.probe and lkg.plan.probe.peer.public_key == request.public_key:
            raise ProfileConflict("the provisioned probe peer cannot be revoked from the panel")

        generation = self._record.high_water + 1
        candidate = Snapshot.create(lkg.plan.model_copy(update={
            "peers": tuple(peer for peer in lkg.plan.peers if peer.public_key not in revoked),
            "generation": generation,
            "generation_id": request.request_id,
            "base_generation": lkg.plan.generation,
        }))
        self._stop()
        self._write(self._record.model_copy(update={
            "revoked": revoked, "high_water": generation, "last_attempt_id": request.request_id,
        }))
        try:
            self._runtime.validate(candidate.payload())
            self._activate(candidate)
        except (OSError, ValueError, XrayRuntimeError):
            self._close("peer revoked; remaining ingress could not be restored")
        else:
            self._write(self._record.model_copy(update={"lkg": candidate}))
        return self._status()

    def maintain(self) -> None:
        if self._fatal:
            raise RuntimeError("Xray Agent could not stop ingress; stopping the bound service")
        if not self._journal_failed and self._record.pending and self._clock() >= self._deadline:
            try:
                self._stop()
                self._restore()
            except (OSError, ValueError, XrayRuntimeError):
                self._close("Xray confirmation expired and recovery failed")

    def handle_request(self, request: XrayRequest) -> IPCResponse:
        ok = False
        try:
            self.maintain()
            if request.method == "read_status":
                if self._active is not None:
                    self._runtime.assert_current(self._runtime_payload(self._active), self._invocation)
                result = self._status()
            elif self._journal_failed:
                raise ProfileConflict("journal unavailable; ingress is closed")
            elif isinstance(request, XrayPlanRequest):
                candidate = self._candidate(request.plan)
                if request.method in {"stage", "validate"}:
                    try:
                        self._runtime.validate(candidate.payload())
                    except XrayRuntimeError:
                        raise ProfileConflict("Xray rejected the configuration before apply") from None
                if request.method == "stage":
                    if self._staged and (
                        candidate.plan.generation < self._staged.plan.generation
                        or (candidate != self._staged and (
                            candidate.plan.generation_id == self._staged.plan.generation_id
                            or candidate.plan.generation == self._staged.plan.generation
                        ))
                    ):
                        raise ProfileConflict("candidate conflicts with the staged generation")
                    self._staged = candidate
                result = self._diff(candidate) if request.method != "validate" else ValidationResult(
                    generation_id=candidate.plan.generation_id, fingerprint=candidate.fingerprint,
                    operation_count=len(candidate.plan.rules), permitted_egress_count=2,
                )
            elif isinstance(request, XrayRevokeRequest):
                result = self._revoke_peer(request)
            elif request.method == "recover_lkg":
                if self._record.pending is not None or self._safe_lkg() is None:
                    raise ProfileConflict("recover requires a safe confirmed generation and no pending apply")
                self._stop()
                self._restore()
                result = self._status()
            elif request.method == "apply":
                result = self._apply(request.generation_id)
            else:
                result = self._transition(request.method, request.generation_id)
            ok = True
        except ProfileConflict as exc:
            error = IPCError(code="conflict", message=str(exc))
        except (OSError, ValueError, XrayRuntimeError):
            self._close("Xray could not verify its active generation; ingress is closed")
            if self._record.pending is not None and not self._fatal and not self._journal_failed:
                try:
                    self._restore()
                except (OSError, ValueError, XrayRuntimeError):
                    self._close("Xray recovery failed; ingress is closed")
            error = IPCError(code="conflict", message="Xray runtime or journal verification failed")
        if ok:
            return IPCResponse(request_id=request.request_id, ok=True, result=result)
        return IPCResponse(request_id=request.request_id, ok=False, error=error)
