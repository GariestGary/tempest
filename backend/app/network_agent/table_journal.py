"""Agent-only intent and receipt for one empty nftables table."""

import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .linux_inventory import NamespaceIdentity
from .models import ContractModel
from .table_plan import TablePlan

_MAX_RECORD = 8192
PENDING_CONFIRMATION = frozenset({"pending_confirmation", "verified"})


class TableEvidence(ContractModel):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    handle: int = Field(strict=True, ge=1, le=2**32 - 1)
    verified_at: datetime


class TableRecord(ContractModel):
    schema_version: Literal[1, 2]
    transaction_id: UUID
    namespace: NamespaceIdentity
    plan: TablePlan
    phase: Literal[
        "create_pending", "owned", "pending_confirmation", "verified", "confirmed",
        "rollback_pending", "rolled_back",
    ]
    intent_at: datetime
    created_at: datetime | None = None
    evidence: TableEvidence | None = None
    deadline_boot_ms: int | None = Field(default=None, strict=True, gt=0)
    deadline_epoch_ms: int | None = Field(default=None, strict=True, gt=0)

    @model_validator(mode="after")
    def check_evidence(self) -> "TableRecord":
        if self.plan.target_namespace_id != self.namespace.name:
            raise ValueError("journal plan and namespace disagree")
        fields_present = (self.created_at is not None, self.evidence is not None)
        expected = self.phase != "create_pending"
        if fields_present != (expected, expected):
            raise ValueError("journal phase and receipt disagree")
        deadlines = (self.deadline_boot_ms is not None, self.deadline_epoch_ms is not None)
        if self.schema_version == 1:
            if any(deadlines) or self.phase in PENDING_CONFIRMATION | {"confirmed"}:
                raise ValueError("legacy receipt has transaction confirmation fields")
        elif self.phase == "owned" or deadlines != (expected, expected):
            raise ValueError("journal phase and confirmation deadline disagree")
        return self


class TableJournal:
    """Hold an exclusive directory lock and replace the record durably."""

    def __init__(self, directory: Path) -> None:
        import fcntl

        self._directory = os.open(directory, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self._require_private(self._directory, 0o700)
            fcntl.flock(self._directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ValueError):
            os.close(self._directory)
            raise

    def __enter__(self) -> "TableJournal":
        return self

    def __exit__(self, *_exc: object) -> None:
        os.close(self._directory)

    @staticmethod
    def _require_private(fd: int, mode: int) -> None:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != mode:
            raise ValueError("journal path is not private to the Agent")
        if mode == 0o600 and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise ValueError("journal record is not a private regular file")

    def read(self) -> TableRecord | None:
        try:
            fd = os.open(
                "table.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self._directory,
            )
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            self._require_private(stream.fileno(), 0o600)
            data = stream.read(_MAX_RECORD + 1)
            if len(data) > _MAX_RECORD:
                raise ValueError("journal record exceeds size limit")
            return TableRecord.model_validate_json(data)

    def write(self, record: TableRecord) -> None:
        # Pydantic also serializes the registry's namespace identity.
        data = record.model_dump_json().encode()
        TableRecord.model_validate_json(data)
        if len(data) > _MAX_RECORD:
            raise ValueError("journal record exceeds size limit")
        temporary = f".table-{uuid4().hex}"
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=self._directory,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                self._require_private(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, "table.json", src_dir_fd=self._directory, dst_dir_fd=self._directory)
            os.fsync(self._directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=self._directory)
            except FileNotFoundError:
                pass
        if self.read() != record:
            raise ValueError("journal read-back differs from the committed record")
