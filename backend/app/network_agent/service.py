"""Shared AF_UNIX transport for TEMPEST Agents."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import socketserver
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from uuid import UUID

from .mock import MockNetworkAgent
from .models import IPCProtocolError, IPCResponse, MAX_IPC_MESSAGE_BYTES, parse_ipc_request

LOG = logging.getLogger("tempest.network_agent")
_UNIX_STREAM_SERVER = getattr(socketserver, "UnixStreamServer", None)
FRAME_HEADER = struct.Struct("!I")
DEFAULT_SOCKET = "/run/tempest-agent/control.sock"
MAX_REPLAY_ENTRIES = 4096


class PeerRejected(PermissionError):
    pass


class ReplayConflict(ValueError):
    pass


@dataclass(frozen=True)
class PeerIdentity:
    pid: int
    uid: int
    gid: int


@dataclass(frozen=True)
class PeerPolicy:
    allowed_uids: frozenset[int]
    allowed_gids: frozenset[int]
    expected_cgroup: str | None = None

    def authorize(self, peer: PeerIdentity, *, cgroup: str | None = None) -> None:
        if peer.uid not in self.allowed_uids or peer.gid not in self.allowed_gids:
            raise PeerRejected("peer credentials are not allowlisted")
        if self.expected_cgroup is not None and (
            cgroup is None or self.expected_cgroup not in cgroup
        ):
            raise PeerRejected("peer service identity is not allowlisted")


class ReplayGuard:
    """Bounded request-ID cache; exact retries return the original response."""

    def __init__(self, max_entries: int = MAX_REPLAY_ENTRIES) -> None:
        self._max_entries = max_entries
        self._entries: dict[UUID, tuple[bytes, bytes]] = {}
        self._lock = threading.Lock()

    def execute(self, request_id: UUID, payload: bytes, callback: Callable[[], bytes]) -> bytes:
        digest = hashlib.sha256(payload).digest()
        with self._lock:
            previous = self._entries.get(request_id)
            if previous is not None:
                old_digest, old_response = previous
                if old_digest != digest:
                    raise ReplayConflict("request_id was reused with different content")
                return old_response
            if len(self._entries) >= self._max_entries:
                raise ReplayConflict("request replay cache is full; refusing uncached requests")
            response = callback()
            self._entries[request_id] = (digest, response)
            return response


def peer_identity(connection: socket.socket) -> PeerIdentity:
    if not hasattr(socket, "SO_PEERCRED"):
        raise PeerRejected("SO_PEERCRED is required on this platform")
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    pid, uid, gid = struct.unpack("3i", credentials)
    return PeerIdentity(pid=pid, uid=uid, gid=gid)


def _read_exact(connection: socket.socket, size: int, deadline: float | None) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        if deadline is not None:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                raise TimeoutError("IPC frame read deadline expired")
            connection.settimeout(timeout)
        chunk = connection.recv(remaining)
        if not chunk:
            raise EOFError("connection closed before frame completed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(connection: socket.socket, *, deadline: float | None = None) -> bytes:
    size = FRAME_HEADER.unpack(_read_exact(connection, FRAME_HEADER.size, deadline))[0]
    if size < 1 or size > MAX_IPC_MESSAGE_BYTES:
        raise IPCProtocolError("IPC frame length is outside the allowed range")
    return _read_exact(connection, size, deadline)


def write_frame(connection: socket.socket, payload: bytes) -> None:
    if not payload or len(payload) > MAX_IPC_MESSAGE_BYTES:
        raise IPCProtocolError("IPC response exceeds the configured size limit")
    connection.sendall(FRAME_HEADER.pack(len(payload)) + payload)


class AgentHandler(Protocol):
    def handle_request(self, request) -> IPCResponse: ...


class AgentServer(_UNIX_STREAM_SERVER or socketserver.TCPServer):
    """Single-threaded listener serializes clients and state transitions."""

    allow_reuse_address = False
    request_queue_size = 16
    timeout = 1

    def __init__(
        self,
        socket_path: str | os.PathLike[str],
        *,
        agent: AgentHandler,
        peer_policy: PeerPolicy,
        io_timeout_seconds: float = 2.0,
        replay_guard: ReplayGuard | None = None,
        parse_request: Callable = parse_ipc_request,
        maintain: Callable[[], None] | None = None,
    ) -> None:
        if _UNIX_STREAM_SERVER is None or not hasattr(socket, "SO_PEERCRED"):
            raise RuntimeError("Network Agent IPC requires Linux AF_UNIX and SO_PEERCRED")
        self.agent = agent
        self.peer_policy = peer_policy
        self.io_timeout_seconds = io_timeout_seconds
        self.replay_guard = replay_guard or ReplayGuard()
        self.parse_request = parse_request
        self._maintain = maintain
        super().__init__(str(socket_path), _RequestHandler, bind_and_activate=False)
        self.server_bind()
        os.chmod(socket_path, 0o660)
        self.server_activate()

    def service_actions(self) -> None:
        if self._maintain is not None:
            self._maintain()


class _RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        connection = self.connection
        connection.settimeout(self.server.io_timeout_seconds)
        try:
            peer = peer_identity(connection)
            cgroup = _read_peer_cgroup(peer.pid) if self.server.peer_policy.expected_cgroup else None
            self.server.peer_policy.authorize(peer, cgroup=cgroup)
            # A peer sending a frame in small pieces must still release the
            # listener in time for transaction maintenance.
            payload = read_frame(connection, deadline=time.monotonic() + self.server.io_timeout_seconds)
            connection.settimeout(self.server.io_timeout_seconds)
            request = self.server.parse_request(payload)
            response = self.server.replay_guard.execute(
                request.request_id,
                payload,
                lambda: self.server.agent.handle_request(request).model_dump_json().encode("utf-8"),
            )
            write_frame(connection, response)
            LOG.info(
                "ipc request_id=%s method=%s generation_id=%s uid=%d gid=%d pid=%d outcome=complete",
                request.request_id,
                request.method,
                _request_generation(request),
                peer.uid,
                peer.gid,
                peer.pid,
            )
        except (PeerRejected, OSError, EOFError, IPCProtocolError, ReplayConflict, ValueError) as exc:
            # Do not log payloads or user-supplied values. Closing the stream is
            # the protocol-level rejection for malformed and unauthenticated input.
            LOG.warning("ipc connection rejected reason=%s", type(exc).__name__)


def _request_generation(request: object) -> str:
    generation_id = getattr(request, "generation_id", None)
    if generation_id is None and hasattr(request, "plan"):
        generation_id = request.plan.generation_id
    return str(generation_id) if generation_id is not None else "none"


def _read_peer_cgroup(pid: int) -> str | None:
    """Read only the kernel cgroup identity of the authenticated peer."""
    try:
        base = Path(f"/proc/{pid}")
        before = _process_start_time(base / "stat")
        cgroup = (base / "cgroup").read_text(encoding="utf-8")
        after = _process_start_time(base / "stat")
        return cgroup if before is not None and before == after else None
    except (OSError, UnicodeError):
        return None


def _process_start_time(stat_path: Path) -> str | None:
    # comm is parenthesized and may itself contain spaces or ')' characters;
    # starttime is field 22, so split only after the final ')'.
    raw = stat_path.read_text(encoding="ascii")
    closing = raw.rfind(")")
    fields_after_comm = raw[closing + 1 :].split()
    return fields_after_comm[19] if closing >= 0 and len(fields_after_comm) > 19 else None


def _parse_id_set(value: str, setting: str) -> frozenset[int]:
    try:
        parsed = frozenset(int(part, 10) for part in value.split(",") if part)
    except ValueError as exc:
        raise ValueError(f"{setting} must be comma-separated numeric IDs") from exc
    if not parsed or any(item < 0 for item in parsed):
        raise ValueError(f"{setting} must contain at least one nonnegative ID")
    return parsed


def serve(
    socket_path: str = DEFAULT_SOCKET,
    *,
    allowed_uids: frozenset[int],
    allowed_gids: frozenset[int],
    expected_cgroup: str | None = None,
    agent: AgentHandler | None = None,
    parse_request: Callable = parse_ipc_request,
    maintain: Callable[[], None] | None = None,
) -> None:
    parent = Path(socket_path).parent
    if not parent.is_dir():
        raise RuntimeError("socket parent must be provisioned by systemd RuntimeDirectory")
    if os.path.lexists(socket_path):
        raise RuntimeError("refusing to replace an existing socket path")
    server = AgentServer(
        socket_path,
        agent=MockNetworkAgent() if agent is None else agent,
        peer_policy=PeerPolicy(allowed_uids, allowed_gids, expected_cgroup),
        parse_request=parse_request,
        maintain=maintain,
    )
    LOG.info("network agent listening socket=%s mode=0660", socket_path)
    try:
        server.service_actions()
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="TEMPEST Network Agent IPC service")
    parser.add_argument("--socket", default=os.environ.get("TEMPEST_AGENT_SOCKET", DEFAULT_SOCKET))
    parser.add_argument("--allowed-uids", default=os.environ.get("TEMPEST_AGENT_ALLOWED_UIDS"))
    parser.add_argument("--allowed-gids", default=os.environ.get("TEMPEST_AGENT_ALLOWED_GIDS"))
    parser.add_argument("--expected-cgroup", default=os.environ.get("TEMPEST_AGENT_EXPECTED_CGROUP"))
    parser.add_argument("--lab-table", help="enable the opt-in table backend for this exact selector")
    parser.add_argument("--lab-journal", type=Path)
    parser.add_argument("--lab-boot-id")
    parser.add_argument("--lab-netns", help="preflight target namespace device:inode")
    parser.add_argument("--lab-host-netns", help="preflight PID 1 namespace device:inode")
    parser.add_argument("--xray", type=Path, help="enable the isolated Xray backend with this pinned binary")
    parser.add_argument("--xray-state", type=Path)
    parser.add_argument("--xray-runtime", type=Path)
    parser.add_argument("--xray-netns")
    parser.add_argument("--xray-listen")
    parser.add_argument("--xray-port", type=int)
    args = parser.parse_args()
    if args.allowed_uids is None or args.allowed_gids is None:
        parser.error("allowed UID and GID lists must be configured explicitly")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    peer_options = dict(
        allowed_uids=_parse_id_set(args.allowed_uids, "allowed-uids"),
        allowed_gids=_parse_id_set(args.allowed_gids, "allowed-gids"),
        expected_cgroup=args.expected_cgroup or None,
    )
    lab_options = (args.lab_table, args.lab_journal, args.lab_boot_id, args.lab_netns, args.lab_host_netns)
    xray_options = (args.xray, args.xray_state, args.xray_runtime, args.xray_netns,
                    args.xray_listen, args.xray_port)
    if any(option is not None for option in xray_options):
        if any(lab_options) or not all(xray_options) or not Path(args.socket).is_absolute():
            parser.error("Xray uses its own socket and requires all deployment settings")
        from ipaddress import ip_address
        import re

        from .xray_agent import XrayAgent, XrayJournal
        from .xray_profile import parse_xray_request
        from .xray_runtime import SystemdXray, XrayRuntimeError

        try:
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", args.xray_netns):
                raise ValueError("invalid provisioned Xray namespace name")
            listen = ip_address(args.xray_listen)
            if not 1024 <= args.xray_port <= 65535:
                raise ValueError("invalid provisioned Xray UDP port")
            socket_parent = Path(args.socket).parent.stat()
            if socket_parent.st_uid != os.geteuid() or socket_parent.st_mode & 0o022:
                raise ValueError("Xray socket directory must be Agent-owned and protected")
            runtime = SystemdXray(args.xray, args.xray_runtime, args.xray_state, args.xray_netns)
            with XrayJournal(args.xray_state) as journal:
                agent = XrayAgent(runtime, journal, namespace=args.xray_netns,
                                  listener=(str(listen), args.xray_port))
                serve(args.socket, agent=agent, parse_request=parse_xray_request,
                      maintain=agent.maintain, **peer_options)
        except (OSError, ValueError, XrayRuntimeError) as exc:
            parser.error(str(exc))
        return
    if not any(option is not None for option in lab_options):
        serve(args.socket, **peer_options)
        return
    if not all(lab_options) or not args.lab_journal.is_absolute() or not Path(args.socket).is_absolute():
        parser.error("lab backend requires an exact selector, absolute journal/socket paths and verified namespace identities")

    from .linux_inventory import InventoryError, NamespaceIdentity
    from .nft_table import NftTable
    from .table_agent import TableAgent
    from .table_journal import TableJournal
    from .table_plan import LAB_NAMESPACE, parse_table_request

    try:
        device, inode = map(int, args.lab_netns.split(":"))
        host_device, host_inode = map(int, args.lab_host_netns.split(":"))
        socket_parent = Path(args.socket).parent.stat()
        if socket_parent.st_uid != os.geteuid() or socket_parent.st_mode & 0o022:
            raise ValueError("lab socket directory must be Agent-owned and protected")
        kernel = NftTable(
            NamespaceIdentity(LAB_NAMESPACE, args.lab_boot_id, device, inode),
            (host_device, host_inode), args.lab_table,
        )
        kernel.preflight()
        with TableJournal(args.lab_journal) as journal:
            agent = TableAgent(kernel, journal)
            serve(
                args.socket, agent=agent, parse_request=parse_table_request,
                maintain=agent.maintain, **peer_options,
            )
    except (InventoryError, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
