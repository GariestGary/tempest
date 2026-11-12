"""Read-only, VM-only capture from an already entered Linux network namespace.

There is no setns, ip netns exec, shell, IPC method, or network mutation here.
The operator must prepare the namespace and its identity independently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .linux_inventory import (
    InventoryError, NamespaceIdentity, bind_ip_family, parse_linux_inventory,
)
from .registry import InventorySnapshot


_TRUSTED_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
_COMMANDS = (
    ("link", "ip", "-j", "link"),
    ("rule4", "ip", "-4", "-j", "rule"),
    ("rule6", "ip", "-6", "-j", "rule"),
    ("route4", "ip", "-4", "-j", "route", "show", "table", "all"),
    ("route6", "ip", "-6", "-j", "route", "show", "table", "all"),
    ("nft", "nft", "-j", "list", "ruleset"),
)
_MAX_FEED_BYTES = 8 * 1024 * 1024
_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class CapturedFeed:
    name: str
    argv: tuple[str, ...]
    stdout: str
    started_at: str
    ended_at: str


@dataclass(frozen=True)
class Capture:
    snapshot: InventorySnapshot
    feeds: tuple[CapturedFeed, ...]


def _current_identity(name: str) -> NamespaceIdentity:
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        stat = Path("/proc/self/ns/net").stat()
        return NamespaceIdentity(name, boot_id, stat.st_dev, stat.st_ino)
    except (OSError, UnicodeError) as exc:
        raise InventoryError("cannot read namespace identity") from exc


def _check_identity(expected: NamespaceIdentity) -> None:
    if _current_identity(expected.name) != expected:
        raise InventoryError("target namespace identity changed")
    try:
        host = Path("/proc/1/ns/net").stat()
    except OSError as exc:
        raise InventoryError("cannot verify host namespace") from exc
    if (host.st_dev, host.st_ino) == (expected.netns_device, expected.netns_inode):
        raise InventoryError("target is the host namespace")


def _binary(name: str) -> str:
    executable = shutil.which(name, path=_TRUSTED_PATH)
    if not executable or not Path(executable).is_file():
        raise InventoryError(f"required binary unavailable: {name}")
    return executable


def capture_linux_inventory(expected: NamespaceIdentity) -> Capture:
    """Capture six fixed commands; any failure invalidates the entire result."""
    if sys.platform != "linux":
        raise InventoryError("Linux VM required")
    _check_identity(expected)
    binaries = {name: _binary(name) for name in ("ip", "nft")}
    feeds: list[CapturedFeed] = []
    for name, binary, *args in _COMMANDS:
        _check_identity(expected)
        argv = (binaries[binary], *args)
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = subprocess.run(
                argv, shell=False, stdin=subprocess.DEVNULL, capture_output=True,
                timeout=_TIMEOUT_SECONDS, check=False,
                env={"PATH": _TRUSTED_PATH, "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InventoryError(f"{name} capture failed") from exc
        ended = datetime.now(timezone.utc).isoformat()
        _check_identity(expected)
        if result.returncode != 0 or result.stderr or not result.stdout:
            raise InventoryError(f"{name} capture returned error or incomplete output")
        if len(result.stdout) > _MAX_FEED_BYTES:
            raise InventoryError(f"{name} feed exceeds size limit")
        try:
            stdout = result.stdout.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise InventoryError(f"{name} feed is not UTF-8") from exc
        feeds.append(CapturedFeed(name, argv, stdout, started, ended))
    raw = {feed.name: feed.stdout for feed in feeds}
    rules = json.loads(bind_ip_family(raw["rule4"], "inet", "rule"))
    rules += json.loads(bind_ip_family(raw["rule6"], "inet6", "rule"))
    routes = json.loads(bind_ip_family(raw["route4"], "inet", "route"))
    routes += json.loads(bind_ip_family(raw["route6"], "inet6", "route"))
    snapshot = parse_linux_inventory(
        links_json=raw["link"], rules_json=json.dumps(rules),
        routes_json=json.dumps(routes), nft_json=raw["nft"], namespace=expected,
    )
    _check_identity(expected)
    if not snapshot.complete or any(obj.proof is not None for obj in snapshot.objects):
        raise InventoryError("capture cannot establish ownership")
    return Capture(snapshot, tuple(feeds))


def write_capture(capture: Capture, directory: Path) -> None:
    """Write private raw evidence only after a complete successful capture."""
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(directory, 0o700)
    manifest = {
        "namespace_identity": capture.snapshot.namespace_identity,
        "revision": capture.snapshot.revision,
        "atomic": False,
        "ownership_proof": False,
        "feeds": [],
    }
    for feed in capture.feeds:
        name = f"{feed.name}.json"
        data = feed.stdout.encode("utf-8")
        path = directory / name
        with path.open("xb") as output:
            os.chmod(path, 0o600)
            output.write(data)
        manifest["feeds"].append({
            "name": name, "argv": feed.argv, "started_at": feed.started_at,
            "ended_at": feed.ended_at, "exit_code": 0, "stderr": "",
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    path = directory / "manifest.json"
    with path.open("x", encoding="utf-8") as output:
        os.chmod(path, 0o600)
        json.dump(manifest, output, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="VM-only read-only TEMPEST inventory capture")
    parser.add_argument("--name", required=True)
    parser.add_argument("--boot-id", required=True)
    parser.add_argument("--netns-device", type=int, required=True)
    parser.add_argument("--netns-inode", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        expected = NamespaceIdentity(args.name, args.boot_id, args.netns_device, args.netns_inode)
        capture = capture_linux_inventory(expected)
        write_capture(capture, args.output_dir)
    except (InventoryError, OSError, ValueError) as exc:
        parser.exit(1, f"capture failed: {exc}\n")
    print(f"Complete read-only capture: {args.output_dir} ({len(capture.snapshot.objects)} unproven objects)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
