"""Kernel adapter for one empty inet table in a prepared lab namespace."""

import json
import socket
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from .linux_inventory import InventoryError, NamespaceIdentity, reject_unproven_desired
from .linux_inventory_capture import (
    Capture, _binary, _check_identity, _TRUSTED_PATH, capture_linux_inventory,
)
from .registry import ClaimKind, NetworkObject, ObjectKind, ResourceClaim
from .table_journal import TableEvidence
from .table_plan import LAB_NAMESPACE, CreateNftTable, TablePlan

# Linux UAPI: netlink.h, nfnetlink.h, nf_tables.h and netfilter.h.
_NETLINK_NETFILTER = 12
_NFNL_SUBSYS_NFTABLES = 10
_NFNL_BATCH_BEGIN, _NFNL_BATCH_END = 16, 17
_NFT_MSG_DELTABLE = (_NFNL_SUBSYS_NFTABLES << 8) | 2
_NFTA_TABLE_HANDLE = 4
_NFPROTO_INET = 1
_NLM_F_REQUEST, _NLM_F_ACK, _NLM_F_NONREC = 1, 4, 0x100
_NLMSG_ERROR = 2
_HEADER = struct.Struct("=IHHII")


def _batch_message(kind: int, seq: int, family: int, flags: int, attrs: bytes = b"") -> bytes:
    body = struct.pack("!BBH", family, 0, _NFNL_SUBSYS_NFTABLES if not family else 0) + attrs
    return _HEADER.pack(_HEADER.size + len(body), kind, _NLM_F_REQUEST | flags, seq, 0) + body


def _delete_empty_table(handle: int) -> None:
    if type(handle) is not int or not 0 < handle <= 2**32 - 1:
        raise InventoryError("invalid rollback table handle")
    # nft's CLI deletion is recursive. The kernel must check emptiness in the
    # same transaction as deletion. A handle also excludes a replacement table.
    attribute = struct.pack("=HH", 12, _NFTA_TABLE_HANDLE) + struct.pack("!Q", handle)
    batch = (
        _batch_message(_NFNL_BATCH_BEGIN, 1, 0, 0)
        + _batch_message(_NFT_MSG_DELTABLE, 2, _NFPROTO_INET, _NLM_F_ACK | _NLM_F_NONREC, attribute)
        + _batch_message(_NFNL_BATCH_END, 3, 0, _NLM_F_ACK)
    )
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, _NETLINK_NETFILTER) as connection:
        connection.settimeout(3)
        connection.bind((0, 0))
        if connection.sendto(batch, (0, 0)) != len(batch):
            raise InventoryError("incomplete nft rollback request")
        remaining = {2, 3}
        while remaining:
            data, _, flags, sender = connection.recvmsg(4096)
            if sender != (0, 0) or flags & socket.MSG_TRUNC or not data:
                raise InventoryError("invalid nft rollback acknowledgement")
            offset = 0
            while offset < len(data):
                if len(data) - offset < _HEADER.size + 4:
                    raise InventoryError("truncated nft rollback acknowledgement")
                size, kind, _, seq, _ = _HEADER.unpack_from(data, offset)
                if not _HEADER.size + 4 <= size <= len(data) - offset or kind != _NLMSG_ERROR:
                    raise InventoryError("unexpected nft rollback response")
                error = struct.unpack_from("=i", data, offset + _HEADER.size)[0]
                if error or seq not in remaining:
                    raise InventoryError("kernel refused or could not confirm nonrecursive rollback")
                remaining.remove(seq)
                offset += (size + 3) & ~3


class NftTable:
    def __init__(
        self, namespace: NamespaceIdentity, host_namespace: tuple[int, int], name: str,
    ) -> None:
        if namespace.name != LAB_NAMESPACE or min(host_namespace) <= 0:
            raise InventoryError("verified lab and host namespace identities are required")
        self.namespace = namespace
        self.host_namespace = host_namespace
        self.operation = CreateNftTable(kind="create_nft_table", family="inet", name=name)

    def preflight(self) -> None:
        _check_identity(self.namespace, host_namespace=self.host_namespace)
        status = dict(
            line.split(":\t", 1)
            for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines()
            if ":\t" in line
        )
        # Lab launcher enters the namespace and then drops every other capability.
        net_admin = 1 << 12
        masks = ("CapEff", "CapPrm", "CapBnd", "CapInh", "CapAmb")
        if status.get("NoNewPrivs") != "1" or any(
            int(status.get(mask, "0"), 16) != net_admin for mask in masks
        ):
            raise InventoryError("Agent requires only CAP_NET_ADMIN and NoNewPrivileges")

    def _capture(self) -> Capture:
        self.preflight()
        capture = capture_linux_inventory(self.namespace, host_namespace=self.host_namespace)
        links = json.loads(next(feed.stdout for feed in capture.feeds if feed.name == "link"))
        if [link["ifname"] for link in links] != ["lo"]:
            raise InventoryError("expected the isolated lab namespace with only loopback")
        return capture

    def _intent(self, plan: TablePlan) -> NetworkObject:
        claim = ResourceClaim(
            ClaimKind.NFT_TABLE_NAME,
            json.dumps(["inet", self.operation.name, None, None], separators=(",", ":")),
        )
        return NetworkObject(
            self.operation.name, ObjectKind.NFT_TABLE, self.namespace.name, "tempest",
            plan.generation_id, plan.generation, (claim,), plan.fingerprint(),
        )

    def require_absent(self, plan: TablePlan) -> None:
        reject_unproven_desired(self._capture().snapshot, (self._intent(plan),), namespace=self.namespace)

    def _nft(self, *args: str) -> str:
        self.preflight()
        try:
            result = subprocess.run(
                (_binary("nft"), *args), check=False, shell=False, stdin=subprocess.DEVNULL,
                capture_output=True, timeout=3, env={"PATH": _TRUSTED_PATH, "LC_ALL": "C"},
            )
        except subprocess.TimeoutExpired as exc:
            raise InventoryError("nft command timed out") from exc
        if result.returncode or result.stderr:
            raise InventoryError("nft refused the exact table operation")
        return result.stdout.decode("utf-8")

    def create(self, transaction_id: UUID) -> None:
        # Exclusive create returns failure if another actor won the selector.
        self._nft("create", "table", "inet", self.operation.name, "{",
                  "comment", f'"tempest:{transaction_id.hex}"', ";", "}")

    def verify(self, plan: TablePlan, transaction_id: UUID) -> TableEvidence:
        inventory = self._capture()
        claim = self._intent(plan).claims[0]
        tables = [obj for obj in inventory.snapshot.objects if claim in obj.claims]
        if len(tables) != 1:
            raise InventoryError("owned table selector was not uniquely observed")
        if any(
            obj.kind in {ObjectKind.NFT_CHAIN, ObjectKind.NFT_RULE}
            and json.loads(obj.kernel_key)[:2] == ["inet", self.operation.name]
            for obj in inventory.snapshot.objects
        ):
            raise InventoryError("owned table has child objects")

        # Use a fresh exact table listing. The JSON captured above may omit table
        # comments in some nft versions. Here we require the transaction marker,
        # handle, no table flags, and no child objects in the exact listing.
        name = self.operation.name
        lines = self._nft("-a", "list", "table", "inet", name).splitlines()
        prefix = f"table inet {name} {{ # handle "
        if (len(lines) != 3 or not lines[0].startswith(prefix)
                or lines[1].strip() != f'comment "tempest:{transaction_id.hex}"' or lines[2] != "}"):
            raise InventoryError("table marker or empty contents could not be verified")
        handle = int(lines[0][len(prefix):])
        nft_feed = next(feed.stdout for feed in inventory.feeds if feed.name == "nft")
        rows = json.loads(nft_feed)["nftables"]
        row = next(entry["table"] for entry in rows if "table" in entry
                   and entry["table"]["family"] == "inet" and entry["table"]["name"] == name)
        if row.get("handle") != handle:
            raise InventoryError("nft table handle changed between observations")
        return TableEvidence(
            fingerprint=tables[0].fingerprint, handle=handle,
            verified_at=datetime.now(timezone.utc),
        )

    def delete(self, handle: int) -> None:
        self.preflight()
        _delete_empty_table(handle)
