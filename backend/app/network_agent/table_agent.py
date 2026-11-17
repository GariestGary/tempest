"""Durable Agent transaction for the empty nftables table lab plan."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from .linux_inventory import InventoryError
from .models import AgentStatus, IPCError, NetworkDiff
from .nft_table import NftTable
from .registry import RegistryConflict
from .table_journal import TableJournal, TableRecord
from .table_plan import (
    ApplyTableRequest, RollbackTableRequest, StageTableRequest, TablePlan,
    TableRequest, TableResponse,
)


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
        evidence = self._kernel.verify(record.plan, record.transaction_id)
        if (record.evidence is None or evidence.handle != record.evidence.handle
                or evidence.fingerprint != record.evidence.fingerprint):
            raise InventoryError("table ownership receipt no longer matches the kernel")

    def _status(self, record: TableRecord | None) -> AgentStatus:
        if self._degraded is not None:
            return AgentStatus(
                state="degraded", active_generation=0, last_known_good_generation=0,
                last_error=self._degraded,
            )
        if record is None or record.phase == "rolled_back":
            return AgentStatus(state="idle", active_generation=0, last_known_good_generation=0)
        if record.phase != "owned":
            raise InventoryError("unfinished table operation requires operator recovery")
        self._verify_owned(record)
        return AgentStatus(
            state="idle", active_generation=record.plan.generation,
            active_generation_id=record.plan.generation_id,
            last_known_good_generation=record.plan.generation,
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
        if record is not None and record.phase == "owned" and record.plan.generation_id == generation_id:
            return self._status(record)
        plan = self._staged
        if plan is None or plan.generation_id != generation_id:
            raise RegistryConflict("apply requires the staged generation")
        self._kernel.require_absent(plan)
        pending = TableRecord(
            transaction_id=uuid4(), namespace=self._kernel.namespace, plan=plan,
            phase="create_pending", intent_at=datetime.now(timezone.utc),
        )
        self._journal.write(pending)
        self._staged = None
        try:
            # The exclusive kernel create closes the remaining race with this check.
            self._kernel.require_absent(plan)
            self._kernel.create(pending.transaction_id)
            created_at = datetime.now(timezone.utc)
            evidence = self._kernel.verify(plan, pending.transaction_id)
            owned = pending.model_copy(update={
                "phase": "owned", "created_at": created_at, "evidence": evidence,
            })
            self._journal.write(owned)
        except RegistryConflict as exc:
            raise InventoryError("creation continuity is uncertain after recording intent") from exc
        return self._status(owned)

    def _rollback(self, generation_id: UUID, record: TableRecord | None) -> AgentStatus:
        if record is None or record.plan.generation_id != generation_id:
            raise RegistryConflict("rollback requires the receipt's generation")
        if record.phase == "owned":
            self._verify_owned(record)
            pending = record.model_copy(update={"phase": "rollback_pending"})
            self._journal.write(pending)
            self._verify_owned(pending)
            self._kernel.delete(record.evidence.handle)
            record = pending
        try:
            self._kernel.require_absent(record.plan)
        except RegistryConflict as exc:
            raise InventoryError("rollback cannot establish table absence") from exc
        if record.phase == "rollback_pending":
            record = record.model_copy(update={"phase": "rolled_back"})
            self._journal.write(record)
        return self._status(record)

    def handle_request(self, request: TableRequest) -> TableResponse:
        try:
            self._kernel.preflight()
            record = self._record()
            if self._degraded is not None:
                if request.method == "read_status":
                    result = self._status(record)
                else:
                    raise InventoryError(self._degraded)
            elif record is not None and record.phase in {"create_pending", "rollback_pending"}:
                # A restart never deletes anything. An interrupted rollback can
                # complete its journal transition if a full read proves absence.
                if record.phase == "create_pending":
                    raise InventoryError("creation intent has no verified receipt; preserve any table")
                try:
                    self._kernel.require_absent(record.plan)
                except RegistryConflict as exc:
                    raise InventoryError("interrupted rollback has an occupied selector") from exc
                record = record.model_copy(update={"phase": "rolled_back"})
                self._journal.write(record)
                if request.method != "read_status":
                    raise RegistryConflict("rollback recovery completed; read status and retry")
                result = self._status(record)
            elif isinstance(request, StageTableRequest):
                result = self._stage(request.plan, record)
            elif isinstance(request, ApplyTableRequest):
                result = self._apply(request.generation_id, record)
            elif isinstance(request, RollbackTableRequest):
                result = self._rollback(request.generation_id, record)
            else:
                result = self._status(record)
            return TableResponse(request_id=request.request_id, ok=True, result=result)
        except (InventoryError, OSError, ValueError) as exc:
            if self._degraded is None:
                self._degraded = (
                    str(exc) if isinstance(exc, InventoryError)
                    else "Agent could not verify the journal or kernel operation"
                )
            if request.method == "read_status":
                return TableResponse(request_id=request.request_id, ok=True, result=self._status(None))
            error = IPCError(code="ownership_conflict", message=self._degraded)
        except RegistryConflict as exc:
            error = IPCError(code="conflict", message=str(exc))
        return TableResponse(request_id=request.request_id, ok=False, error=error)
