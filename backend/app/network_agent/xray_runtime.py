"""Manage the fixed Xray and traffic-probe units through systemd."""

import hashlib
import os
import stat
import subprocess
import tempfile
import time
from pathlib import Path

READINESS_SECONDS = 45.0
XRAY_UNIT = "tempest-xray.service"
PROBE_UNIT = "tempest-xray-probe.service"
AWG_UNIT = "tempest-awgproxy.service"
AWG_BINARY = Path("/opt/tempest/bin/awgproxy")
AWG_AMD64_SHA256 = "e0fe256ad362221026a15a372a246b21026b0405e4dc32ab8f2c87c92543686e"
XRAY_AMD64_SHA256 = "cc4b84d3069569f297a61f04b914f2bb37e9d202fd7b84cb50def1fc30fd40e1"


class XrayRuntimeError(RuntimeError):
    pass


class SystemdXray:
    def __init__(self, binary: Path, directory: Path, state_directory: Path, namespace: str):
        if not all(path.is_absolute() for path in (binary, directory, state_directory)):
            raise ValueError("Xray deployment paths must be absolute")
        self._binary = binary
        self._directory = directory
        self._state_directory = state_directory
        self._namespace = Path("/run/netns", namespace)
        self._active = directory / "active.json"
        self._awg_active = directory / "active-awg.conf"
        self._awg_invocation = None
        self._check_namespace()
        info = directory.stat(follow_symlinks=False)
        if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o2750
                or not stat.S_ISDIR(info.st_mode)):
            raise ValueError("Xray runtime directory must be Agent-owned, private and setgid")
        info = binary.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Xray must be a protected operator-installed binary")
        with binary.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != XRAY_AMD64_SHA256:
                raise ValueError("Xray 26.10.31 amd64 checksum mismatch")

    def _check_namespace(self) -> None:
        current = os.stat("/proc/self/ns/net")
        if not os.path.samestat(current, self._namespace.stat()):
            raise XrayRuntimeError("Xray Agent must run in the provisioned network namespace")
        # The unprivileged unit may not inspect a different UID's procfs names.
        # The operator also verifies the PID 1 identity when provisioning the mount.
        try:
            host = os.stat("/proc/1/ns/net")
        except PermissionError:
            return
        if os.path.samestat(current, host):
            raise XrayRuntimeError("Xray Agent must not run in the PID 1 network namespace")

    @staticmethod
    def _run(command: list[str], message: str, *, output: bool = False, timeout: float = 7) -> str:
        try:
            result = subprocess.run(
                command, check=True, timeout=timeout, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if output else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, text=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise XrayRuntimeError(message) from exc
        return result.stdout if output else ""

    def _systemctl(
        self, operation: str, unit: str, *options: str, output: bool = False, timeout: float = 7,
    ) -> str:
        return self._run(
            ["/usr/bin/systemctl", "--no-ask-password", "--no-pager", operation, *options, unit],
            f"systemd {operation} failed for {unit}", output=output, timeout=timeout,
        )

    def _install(self, path: Path, payload: bytes) -> None:
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".xray-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fchmod(stream.fileno(), 0o640)
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._sync_directory()
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _sync_directory(self) -> None:
        fd = os.open(self._directory, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def validate(self, payload: bytes) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", dir=self._state_directory) as candidate:
            candidate.write(payload)
            candidate.flush()
            self._run(
                [str(self._binary), "run", "-test", "-config", candidate.name],
                "Xray rejected the candidate configuration",
            )

    def validate_awg(self, payload: bytes) -> None:
        try:
            info = AWG_BINARY.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise XrayRuntimeError("AWG binary is not protected")
            with AWG_BINARY.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != AWG_AMD64_SHA256:
                    raise XrayRuntimeError("AWG binary checksum mismatch")
            with tempfile.NamedTemporaryFile(suffix=".conf", dir=self._state_directory) as candidate:
                candidate.write(payload)
                candidate.flush()
                self._run([str(AWG_BINARY), "-n", "-c", candidate.name], "AWG rejected the candidate")
        except OSError as exc:
            raise XrayRuntimeError("AWG binary or private configuration unavailable") from exc

    def _identity(self, unit: str = XRAY_UNIT) -> str | None:
        properties = dict(line.split("=", 1) for line in self._systemctl(
            "show", unit, "--property=ActiveState,MainPID,InvocationID", output=True,
        ).splitlines())
        if properties.get("ActiveState") in {"inactive", "failed"} and properties.get("MainPID") == "0":
            return None
        invocation = properties.get("InvocationID", "")
        if (properties.get("ActiveState") != "active" or not invocation
                or int(properties.get("MainPID", "0")) <= 0):
            raise XrayRuntimeError("Xray unit has no stable active invocation")
        return invocation

    def stop(self) -> None:
        self._systemctl("stop", XRAY_UNIT)
        if self._identity() is not None:
            raise XrayRuntimeError("Xray ingress did not stop")
        self._active.unlink(missing_ok=True)
        if os.path.lexists(self._awg_active) or self._awg_invocation is not None:
            self._systemctl("stop", AWG_UNIT)
            if self._identity(AWG_UNIT) is not None:
                raise XrayRuntimeError("AWG unit did not stop")
            self._awg_active.unlink(missing_ok=True)
            self._awg_invocation = None
        self._sync_directory()

    def activate_awg(self, payload: bytes) -> None:
        self._check_namespace()
        self._install(self._awg_active, payload)
        self._systemctl("start", AWG_UNIT)
        self._awg_invocation = self._identity(AWG_UNIT)
        if self._awg_invocation is None:
            raise XrayRuntimeError("AWG unit did not start")

    def assert_awg(self, payload: bytes) -> None:
        self._check_namespace()
        if (self._awg_invocation is None or self._identity(AWG_UNIT) != self._awg_invocation
                or self._awg_active.read_bytes() != payload):
            raise XrayRuntimeError("AWG runtime differs from the expected generation")

    def activate(self, payload: bytes | None) -> str | None:
        self._check_namespace()
        if payload is None:
            return None
        self._install(self._active, payload)
        self._systemctl("start", XRAY_UNIT)
        identity = self._identity()
        if identity is None:
            raise XrayRuntimeError("Xray unit did not start")
        return identity

    def assert_current(self, payload: bytes | None, identity: str | None) -> None:
        self._check_namespace()
        if self._identity() != identity:
            raise XrayRuntimeError("Xray unit invocation changed")
        if payload is None:
            matches = not os.path.lexists(self._active)
        else:
            matches = self._active.read_bytes() == payload
        if not matches:
            raise XrayRuntimeError("Xray active config differs from the expected generation")

    def verify(self, payload: bytes | None, identity: str | None) -> None:
        self.assert_current(payload, identity)
        if payload is not None:
            if identity is None:
                raise XrayRuntimeError("Xray ingress is closed")
            deadline = time.monotonic() + READINESS_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise XrayRuntimeError("Xray traffic readiness deadline expired")
                probe_state = self._systemctl(
                    "show", PROBE_UNIT, "--property=ActiveState", "--value", output=True,
                ).strip()
                if probe_state not in {"inactive", "failed"}:
                    self.assert_current(payload, identity)
                    time.sleep(min(0.25, remaining))
                    continue
                try:
                    self._systemctl("start", PROBE_UNIT, timeout=min(7, remaining))
                except XrayRuntimeError:
                    # An existing WireGuard client may need to rekey against
                    # the new engine. The same invocation must stay active.
                    self.assert_current(payload, identity)
                    time.sleep(min(0.25, max(0, deadline - time.monotonic())))
                else:
                    self.assert_current(payload, identity)
                    return
