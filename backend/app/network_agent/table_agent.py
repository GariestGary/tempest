"""Durable Agent transaction for the empty nftables table lab plan."""

import logging
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .linux_inventory import InventoryError
from .models import AgentStatus, IPCError, NetworkDiff
from .nft_table import NftTable
from .registry import RegistryConflict
from .table_journal import PENDING_CONFIRMATION, TableJournal, TableRecord
from .table_plan import (
    ApplyTableRequest, ConfirmTableRequest, RollbackTableRequest, StageTableRequest,
    TablePlan, TableRequest, TableResponse, VerifyTableRequest,
)

LOG = logging.getLogger(__name__)
OWNED_PHASES = PENDING_CONFIRMATION | {"confirmed", "owned"}


def _boot_ms() -> int:
    # The journal's boot identity must match before we compare this clock.
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME) // 1_000_000


class TableAgent:
    """AgentServer serializes requests and the journal locks out other processes."""

    def __init__(self, kernel: NftTable, journal: TableJournal) -> None:
        self._kernel = kernel
        self._journal = journal
        self._staged: TablePlan | None = None
        self._degraded: str | None = None
        self._kernel.preflight()

    def _record(self) -> TableRecord | None:
        record = self._journal.read()
        if record is not None and (
            record.namespace != self._kernel.namespace
            or record.plan.operations != (self._kernel.operation,)
        ):
            raise InventoryError("journal belongs to a different boot, namespace or table")
        return record

    def _verify_owned(self, record: TableRecord) -> None:
        if record.evidence is None:
            raise InventoryError("table ownership receipt is incomplete")
        evidence = self._kernel.verify(record.plan, record.transaction_id)
        if (evidence.handle != record.evidence.handle
                or evidence.fingerprint != record.evidence.fingerprint):
            raise InventoryError("table ownership receipt no longer matches the kernel")

    def _degrade(self, exc: Exception) -> None:
        if self._degraded is None:
            self._degraded = (
                str(exc) if isinstance(exc, InventoryError)
                else "Agent could not verify the journal or kernel operation"
            )
            LOG.warning("Lab table transaction degraded: %s", self._degraded)

    def _status(self, record: TableRecord | None) -> AgentStatus:
        if self._degraded is not None:
            return AgentStatus(
                state="degraded", active_generation=0, last_known_good_generation=0,
                last_error=self._degraded,
            )
        if record is None or record.phase == "rolled_back":
            return AgentStatus(state="idle", active_generation=0, last_known_good_generation=0)
        pending = record.phase in PENDING_CONFIRMATION
        return AgentStatus(
            state="pending_confirmation" if pending else "idle",
            active_generation=record.plan.generation,
            active_generation_id=record.plan.generation_id,
            last_known_good_generation=record.plan.generation if record.phase == "confirmed" else 0,
            pending_generation_id=record.plan.generation_id if pending else None,
            confirmation_deadline_epoch_ms=record.deadline_epoch_ms if pending else None,
            owned_object_ids=(self._kernel.operation.name,),
        )

    def _stage(self, plan: TablePlan, record: TableRecord | None) -> NetworkDiff:
        if plan.operations != (self._kernel.operation,) or plan.base_generation != 0:
            raise RegistryConflict("plan must create the configured lab table from an empty base")
        if record is not None and (
            record.phase != "rolled_back" or plan.generation <= record.plan.generation
            or plan.generation_id == record.plan.generation_id
        ):
            raise RegistryConflict("plan generation conflicts with the durable journal")
        if self._staged is not None and (
            plan.generation < self._staged.generation
            or ((plan.generation_id == self._staged.generation_id
                 or plan.generation == self._staged.generation) and plan != self._staged)
        ):
            raise RegistryConflict("plan generation conflicts with the staged plan")
        self._kernel.require_absent(plan)
        self._staged = plan
        return NetworkDiff(
            generation_id=plan.generation_id, target_generation=plan.generation,
            base_generation=plan.base_generation, added=(self._kernel.operation.name,),
        )

    def _apply(self, generation_id: UUID, record: TableRecord | None) -> AgentStatus:
        if record is not None and record.phase in OWNED_PHASES and record.plan.generation_id == generation_id:
            self._verify_owned(record)
            return self._status(self._expire(record))
        plan = self._staged
        if plan is None or plan.generation_id != generation_id:
            raise RegistryConflict("apply requires the staged generation")
        self._kernel.require_absent(plan)
        intent = TableRecord(
            schema_version=2, transaction_id=uuid4(), namespace=self._kernel.namespace, plan=plan,
            phase="create_pending", intent_at=datetime.now(timezone.utc),
        )
        self._journal.write(intent)
        self._staged = None
        try:
            # The exclusive kernel create closes the remaining race with this check.
            self._kernel.require_absent(plan)
            self._kernel.create(intent.transaction_id)
            created_at = datetime.now(timezone.utc)
            evidence = self._kernel.verify(plan, intent.transaction_id)
        except RegistryConflict as exc:
            raise InventoryError("creation continuity is uncertain after recording intent") from exc
        timeout_ms = plan.confirmation_timeout_seconds * 1000
        pending = intent.model_copy(update={
            "phase": "pending_confirmation", "created_at": created_at, "evidence": evidence,
            "deadline_boot_ms": _boot_ms() + timeout_ms,
            "deadline_epoch_ms": time.time_ns() // 1_000_000 + timeout_ms,
        })
        self._journal.write(pending)
        return self._status(self._expire(pending))

    def _verify_or_confirm(self, record: TableRecord, *, confirm: bool) -> AgentStatus:
        if record.phase not in PENDING_CONFIRMATION | {"confirmed"}:
            raise RegistryConflict("generation has no confirmable receipt")
        if confirm and record.phase == "pending_confirmation":
            raise RegistryConflict("generation must pass Verify before Confirm")
        self._verify_owned(record)
        record = self._expire(record)
        if record.phase == "rolled_back":
            raise RegistryConflict("table confirmation deadline expired")
        phase = "confirmed" if confirm else "verified"
        if record.phase not in {phase, "confirmed"}:
            record = record.model_copy(update={"phase": phase})
            self._journal.write(record)
        return self._status(record)

    def _finish_rollback(self, record: TableRecord) -> TableRecord:
        try:
            self._kernel.require_absent(record.plan)
        except RegistryConflict as exc:
            raise InventoryError("rollback cannot establish table absence; preserve any table") from exc
        if record.phase == "rollback_pending":
            record = record.model_copy(update={"phase": "rolled_back"})
            self._journal.write(record)
        return record

    def _rollback(self, record: TableRecord) -> TableRecord:
        if record.phase == "rolled_back":
            return self._finish_rollback(record)
        if record.phase not in OWNED_PHASES or record.evidence is None:
            raise InventoryError("rollback requires a complete ownership receipt")
        self._verify_owned(record)
        pending = record.model_copy(update={"phase": "rollback_pending"})
        self._journal.write(pending)
        self._verify_owned(pending)
        self._kernel.delete(record.evidence.handle)
        return self._finish_rollback(pending)

    def _expire(self, record: TableRecord) -> TableRecord:
        if record.phase in PENDING_CONFIRMATION:
            if record.deadline_boot_ms is None or record.deadline_epoch_ms is None:
                raise InventoryError("pending table receipt has no confirmation deadline")
            if _boot_ms() >= record.deadline_boot_ms:
                return self._rollback(record)
        return record

    def _recover(self) -> TableRecord | None:
        record = self._record()
        if record is None:
            return None
        if record.phase == "create_pending":
            raise InventoryError("creation intent has no verified receipt; preserve any table")
        if record.phase == "rollback_pending":
            # The delete outcome is unknown. Absence can complete the journal
            # transition without repeating the mutation.
            return self._finish_rollback(record)
        return self._expire(record)

    def maintain(self) -> None:
        """Recover or expire transactions on the listener thread, even without IPC."""
        if self._degraded is None:
            try:
                self._recover()
            except (InventoryError, OSError, ValueError) as exc:
                self._degrade(exc)

    def handle_request(self, request: TableRequest) -> TableResponse:
        try:
            if self._degraded is not None:
                raise InventoryError(self._degraded)
            self._kernel.preflight()
            record = self._recover()
            if isinstance(request, StageTableRequest):
                result = self._stage(request.plan, record)
            elif isinstance(request, ApplyTableRequest):
                result = self._apply(request.generation_id, record)
            elif isinstance(request, (VerifyTableRequest, ConfirmTableRequest, RollbackTableRequest)):
                if record is None or record.plan.generation_id != request.generation_id:
                    raise RegistryConflict("request requires the receipt's generation")
                if isinstance(request, RollbackTableRequest):
                    result = self._status(self._rollback(record))
                else:
                    result = self._verify_or_confirm(record, confirm=isinstance(request, ConfirmTableRequest))
            else:
                if record is not None and record.phase in OWNED_PHASES:
                    self._verify_owned(record)
                    record = self._expire(record)
                result = self._status(record)
            return TableResponse(request_id=request.request_id, ok=True, result=result)
        except (InventoryError, OSError, ValueError) as exc:
            self._degrade(exc)
            if request.method == "read_status":
                return TableResponse(request_id=request.request_id, ok=True, result=self._status(None))
            error = IPCError(code="ownership_conflict", message=self._degraded)
        except RegistryConflict as exc:
            error = IPCError(code="conflict", message=str(exc))
        return TableResponse(request_id=request.request_id, ok=False, error=error)
