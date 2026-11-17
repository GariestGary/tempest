"""Version 2 contract for the opt-in, empty-table-only lab Agent."""

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import Field, TypeAdapter, ValidationError

from .models import (
    ApplyRequest, ConfirmRequest, ContractModel, GenerationPlan, IPCProtocolError, IPCResponse,
    MAX_IPC_MESSAGE_BYTES, ReadStatusRequest, RollbackRequest, VerifyRequest, _RESOURCE_ID,
)

LAB_NAMESPACE = "tempest-ns-lab"
TableName = Annotated[str, Field(pattern=_RESOURCE_ID)]


class CreateNftTable(ContractModel):
    kind: Literal["create_nft_table"]
    family: Literal["inet"]
    name: TableName


class TablePlan(GenerationPlan):
    schema_version: Literal[2]
    target_namespace_id: Literal[LAB_NAMESPACE]
    operations: tuple[CreateNftTable]
    confirmation_timeout_seconds: int = Field(default=60, strict=True, ge=5, le=300)


class StageTableRequest(ContractModel):
    protocol_version: Literal[2] = Field(...)
    request_id: UUID
    method: Literal["stage"]
    plan: TablePlan


class ApplyTableRequest(ApplyRequest):
    protocol_version: Literal[2] = Field(...)


class VerifyTableRequest(VerifyRequest):
    protocol_version: Literal[2] = Field(...)


class ConfirmTableRequest(ConfirmRequest):
    protocol_version: Literal[2] = Field(...)


class RollbackTableRequest(RollbackRequest):
    protocol_version: Literal[2] = Field(...)


class TableStatusRequest(ReadStatusRequest):
    protocol_version: Literal[2] = Field(...)


class TableResponse(IPCResponse):
    protocol_version: Literal[2] = 2


TableRequest = Annotated[
    Union[
        StageTableRequest, ApplyTableRequest, VerifyTableRequest, ConfirmTableRequest,
        RollbackTableRequest, TableStatusRequest,
    ],
    Field(discriminator="method"),
]
_REQUEST = TypeAdapter(TableRequest)


def parse_table_request(payload: bytes) -> TableRequest:
    if len(payload) > MAX_IPC_MESSAGE_BYTES:
        raise IPCProtocolError("IPC message exceeds the configured size limit")
    try:
        return _REQUEST.validate_json(payload)
    except ValidationError as exc:
        raise IPCProtocolError("IPC message does not match the lab table protocol") from exc
