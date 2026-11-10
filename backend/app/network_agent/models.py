"""Versioned NetworkPlan and Unix-socket wire schemas.

These models describe intent only. They do not execute Linux networking
operations and deliberately contain no shell command, path, or nftables text.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)


PROTOCOL_VERSION = 1
MAX_IPC_MESSAGE_BYTES = 64 * 1024
_RESOURCE_ID = r"^tempest-[a-z0-9][a-z0-9_.-]{0,62}$"
_NAMESPACE_ID = r"^tempest-ns-[a-z0-9][a-z0-9_.-]{0,56}$"
_EGRESS_ID = r"^tempest-egress-[a-z0-9][a-z0-9_.-]{0,54}$"


class ContractModel(BaseModel):
    """Immutable wire model that rejects unrecognized input fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class OwnedObject(ContractModel):
    owner: Literal["tempest"]
    object_type: Literal["interface", "address", "route", "firewall_policy"]
    object_id: str = Field(pattern=_RESOURCE_ID)


class EgressCandidate(ContractModel):
    egress_id: str = Field(pattern=_EGRESS_ID)
    kind: Literal["direct", "wireguard", "proxy"]
    enabled: bool = True


class EnsureInterface(ContractModel):
    kind: Literal["ensure_interface"]
    object: OwnedObject
    namespace_id: str = Field(pattern=_NAMESPACE_ID)
    interface_kind: Literal["wireguard", "tun", "veth", "dummy"]

    @model_validator(mode="after")
    def require_interface_ownership_type(self) -> "EnsureInterface":
        if self.object.object_type != "interface":
            raise ValueError("ensure_interface requires an interface-owned object")
        return self


class AssignAddress(ContractModel):
    kind: Literal["assign_address"]
    object: OwnedObject
    interface_id: str = Field(pattern=_RESOURCE_ID)
    cidr: str

    @field_validator("cidr")
    @classmethod
    def normalize_cidr(cls, value: str) -> str:
        try:
            return str(ipaddress.ip_interface(value))
        except ValueError as exc:
            raise ValueError("cidr must be a valid IPv4 or IPv6 interface prefix") from exc

    @model_validator(mode="after")
    def require_address_ownership_type(self) -> "AssignAddress":
        if self.object.object_type != "address":
            raise ValueError("assign_address requires an address-owned object")
        return self


class EnsureRoute(ContractModel):
    kind: Literal["ensure_route"]
    object: OwnedObject
    interface_id: str = Field(pattern=_RESOURCE_ID)
    destination: str
    egress_id: str = Field(pattern=_EGRESS_ID)
    table_id: int = Field(ge=1, le=2**31 - 1)
    gateway: str | None = None

    @field_validator("destination")
    @classmethod
    def normalize_destination(cls, value: str) -> str:
        try:
            return str(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise ValueError("destination must be a valid IPv4 or IPv6 network") from exc

    @field_validator("gateway")
    @classmethod
    def normalize_gateway(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return str(ipaddress.ip_address(value))
        except ValueError as exc:
            raise ValueError("gateway must be a valid IPv4 or IPv6 address") from exc

    @model_validator(mode="after")
    def require_route_ownership_type(self) -> "EnsureRoute":
        if self.object.object_type != "route":
            raise ValueError("ensure_route requires a route-owned object")
        if self.gateway is not None:
            destination_version = ipaddress.ip_network(self.destination).version
            gateway_version = ipaddress.ip_address(self.gateway).version
            if destination_version != gateway_version:
                raise ValueError("gateway address family must match the destination")
        return self


class SetEgressPolicy(ContractModel):
    kind: Literal["set_egress_policy"]
    object: OwnedObject
    traffic_scope: Literal["protected_ingress"]
    verdict: Literal["allow", "block"]
    egress_id: str | None = Field(default=None, pattern=_EGRESS_ID)

    @model_validator(mode="after")
    def require_policy_ownership_type(self) -> "SetEgressPolicy":
        if self.object.object_type != "firewall_policy":
            raise ValueError("set_egress_policy requires a firewall_policy object")
        if self.verdict == "allow" and self.egress_id is None:
            raise ValueError("an allow policy must name an approved egress")
        if self.verdict == "block" and self.egress_id is not None:
            raise ValueError("a block policy cannot name an egress")
        return self


Operation = Annotated[
    Union[EnsureInterface, AssignAddress, EnsureRoute, SetEgressPolicy],
    Field(discriminator="kind"),
]


class NetworkPlan(ContractModel):
    """Full desired state for TEMPEST-owned objects in one prepared namespace."""

    schema_version: Literal[1] = 1
    generation_id: UUID
    generation: int = Field(ge=1)
    base_generation: int = Field(ge=0)
    target_namespace_id: str = Field(pattern=_NAMESPACE_ID)
    egress_candidates: tuple[EgressCandidate, ...] = Field(min_length=1, max_length=64)
    default_egress_id: str = Field(pattern=_EGRESS_ID)
    direct_fallback_allowed: bool = False
    confirmation_timeout_seconds: int = Field(default=60, ge=5, le=300)
    operations: tuple[Operation, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_plan_references(self) -> "NetworkPlan":
        if self.generation <= self.base_generation:
            raise ValueError("generation must be newer than base_generation")

        candidate_ids = [candidate.egress_id for candidate in self.egress_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("egress candidate IDs must be unique")

        permitted = {
            candidate.egress_id
            for candidate in self.egress_candidates
            if candidate.enabled
        }
        if not permitted:
            raise ValueError("plan must contain at least one enabled, permitted egress")
        if self.default_egress_id not in permitted:
            raise ValueError("default egress must name an enabled candidate")

        direct_enabled = any(
            candidate.enabled and candidate.kind == "direct"
            for candidate in self.egress_candidates
        )
        if direct_enabled and not self.direct_fallback_allowed:
            raise ValueError("DIRECT must be explicitly allowed by the plan")

        object_ids: list[str] = []
        interface_ids: set[str] = set()
        for operation in self.operations:
            if hasattr(operation, "object"):
                object_ids.append(operation.object.object_id)
            if isinstance(operation, EnsureInterface):
                if operation.namespace_id != self.target_namespace_id:
                    raise ValueError("interfaces must target the plan namespace")
                interface_ids.add(operation.object.object_id)
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("a plan cannot declare an owned object more than once")

        for operation in self.operations:
            if isinstance(operation, AssignAddress) and operation.interface_id not in interface_ids:
                raise ValueError("address references an interface absent from the plan")
            if isinstance(operation, EnsureRoute):
                if operation.interface_id not in interface_ids:
                    raise ValueError("route references an interface absent from the plan")
                if operation.egress_id not in permitted:
                    raise ValueError("route references an egress not enabled by the plan")
            if isinstance(operation, SetEgressPolicy) and operation.verdict == "allow":
                if operation.egress_id not in permitted:
                    raise ValueError("allow policy references an egress not enabled by the plan")
                if not any(
                    isinstance(route, EnsureRoute) and route.egress_id == operation.egress_id
                    for route in self.operations
                ):
                    raise ValueError("allow policy requires a route for the same egress")

        policies = [op for op in self.operations if isinstance(op, SetEgressPolicy)]
        if not any(policy.verdict == "block" for policy in policies):
            raise ValueError(
                "protected_ingress requires a default block policy in every NetworkPlan"
            )
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def owned_objects(self) -> dict[str, dict[str, object]]:
        return {
            operation.object.object_id: operation.model_dump(mode="json")
            for operation in self.operations
        }


class _IPCBase(ContractModel):
    protocol_version: Literal[1] = PROTOCOL_VERSION
    request_id: UUID


class ValidateRequest(_IPCBase):
    method: Literal["validate"]
    plan: NetworkPlan


class StageRequest(_IPCBase):
    method: Literal["stage"]
    plan: NetworkPlan


class DiffRequest(_IPCBase):
    method: Literal["diff"]
    plan: NetworkPlan


class ApplyRequest(_IPCBase):
    method: Literal["apply"]
    generation_id: UUID


class VerifyRequest(_IPCBase):
    method: Literal["verify"]
    generation_id: UUID


class ConfirmRequest(_IPCBase):
    method: Literal["confirm"]
    generation_id: UUID


class RollbackRequest(_IPCBase):
    method: Literal["rollback"]
    generation_id: UUID
    reason: str = Field(min_length=1, max_length=200)


class ReadStatusRequest(_IPCBase):
    method: Literal["read_status"]


IPCRequest = Annotated[
    Union[
        ValidateRequest,
        StageRequest,
        DiffRequest,
        ApplyRequest,
        VerifyRequest,
        ConfirmRequest,
        RollbackRequest,
        ReadStatusRequest,
    ],
    Field(discriminator="method"),
]
IPC_REQUEST_ADAPTER = TypeAdapter(IPCRequest)


class AgentStatus(ContractModel):
    state: Literal["idle", "pending_confirmation", "degraded"]
    active_generation: int = Field(ge=0)
    active_generation_id: UUID | None = None
    last_known_good_generation: int = Field(ge=0)
    pending_generation_id: UUID | None = None
    confirmation_deadline_epoch_ms: int | None = Field(default=None, ge=0)
    owned_object_ids: tuple[str, ...] = ()
    last_error: str | None = None


class ValidationResult(ContractModel):
    valid: Literal[True] = True
    generation_id: UUID
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation_count: int = Field(ge=0)
    permitted_egress_count: int = Field(ge=1)


class NetworkDiff(ContractModel):
    generation_id: UUID
    base_generation: int = Field(ge=0)
    target_generation: int = Field(ge=1)
    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


ResponseResult = Union[ValidationResult, NetworkDiff, AgentStatus]


class IPCError(ContractModel):
    code: Literal[
        "invalid_request",
        "invalid_plan",
        "conflict",
        "ownership_conflict",
        "not_found",
        "deadline_expired",
    ]
    message: str = Field(max_length=300)


class IPCResponse(ContractModel):
    protocol_version: Literal[1] = PROTOCOL_VERSION
    request_id: UUID
    ok: bool
    result: ResponseResult | None = None
    error: IPCError | None = None

    @model_validator(mode="after")
    def result_matches_outcome(self) -> "IPCResponse":
        if self.ok != (self.result is not None and self.error is None):
            raise ValueError("response must contain exactly one successful result or error")
        if not self.ok and self.error is None:
            raise ValueError("failed response requires an error")
        return self


class IPCProtocolError(ValueError):
    pass


def parse_ipc_request(raw: bytes | str) -> IPCRequest:
    """Parse one bounded JSON frame; callers must authenticate the socket peer."""

    payload = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(payload) > MAX_IPC_MESSAGE_BYTES:
        raise IPCProtocolError("IPC message exceeds the configured size limit")
    try:
        return IPC_REQUEST_ADAPTER.validate_json(payload)
    except ValidationError as exc:
        raise IPCProtocolError("IPC message does not match protocol version 1") from exc
