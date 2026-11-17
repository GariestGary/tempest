"""Fault injection for the lab Agent; kernel mutation is always a test double."""

import json
import os
import socket
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.network_agent import service, table_agent
from app.network_agent.linux_inventory import InventoryError, NamespaceIdentity
from app.network_agent.models import IPCProtocolError, parse_ipc_request
from app.network_agent.registry import RegistryConflict
from app.network_agent.service import AgentServer, PeerPolicy, read_frame, write_frame
from app.network_agent.table_agent import TableAgent
from app.network_agent.table_journal import TableEvidence, TableJournal, TableRecord
from app.network_agent.table_plan import (
    ApplyTableRequest, ConfirmTableRequest, CreateNftTable, RollbackTableRequest, StageTableRequest,
    TablePlan, TableResponse, TableStatusRequest, VerifyTableRequest, parse_table_request,
)


@dataclass
class JournalDouble:
    record: TableRecord | None = None
    fail_phase: str | None = None

    def read(self):
        return self.record

    def write(self, record):
        if record.phase == self.fail_phase:
            raise OSError("injected journal failure")
        self.record = record


class TestKernel:
    __test__ = False

    def __init__(self, operation):
        self.namespace = NamespaceIdentity("tempest-ns-lab", str(uuid4()), 5, 23)
        self.operation = operation
        self.marker = None
        self.has_children = False
        self.handle = 5
        self.creates = 0
        self.deletes = 0
        self.fail_create = False
        self.fail_verify = False
        self.fail_delete = False
        self.fail_absence = False
        self.on_verify = lambda: None
        self.deleted = Event()
        self.events = []

    def preflight(self):
        pass

    def require_absent(self, _plan):
        if self.fail_absence:
            raise InventoryError("inventory unavailable")
        if self.marker is not None:
            raise RegistryConflict("occupied table")

    def create(self, marker):
        self.creates += 1
        self.events.append("create")
        self.marker = marker
        if self.fail_create:
            raise OSError("lost create acknowledgement")

    def verify(self, _plan, marker):
        self.on_verify()
        if self.has_children or self.marker != marker or self.fail_verify:
            raise InventoryError("table state changed")
        return TableEvidence(
            handle=self.handle, fingerprint="a" * 64, verified_at=datetime.now(timezone.utc),
        )

    def delete(self, handle):
        assert handle == self.handle
        self.deletes += 1
        self.events.append("delete")
        if self.fail_delete:
            raise InventoryError("kernel refused nonrecursive rollback")
        self.marker = None
        self.deleted.set()


@pytest.fixture
def boot_clock(monkeypatch):
    now = [123_000]
    monkeypatch.setattr(table_agent, "_boot_ms", lambda: now[0])
    return now


@pytest.fixture(autouse=True)
def portable_clock(request):
    if sys.platform != "linux":
        request.getfixturevalue("boot_clock")


@pytest.fixture
def plan():
    return TablePlan(
        schema_version=2, generation=1, base_generation=0, generation_id=uuid4(),
        target_namespace_id="tempest-ns-lab",
        operations=(CreateNftTable(kind="create_nft_table", family="inet", name="tempest-first-nft-table"),),
    )


def request(model, method, **kwargs):
    return model(protocol_version=2, request_id=uuid4(), method=method, **kwargs)


def stage(agent, plan):
    return agent.handle_request(request(StageTableRequest, "stage", plan=plan))


def apply(agent, plan):
    return agent.handle_request(request(ApplyTableRequest, "apply", generation_id=plan.generation_id))


def verify(agent, plan):
    return agent.handle_request(request(VerifyTableRequest, "verify", generation_id=plan.generation_id))


def confirm(agent, plan):
    return agent.handle_request(request(ConfirmTableRequest, "confirm", generation_id=plan.generation_id))


def rollback(agent, plan):
    return agent.handle_request(request(
        RollbackTableRequest, "rollback", generation_id=plan.generation_id, reason="trial complete",
    ))


def status(agent):
    return agent.handle_request(request(TableStatusRequest, "read_status")).result


def test_lab_protocol_is_explicit_and_v1_stays_closed(plan):
    stage_request = request(StageTableRequest, "stage", plan=plan)
    assert parse_table_request(stage_request.model_dump_json().encode()) == stage_request
    with pytest.raises(IPCProtocolError):
        parse_ipc_request(stage_request.model_dump_json())
    wire = stage_request.model_dump(mode="json")
    for change in (
        {"protocol_version": 1},
        {"plan": {**wire["plan"], "confirmation_timeout_seconds": 4}},
        {"plan": {**wire["plan"], "confirmation_timeout_seconds": 301}},
        {"plan": {**wire["plan"], "operations": [*wire["plan"]["operations"]] * 2}},
        {"plan": {**wire["plan"], "operations": [{**wire["plan"]["operations"][0], "name": "tempest-x; flush ruleset"}]}},
    ):
        with pytest.raises(ValueError):
            StageTableRequest.model_validate(wire | change)
    with pytest.raises(ValueError):
        TableStatusRequest.model_validate({"method": "read_status", "request_id": uuid4()})


@pytest.mark.skipif(sys.platform != "linux", reason="Linux directory flock and Agent journal")
def test_durable_receipt_restart_and_exact_rollback(plan, tmp_path, boot_clock):
    tmp_path.chmod(0o700)
    kernel = TestKernel(plan.operations[0])
    with TableJournal(tmp_path) as journal:
        agent = TableAgent(kernel, journal)
        assert stage(agent, plan).ok
        assert journal.read() is None
        assert apply(agent, plan).ok
        receipt = journal.read()
        assert receipt.phase == "pending_confirmation"
        assert receipt.evidence.handle == kernel.handle
        assert not confirm(agent, plan).ok
        assert verify(agent, plan).ok
        assert journal.read().phase == "verified"
        assert (tmp_path / "table.json").stat().st_mode & 0o777 == 0o600
        with pytest.raises(BlockingIOError):
            TableJournal(tmp_path)
    with TableJournal(tmp_path) as journal:
        agent = TableAgent(kernel, journal)
        assert status(agent).confirmation_deadline_epoch_ms == receipt.deadline_epoch_ms
        assert apply(agent, plan).result.last_known_good_generation == 0
        assert confirm(agent, plan).result.last_known_good_generation == plan.generation
        assert journal.read().phase == "confirmed"
    boot_clock[0] = receipt.deadline_boot_ms
    with TableJournal(tmp_path) as journal:
        agent = TableAgent(kernel, journal)
        agent.maintain()
        assert status(agent).last_known_good_generation == plan.generation
        assert status(agent).owned_object_ids == (plan.operations[0].name,)
        assert apply(agent, plan).ok
        assert confirm(agent, plan).ok
        assert rollback(agent, plan).ok
        assert rollback(agent, plan).ok
        assert journal.read().phase == "rolled_back"
        assert kernel.events == ["create", "delete"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux Agent journal and no-follow opens")
def test_journal_refuses_nonprivate_state_and_symlinks(tmp_path):
    tmp_path.chmod(0o750)
    with pytest.raises(ValueError, match="not private"):
        TableJournal(tmp_path)
    tmp_path.chmod(0o700)
    (tmp_path / "table.json").symlink_to(tmp_path / "other.json")
    with TableJournal(tmp_path) as journal, pytest.raises(OSError):
        journal.read()


@pytest.mark.parametrize("failure,phase,creates", [
    ("intent", None, 0),
    ("create", "create_pending", 1),
    ("verify", "create_pending", 1),
    ("receipt", "create_pending", 1),
])
def test_create_faults_never_adopt_or_delete(plan, failure, phase, creates):
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok
    kernel.fail_create = failure == "create"
    kernel.fail_verify = failure == "verify"
    journal.fail_phase = {"intent": "create_pending", "receipt": "pending_confirmation"}.get(failure)
    assert not apply(agent, plan).ok
    assert (journal.record.phase if journal.record else None) == phase
    assert kernel.creates == creates
    restarted = TableAgent(kernel, journal)
    assert not rollback(restarted, plan).ok
    assert not apply(restarted, plan).ok
    assert kernel.deletes == 0


@pytest.mark.parametrize("drift", ["children", "marker", "handle", "boot", "kernel_refusal"])
def test_rollback_refuses_uncertain_ownership(plan, drift):
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok and apply(agent, plan).ok
    kernel.has_children = drift == "children"
    kernel.fail_delete = drift == "kernel_refusal"
    if drift == "marker":
        kernel.marker = uuid4()
    if drift == "handle":
        kernel.handle += 1
    if drift == "boot":
        kernel.namespace = replace(kernel.namespace, boot_id=str(uuid4()))
    assert not rollback(agent, plan).ok
    assert status(agent).state == "degraded"
    assert kernel.marker is not None
    assert kernel.deletes == int(drift == "kernel_refusal")
    assert not rollback(TableAgent(kernel, journal), plan).ok
    assert kernel.deletes == int(drift == "kernel_refusal")


@pytest.mark.parametrize("failure,deletes,phase", [
    ("rollback_pending", 0, "pending_confirmation"),
    ("rolled_back", 1, "rollback_pending"),
])
def test_rollback_journal_fault_and_absence_recovery(plan, failure, deletes, phase):
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok and apply(agent, plan).ok
    journal.fail_phase = failure
    assert not rollback(agent, plan).ok
    assert journal.record.phase == phase
    assert kernel.deletes == deletes
    journal.fail_phase = None
    status_after_restart = status(TableAgent(kernel, journal))
    assert status_after_restart.state == ("idle" if deletes else "pending_confirmation")
    assert bool(status_after_restart.owned_object_ids) == (deletes == 0)
    assert kernel.deletes == deletes


def test_no_mutation_if_selector_appears_after_staging(plan):
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok
    kernel.marker = uuid4()
    assert not apply(agent, plan).ok
    assert journal.record is None
    assert kernel.events == []


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux AF_UNIX/SO_PEERCRED")
def test_lab_protocol_uses_existing_authenticated_agent_transport(plan, tmp_path):
    path = tmp_path / "agent.sock"
    kernel = TestKernel(plan.operations[0])
    peer_policy = PeerPolicy(frozenset({os.getuid()}), frozenset({os.getgid()}))
    with AgentServer(
        path, agent=TableAgent(kernel, JournalDouble()), peer_policy=peer_policy,
        parse_request=parse_table_request,
    ) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            for message in (
                request(StageTableRequest, "stage", plan=plan),
                request(ApplyTableRequest, "apply", generation_id=plan.generation_id),
                request(VerifyTableRequest, "verify", generation_id=plan.generation_id),
                request(ConfirmTableRequest, "confirm", generation_id=plan.generation_id),
                request(RollbackTableRequest, "rollback", generation_id=plan.generation_id, reason="test"),
            ):
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(2)
                    client.connect(str(path))
                    write_frame(client, message.model_dump_json().encode())
                    reply = TableResponse.model_validate_json(read_frame(client))
                    assert reply.ok and reply.request_id == message.request_id
        finally:
            server.shutdown()
            worker.join()
    assert kernel.events == ["create", "delete"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux Agent journal")
@pytest.mark.parametrize("after_create", [False, True])
def test_process_loss_with_only_intent_preserves_kernel_state(plan, tmp_path, monkeypatch, after_create):
    class ProcessLost(BaseException):
        pass

    tmp_path.chmod(0o700)
    kernel = TestKernel(plan.operations[0])
    with TableJournal(tmp_path) as journal:
        agent = TableAgent(kernel, journal)
        assert stage(agent, plan).ok
        original_write = journal.write
        original_create = kernel.create

        def write_then_stop(record):
            original_write(record)
            raise ProcessLost()

        def create_then_stop(marker):
            original_create(marker)
            raise ProcessLost()

        if after_create:
            monkeypatch.setattr(kernel, "create", create_then_stop)
        else:
            monkeypatch.setattr(journal, "write", write_then_stop)
        with pytest.raises(ProcessLost):
            apply(agent, plan)

    with TableJournal(tmp_path) as journal:
        intent = journal.read()
        assert intent.phase == "create_pending"
        assert intent.evidence is None
        restarted = TableAgent(kernel, journal)
        restarted.maintain()
        assert status(restarted).state == "degraded"
        assert not rollback(restarted, plan).ok
        assert not stage(restarted, plan).ok
        assert journal.read() == intent
        assert kernel.marker == (intent.transaction_id if after_create else None)
        assert kernel.deletes == 0


@pytest.mark.parametrize("after_verify", [False, True])
def test_expired_receipt_rolls_back_after_restart(plan, boot_clock, after_verify):
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok and apply(agent, plan).ok
    if after_verify:
        assert verify(agent, plan).ok
    boot_clock[0] = journal.record.deadline_boot_ms - 1
    restarted = TableAgent(kernel, journal)
    restarted.maintain()
    assert status(restarted).state == "pending_confirmation"
    assert kernel.events == ["create"]
    boot_clock[0] += 1
    restarted.maintain()
    assert kernel.events == ["create", "delete"]
    assert journal.record.phase == "rolled_back"
    assert status(restarted).active_generation == 0
    assert not confirm(restarted, plan).ok


@pytest.mark.parametrize("drift", ["children", "handle", "fingerprint", "boot", "netns", "inventory"])
def test_deadline_preserves_table_if_ownership_is_uncertain(plan, boot_clock, drift):
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok and apply(agent, plan).ok
    if drift == "children":
        kernel.has_children = True
    elif drift == "handle":
        kernel.handle += 1
    elif drift == "fingerprint":
        journal.record = journal.record.model_copy(update={
            "evidence": journal.record.evidence.model_copy(update={"fingerprint": "b" * 64}),
        })
    elif drift == "boot":
        kernel.namespace = replace(kernel.namespace, boot_id=str(uuid4()))
    elif drift == "netns":
        kernel.namespace = replace(kernel.namespace, netns_inode=kernel.namespace.netns_inode + 1)
    else:
        kernel.fail_verify = True
    boot_clock[0] = journal.record.deadline_boot_ms
    restarted = TableAgent(kernel, journal)
    restarted.maintain()
    assert status(restarted).state == "degraded"
    assert not confirm(restarted, plan).ok
    assert kernel.marker == journal.record.transaction_id
    assert kernel.events == ["create"]


def test_deadline_preserves_child_inserted_after_last_verification(plan, boot_clock, monkeypatch):
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok and apply(agent, plan).ok
    delete = kernel.delete

    def insert_child_then_delete(handle):
        kernel.has_children = kernel.fail_delete = True
        delete(handle)

    monkeypatch.setattr(kernel, "delete", insert_child_then_delete)
    boot_clock[0] = journal.record.deadline_boot_ms
    agent.maintain()
    assert journal.record.phase == "rollback_pending"
    assert status(TableAgent(kernel, journal)).state == "degraded"
    assert kernel.has_children and kernel.marker == journal.record.transaction_id
    assert kernel.events == ["create", "delete"]


def test_confirm_rechecks_kernel_after_verify(plan):
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok and apply(agent, plan).ok and verify(agent, plan).ok
    kernel.has_children = True
    assert not confirm(agent, plan).ok
    assert journal.record.phase == "verified"
    assert status(agent).state == "degraded"
    assert kernel.events == ["create"]


def test_confirm_cannot_commit_if_deadline_passes_during_kernel_read(plan, boot_clock):
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok and apply(agent, plan).ok and verify(agent, plan).ok
    deadline = journal.record.deadline_boot_ms
    boot_clock[0] = deadline - 1

    def reach_deadline():
        boot_clock[0] = deadline

    kernel.on_verify = reach_deadline
    assert not confirm(agent, plan).ok
    assert journal.record.phase == "rolled_back"
    assert kernel.events == ["create", "delete"]


def test_confirm_journal_failure_does_not_report_success(plan, boot_clock):
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok and apply(agent, plan).ok and verify(agent, plan).ok
    journal.fail_phase = "confirmed"
    assert not confirm(agent, plan).ok
    assert status(agent).state == "degraded"
    assert journal.record.phase == "verified"
    journal.fail_phase = None
    boot_clock[0] = journal.record.deadline_boot_ms
    restarted = TableAgent(kernel, journal)
    restarted.maintain()
    assert journal.record.phase == "rolled_back"
    assert kernel.events == ["create", "delete"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux Agent journal")
@pytest.mark.parametrize("missing", ["evidence", "deadline_boot_ms", "deadline_epoch_ms", "schema_version"])
def test_incomplete_durable_receipt_fails_closed(plan, tmp_path, missing):
    tmp_path.chmod(0o700)
    kernel = TestKernel(plan.operations[0])
    with TableJournal(tmp_path) as journal:
        agent = TableAgent(kernel, journal)
        assert stage(agent, plan).ok and apply(agent, plan).ok
        record = journal.read().model_dump(mode="json")
    del record[missing]
    (tmp_path / "table.json").write_text(json.dumps(record))
    with TableJournal(tmp_path) as journal:
        restarted = TableAgent(kernel, journal)
        assert status(restarted).state == "degraded"
        assert not rollback(restarted, plan).ok
        assert kernel.events == ["create"]


def test_legacy_receipt_preserves_manual_rollback_only(plan, boot_clock):
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok and apply(agent, plan).ok
    legacy = journal.record.model_dump(exclude={"deadline_boot_ms", "deadline_epoch_ms"})
    journal.record = TableRecord.model_validate(legacy | {"schema_version": 1, "phase": "owned"})
    boot_clock[0] += plan.confirmation_timeout_seconds * 1000
    restarted = TableAgent(kernel, journal)
    restarted.maintain()
    assert status(restarted).last_known_good_generation == 0
    assert not confirm(restarted, plan).ok
    assert kernel.events == ["create"]
    assert rollback(restarted, plan).ok
    assert journal.record.phase == "rolled_back"


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux AF_UNIX/SO_PEERCRED")
def test_listener_rolls_back_without_an_ipc_request(plan, boot_clock, tmp_path):
    path = tmp_path / "agent.sock"
    kernel = TestKernel(plan.operations[0])
    journal = JournalDouble()
    agent = TableAgent(kernel, journal)
    assert stage(agent, plan).ok and apply(agent, plan).ok
    boot_clock[0] = journal.record.deadline_boot_ms

    with AgentServer(
        path, agent=agent, peer_policy=PeerPolicy(frozenset({os.getuid()}), frozenset({os.getgid()})),
        parse_request=parse_table_request, maintain=agent.maintain,
    ) as server:
        worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        try:
            assert kernel.deleted.wait(timeout=2)
        finally:
            server.shutdown()
            worker.join()
    assert journal.record.phase == "rolled_back"
    assert kernel.events == ["create", "delete"]


def test_frame_deadline_covers_header_and_payload(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: now[0]))

    class Peer:
        def settimeout(self, timeout):
            assert timeout > 0

        def recv(self, size):
            now[0] += 0.6
            return b"\x00\x00\x00\x02" if size == 4 else b"{"

    with pytest.raises(TimeoutError):
        read_frame(Peer(), deadline=1.0)
