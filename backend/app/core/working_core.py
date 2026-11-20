"""Build the Working Core plan and standard WireGuard client profiles."""

import base64
from itertools import islice
from typing import Annotated
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from pydantic import Field, IPvAnyAddress, IPvAnyInterface, IPvAnyNetwork, SecretStr, model_validator

from app.core.uri_parser import _parse_vless
from app.network_agent.models import AgentStatus, ContractModel
from app.network_agent.xray_profile import (
    MAX_PEERS, MAX_RULES, DestinationRule, ProbePolicy, VlessTLS, WireGuardKey, WireGuardPeer, XrayPlan,
)


class Deployment(ContractModel):
    """Operator-owned inputs shared by plan generation and client provisioning."""

    target_namespace_id: str
    listen_address: IPvAnyAddress
    listen_port: Annotated[int, Field(strict=True, ge=1024, le=65535)]
    client_endpoint_address: IPvAnyAddress
    client_endpoint_port: Annotated[int, Field(strict=True, ge=1024, le=65535)]
    tunnel_addresses: tuple[IPvAnyInterface, IPvAnyInterface]
    client_pools: tuple[IPvAnyNetwork, IPvAnyNetwork]
    private_key: WireGuardKey = Field(repr=False)
    probe: ProbePolicy
    resolvers: tuple[IPvAnyAddress, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def validate_pools(self) -> "Deployment":
        reserved = (*self.tunnel_addresses, *self.probe.peer.addresses)
        if tuple(pool.version for pool in self.client_pools) != (4, 6) or any(
            address.ip not in self.client_pools[0 if address.version == 4 else 1]
            for address in reserved
        ):
            raise ValueError("client pools must contain the WireGuard server and probe addresses")
        if any(getattr(address, "scope_id", None) is not None for address in (
            self.client_endpoint_address, self.listen_address, *self.resolvers,
        )):
            raise ValueError("address scopes are not supported")
        return self

    def plan(self, draft: "Draft", vless: VlessTLS, status: AgentStatus) -> XrayPlan:
        return XrayPlan(
            generation_id=uuid4(),
            base_generation=status.active_generation,
            generation=max(status.active_generation, status.generation_watermark or 0) + 1,
            schema_version=1,
            target_namespace_id=self.target_namespace_id,
            listen_address=self.listen_address,
            listen_port=self.listen_port,
            default_egress="block",
            tunnel_addresses=self.tunnel_addresses,
            private_key=self.private_key,
            peers=tuple(client.peer for client in draft.clients),
            probe=self.probe,
            vless=vless,
            resolvers=self.resolvers,
            rules=draft.rules,
            confirmation_timeout_seconds=180,
        )

    def create_client(self, name: str, draft: "Draft") -> tuple["Client", str]:
        reserved = {
            address.ip for address in (*self.tunnel_addresses, *self.probe.peer.addresses)
        }
        reserved.update(address.ip for client in draft.clients for address in client.peer.addresses)
        addresses = []
        for pool in self.client_pools:
            ip = next((ip for ip in islice(pool.hosts(), MAX_PEERS + 3) if ip not in reserved), None)
            if ip is None:
                raise ValueError("WireGuard client pool is full")
            addresses.append(f"{ip}/{pool.max_prefixlen}")
        private_key = X25519PrivateKey.generate()
        client = Client(
            id=uuid4(),
            name=name,
            peer=WireGuardPeer(public_key=_public_key(private_key), addresses=tuple(addresses)),
        )
        server_key = X25519PrivateKey.from_private_bytes(base64.b64decode(self.private_key))
        endpoint = self.client_endpoint_address
        host = f"[{endpoint}]" if endpoint.version == 6 else str(endpoint)
        secret = private_key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption(),
        )
        profile = "\n".join((
            "[Interface]",
            f"PrivateKey = {base64.b64encode(secret).decode('ascii')}",
            "Address = " + ", ".join(addresses),
            "DNS = " + ", ".join(map(str, self.resolvers)),
            "MTU = 1280",
            "",
            "[Peer]",
            f"PublicKey = {_public_key(server_key)}",
            "AllowedIPs = 0.0.0.0/0, ::/0",
            f"Endpoint = {host}:{self.client_endpoint_port}",
            "PersistentKeepalive = 25",
            "",
        ))
        return client, profile


def _public_key(key: X25519PrivateKey) -> str:
    return base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )).decode("ascii")


ClientName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")]


class Client(ContractModel):
    id: UUID
    name: ClientName
    peer: WireGuardPeer


class SavedEgress(ContractModel):
    id: UUID
    name: ClientName
    encrypted_vless: str = Field(min_length=1, max_length=8192, repr=False)
    provider_id: UUID | None = None


class Provider(ContractModel):
    id: UUID
    name: ClientName


class Draft(ContractModel):
    revision: int = Field(default=0, ge=0)
    encrypted_vless: str | None = Field(default=None, repr=False)
    saved_egresses: tuple[SavedEgress, ...] = Field(default=(), max_length=64)
    providers: tuple[Provider, ...] = Field(default=(), max_length=64)
    selected_egress_id: UUID | None = None
    clients: tuple[Client, ...] = Field(default=(), max_length=MAX_PEERS)
    rules: tuple[DestinationRule, ...] = Field(default=(), max_length=MAX_RULES)


class VlessImport(ContractModel):
    uri: SecretStr = Field(min_length=1, max_length=4096)
    dial_address: IPvAnyAddress | None = None

    def vless(self) -> VlessTLS:
        uri = self.uri.get_secret_value().strip()
        try:
            parsed = urlsplit(uri)
            pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=12)
            params = dict(pairs)
            if (parsed.scheme != "vless" or not parsed.username or not parsed.hostname
                    or parsed.password is not None or len(pairs) != len(params)):
                raise ValueError
            if (params.keys() - {"type", "security", "encryption", "sni", "pcs", "headerType", "fp"}
                    or params.get("type", "tcp") not in {"tcp", "raw"}
                    or params.get("security") != "tls"
                    or params.get("encryption", "none") != "none"
                    or params.get("headerType", "none") != "none"
                    or parsed.path not in {"", "/"}):
                raise ValueError
            node = _parse_vless(uri)
            if "pcs" in params and not node["pinned_cert_sha256"]:
                raise ValueError
            return VlessTLS(
                address=self.dial_address or node["address"],
                port=parsed.port if parsed.port is not None else 443,
                client_id=node["uuid"],
                server_name=node["sni"],
                certificate_sha256=node["pinned_cert_sha256"],
            )
        except ValueError:
            raise ValueError(
                "Expected VLESS over TCP/TLS. Enter a dial IP for a hostname and use a DNS TLS name."
            ) from None
