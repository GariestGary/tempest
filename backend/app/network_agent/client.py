"""The Control Plane's typed, read-only Network Agent boundary for task 2.3.

The current Agent is a mock. In particular, its successful ``apply`` reply
would not change the network, so this client does not expose that operation.
"""

from __future__ import annotations

import asyncio
import struct
from uuid import uuid4

from app.config import settings
from app.network_agent.models import (
    MAX_IPC_MESSAGE_BYTES,
    DiffRequest,
    IPCResponse,
    NetworkPlan,
    ReadStatusRequest,
    ValidateRequest,
)


class NetworkAgentUnavailable(RuntimeError):
    """The local Agent is absent or returned an invalid/failed response."""


class NetworkApplyUnsupported(RuntimeError):
    """No real, recoverable NetworkPlan apply exists yet."""


class NetworkAgentClient:
    """Only typed v1 requests; no command, file or Docker operation exists."""

    def __init__(self, socket_path: str | None = None, timeout: float = 3.0):
        self._socket_path = socket_path or settings.network_agent_socket
        self._timeout = timeout

    async def _call(self, request: ValidateRequest | DiffRequest | ReadStatusRequest) -> IPCResponse:
        payload = request.model_dump_json().encode("utf-8")
        if len(payload) > MAX_IPC_MESSAGE_BYTES:
            raise NetworkAgentUnavailable("NetworkPlan exceeds the IPC frame limit")

        async def exchange() -> IPCResponse:
            reader, writer = await asyncio.open_unix_connection(self._socket_path)
            try:
                writer.write(struct.pack(">I", len(payload)) + payload)
                await writer.drain()
                size = struct.unpack(">I", await reader.readexactly(4))[0]
                if size > MAX_IPC_MESSAGE_BYTES:
                    raise NetworkAgentUnavailable("Agent response exceeds the IPC frame limit")
                raw = await reader.readexactly(size)
                response = IPCResponse.model_validate_json(raw)
                if response.request_id != request.request_id:
                    raise NetworkAgentUnavailable("Agent response request ID mismatch")
                if not response.ok:
                    raise NetworkAgentUnavailable(
                        f"Agent rejected {request.method}: {response.error.code}"
                    )
                return response
            finally:
                writer.close()
                await writer.wait_closed()

        try:
            return await asyncio.wait_for(exchange(), timeout=self._timeout)
        except (OSError, asyncio.TimeoutError, EOFError, ValueError) as exc:
            if isinstance(exc, NetworkAgentUnavailable):
                raise
            raise NetworkAgentUnavailable("Network Agent IPC unavailable or invalid") from exc

    async def validate(self, plan: NetworkPlan) -> IPCResponse:
        return await self._call(ValidateRequest(request_id=uuid4(), method="validate", plan=plan))

    async def diff(self, plan: NetworkPlan) -> IPCResponse:
        return await self._call(DiffRequest(request_id=uuid4(), method="diff", plan=plan))

    async def read_status(self) -> IPCResponse:
        return await self._call(ReadStatusRequest(request_id=uuid4(), method="read_status"))

    async def apply(self, plan: NetworkPlan) -> None:
        # Validate the typed input at the caller, but never interpret it as
        # an instruction to the mock's successful no-op apply implementation.
        if not isinstance(plan, NetworkPlan):
            raise TypeError("apply requires a typed NetworkPlan")
        raise NetworkApplyUnsupported(
            "NetworkPlan apply is unavailable until real Agent apply and recovery are verified"
        )


network_agent_client = NetworkAgentClient()
