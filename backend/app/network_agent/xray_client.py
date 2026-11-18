"""Operator CLI for the typed Xray profile on the authenticated AF_UNIX socket."""

import argparse
import asyncio
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from .client import NetworkAgentClient, NetworkAgentRejected, NetworkAgentUnavailable
from .models import (
    MAX_IPC_MESSAGE_BYTES, ApplyRequest, ConfirmRequest, ReadStatusRequest, RollbackRequest, VerifyRequest,
)
from .xray_profile import XrayPlan, XrayPlanRequest, XrayRecoverRequest, XrayRevokeRequest

DEFAULT_SOCKET = "/run/tempest-xray-agent/control.sock"


class XrayAgentClient(NetworkAgentClient):
    async def revoke_peer(self, public_key: str):
        return await self._call(XrayRevokeRequest(
            protocol_version=1, request_id=uuid4(), method="revoke_peer", public_key=public_key,
        ))

    async def request(self, method: str, *, plan: XrayPlan | None = None, generation_id: UUID | None = None):
        fields = {"request_id": uuid4(), "method": method}
        if method in {"validate", "stage", "diff"}:
            request = XrayPlanRequest(protocol_version=1, plan=plan, **fields)
        elif method == "read_status":
            request = ReadStatusRequest(**fields)
        elif method == "recover_lkg":
            request = XrayRecoverRequest(protocol_version=1, **fields)
        else:
            request_type = {
                "apply": ApplyRequest, "verify": VerifyRequest,
                "confirm": ConfirmRequest, "rollback": RollbackRequest,
            }[method]
            if method == "rollback":
                fields["reason"] = "operator rollback"
            request = request_type(generation_id=generation_id, **fields)
        return await self._call(request)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default=DEFAULT_SOCKET)
    commands = parser.add_subparsers(dest="method", required=True)
    for action in ("validate", "stage", "diff"):
        commands.add_parser(action).add_argument("plan", type=Path)
    for action in ("apply", "verify", "confirm", "rollback"):
        commands.add_parser(action).add_argument("generation_id", type=UUID)
    commands.add_parser("read_status")
    args = parser.parse_args()
    plan = None
    try:
        if hasattr(args, "plan"):
            with args.plan.open("rb") as stream:
                contents = stream.read(MAX_IPC_MESSAGE_BYTES + 1)
            if len(contents) > MAX_IPC_MESSAGE_BYTES:
                parser.error("Xray profile exceeds the frame limit")
            plan = XrayPlan.model_validate_json(contents)
        client = XrayAgentClient(args.socket, timeout=65.0)
        result = asyncio.run(client.request(args.method, plan=plan, generation_id=getattr(args, "generation_id", None)))
    except (OSError, ValidationError):
        parser.error("Xray profile could not be read or validated")
    except (NetworkAgentUnavailable, NetworkAgentRejected) as exc:
        parser.error(str(exc))
    print(result.model_dump_json(exclude_none=True))


if __name__ == "__main__":
    main()
