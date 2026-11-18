"""Typed, closed configuration for the Xray WireGuard MVP."""

import base64
import binascii
from ipaddress import IPv6Network
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator, Field, IPvAnyAddress, IPvAnyInterface, IPvAnyNetwork,
    TypeAdapter, ValidationError, model_validator,
)

from .models import (
    ApplyRequest, ConfirmRequest, ContractModel, GenerationPlan, IPCProtocolError,
    MAX_IPC_MESSAGE_BYTES, ReadStatusRequest, RollbackRequest, VerifyRequest,
)

INBOUND = "wg-mvp"
DNS_PORTS = (53, 853)
MAPPED_IPV6 = IPv6Network("::ffff:0:0/96")
Port = Annotated[int, Field(strict=True, ge=1, le=65535)]


def _wireguard_key(value: str) -> str:
    try:
        key = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("invalid WireGuard key") from None
    if len(key) != 32 or not any(key) or base64.b64encode(key).decode("ascii") != value:
        raise ValueError("invalid WireGuard key")
    return value


WireGuardKey = Annotated[str, Field(min_length=44, max_length=44), AfterValidator(_wireguard_key)]


class WireGuardPeer(ContractModel):
    public_key: WireGuardKey
    addresses: tuple[IPvAnyInterface, IPvAnyInterface]

    @model_validator(mode="after")
    def validate_addresses(self) -> "WireGuardPeer":
        _check_host_addresses(self.addresses)
        return self


def _check_host_addresses(addresses) -> None:
    if ({address.version for address in addresses} != {4, 6}
            or any(address.network.prefixlen != address.max_prefixlen
                   or address.ip.is_unspecified or address.ip.is_multicast
                   or address.ip.is_loopback or address.ip.is_link_local
                   or getattr(address.ip, "ipv4_mapped", None) is not None for address in addresses)):
        raise ValueError("expected one IPv4 /32 and one IPv6 /128 unicast host address")


class VlessTLS(ContractModel):
    address: IPvAnyAddress
    port: Port
    client_id: UUID = Field(repr=False)
    server_name: str = Field(
        min_length=1, max_length=253,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*$",
    )
    certificate_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class DestinationRule(ContractModel):
    destination: IPvAnyNetwork
    port: Port
    egress: Literal["direct", "vpn", "block"]

    @model_validator(mode="after")
    def validate_rule(self) -> "DestinationRule":
        if self.port in DNS_PORTS or self.destination.prefixlen == 0:
            raise ValueError("DNS ports are reserved and user rules require an explicit destination")
        if isinstance(self.destination, IPv6Network) and self.destination.overlaps(MAPPED_IPV6):
            raise ValueError("IPv4-mapped address space is not allowed in IPv6 rules")
        return self


class XrayPlan(GenerationPlan):
    """An allowlist of fields. Callers have no access to raw engine JSON."""

    schema_version: Literal[1]
    target_namespace_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    default_egress: Literal["block"]
    listen_address: IPvAnyAddress
    listen_port: Annotated[int, Field(strict=True, ge=1024, le=65535)]
    tunnel_addresses: tuple[IPvAnyInterface, IPvAnyInterface]
    private_key: WireGuardKey = Field(repr=False)
    peers: tuple[WireGuardPeer, ...] = Field(max_length=32)
    vless: VlessTLS
    resolvers: tuple[IPvAnyAddress, ...] = Field(min_length=1, max_length=2)
    rules: tuple[DestinationRule, ...] = Field(max_length=32)
    confirmation_timeout_seconds: int = Field(default=90, strict=True, ge=15, le=300)

    @model_validator(mode="after")
    def validate_policy(self) -> "XrayPlan":
        _check_host_addresses(self.tunnel_addresses)
        keys = [peer.public_key for peer in self.peers]
        addresses = [address.ip for peer in self.peers for address in peer.addresses]
        if len(keys) != len(set(keys)) or len(addresses) != len(set(addresses)):
            raise ValueError("duplicate WireGuard peer key or address")
        if set(addresses).intersection(address.ip for address in self.tunnel_addresses):
            raise ValueError("peer address collides with the server address")
        endpoints = (self.listen_address, self.vless.address, *self.resolvers)
        if any(addr.is_unspecified or addr.is_loopback or addr.is_multicast or addr.is_link_local
               or getattr(addr, "ipv4_mapped", None) is not None for addr in endpoints):
            raise ValueError("listener, upstream and resolver addresses must be explicit unicast")
        if self.vless.address == self.listen_address or self.listen_address in self.resolvers:
            raise ValueError("listener cannot be an egress destination")
        if len({address.version for address in self.resolvers}) != len(self.resolvers):
            raise ValueError("only one resolver per IP family is supported")
        for index, rule in enumerate(self.rules):
            for previous in self.rules[:index]:
                if (rule.port == previous.port
                        and rule.destination.version == previous.destination.version
                        and rule.destination.overlaps(previous.destination)):
                    raise ValueError("overlapping destination rules on the same port")
        return self

    def config_bytes(self) -> bytes:
        import json

        from app.core.config_gen import _outbound_vless
        from app.models import Node

        upstream = self.vless
        vpn = _outbound_vless(Node(
            name="Xray MVP egress", protocol="vless", address=str(upstream.address),
            port=upstream.port, transport="tcp", tls="tls", uuid=str(upstream.client_id),
            sni=upstream.server_name, fingerprint="", pinned_cert_sha256=upstream.certificate_sha256,
        ))
        vpn["tag"] = "vpn"
        vpn["streamSettings"].pop("sockopt")
        vpn["streamSettings"]["network"] = "raw"
        if upstream.certificate_sha256 is not None:
            vpn["streamSettings"]["tlsSettings"]["verifyPeerCertByName"] = upstream.server_name

        def match(egress: str, **conditions) -> dict:
            return dict(conditions, type="field", inboundTag=[INBOUND], outboundTag=egress)

        return (json.dumps({
            "log": {"loglevel": "warning"},
            "inbounds": [{
                "protocol": "wireguard", "tag": INBOUND,
                "listen": str(self.listen_address), "port": self.listen_port,
                "settings": {
                    "secretKey": self.private_key, "mtu": 1280,
                    "address": [str(address) for address in self.tunnel_addresses],
                    "peers": [{"publicKey": peer.public_key,
                               "allowedIPs": [str(address) for address in peer.addresses]}
                              for peer in self.peers],
                },
            }],
            "outbounds": [
                {"protocol": "blackhole", "tag": "block"},
                {"protocol": "freedom", "tag": "direct", "settings": {"finalRules": [
                    {"action": "allow", "network": "tcp,udp",
                     "ip": [str(rule.destination)], "port": str(rule.port)}
                    for rule in self.rules if rule.egress == "direct"
                ]}},
                vpn,
            ],
            "routing": {"domainStrategy": "AsIs", "rules": [
                match("vpn", ip=[str(address) for address in self.resolvers], port="53", network="tcp,udp"),
                match("block", port="53,853"),
                *(match(rule.egress, ip=[str(rule.destination)], port=str(rule.port), network="tcp,udp")
                  for rule in self.rules),
                match("block"),
            ]},
        }, sort_keys=True, separators=(",", ":")) + "\n").encode()


class XrayPlanRequest(ContractModel):
    protocol_version: Literal[1]
    request_id: UUID
    method: Literal["validate", "stage", "diff"]
    plan: XrayPlan


XrayRequest = Annotated[
    XrayPlanRequest | ApplyRequest | VerifyRequest | ConfirmRequest | RollbackRequest | ReadStatusRequest,
    Field(discriminator="method"),
]
_REQUEST = TypeAdapter(XrayRequest)


def parse_xray_request(payload: bytes) -> XrayRequest:
    if len(payload) > MAX_IPC_MESSAGE_BYTES:
        raise IPCProtocolError("Xray IPC message exceeds the frame limit")
    try:
        return _REQUEST.validate_json(payload)
    except ValidationError:
        raise IPCProtocolError("request does not match the Xray profile") from None
