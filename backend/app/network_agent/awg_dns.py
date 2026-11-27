"""Fixed sing-box DNS adapter configuration for an AWG generation."""

import json
from typing import Annotated

from pydantic import Field, IPvAnyAddress, model_validator

from .models import ContractModel

DNS_ADAPTER_PORT = 18878


class AwgDns(ContractModel):
    address: IPvAnyAddress
    server_name: Annotated[str, Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$")]
    port: Annotated[int, Field(strict=True, ge=1, le=65535)] = 853

    @model_validator(mode="after")
    def check_address(self):
        addr = self.address
        if (addr.version != 4 or addr.is_unspecified or addr.is_loopback or addr.is_multicast
                or addr.is_link_local or not self.server_name.split('.')[-1][0].isalpha()
                or any(len(label) > 63 for label in self.server_name.split('.'))):
            raise ValueError("DoT requires an explicit IPv4 address and DNS certificate name")
        return self

    def config_bytes(self, password: str, socks_port: int) -> bytes:
        config = {
            "log": {"disabled": True},
            "dns": {"disable_cache": True, "servers": [{
                "type": "tls", "tag": "dot", "server": str(self.address), "server_port": self.port,
                "detour": "awg", "tls": {"enabled": True, "server_name": self.server_name},
            }], "final": "dot"},
            "inbounds": [{"type": "direct", "tag": "dns", "listen": "127.0.0.1", "listen_port": DNS_ADAPTER_PORT}],
            "outbounds": [{"type": "socks", "tag": "awg", "server": "127.0.0.1", "server_port": socks_port,
                           "username": "tempest", "password": password}],
            "route": {"rules": [{"inbound": "dns", "action": "hijack-dns"}]},
        }
        return (json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n").encode()
