"""Locating and running the ``adb`` executable.

This module deliberately contains **no** installation logic (that lives in
:mod:`adbk.adb_installer`). Its two jobs are:

* resolve which ``adb`` to use, following a documented precedence order, and
* run ``adb`` as a normal subprocess using an argument list (never a shell
  string), so paths with spaces and Unicode work on every platform.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from adbk import platform_support as ps
from adbk.errors import AdbError, AdbNotFoundError

# How an ``adb`` executable was found. ``container`` means it was found on PATH
# while running inside our container image (i.e. the bundled copy).
AdbKind = Literal["explicit", "environment", "managed", "container", "path", "sdk"]

# A friendly, human-readable label for each kind, used by the doctor command.
ADB_KIND_LABELS: dict[AdbKind, str] = {
    "explicit": "explicit (--adb-path)",
    "environment": f"environment ({ps.ENV_ADB_PATH})",
    "managed": "application-managed",
    "container": "container-bundled",
    "path": "system PATH",
    "sdk": "Android SDK",
}

# Default timeout (seconds) for short informational adb calls like ``version``.
_QUICK_TIMEOUT = 20.0


@dataclass(frozen=True)
class AdbResolution:
    """The outcome of resolving which ``adb`` executable to use."""

    path: Path
    kind: AdbKind

    @property
    def label(self) -> str:
        return ADB_KIND_LABELS[self.kind]


@dataclass(frozen=True)
class DeviceInfo:
    """One entry from ``adb devices -l``."""

    serial: str
    state: str  # "device", "unauthorized", "offline", "no permissions", ...
    description: str = ""

    @property
    def is_ready(self) -> bool:
        return self.state == "device"


def managed_adb_path() -> Path:
    """Location of the ADB executable this application installs for itself."""

    return ps.managed_tools_dir() / ps.executable_name("adb")


def _looks_executable(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def resolve_adb(explicit_path: Path | None = None) -> AdbResolution | None:
    """Resolve an ``adb`` executable following the documented precedence.

    Order: ``--adb-path`` -> :data:`ENV_ADB_PATH` -> application-managed install ->
    system ``PATH`` -> common Android SDK locations. Returns ``None`` when nothing
    is found (the caller then decides whether to offer installation).

    An explicit ``--adb-path`` or environment value that does not point to a
    real file is treated as an error rather than silently skipped.
    """

    # 1. Explicit path from the command line.
    if explicit_path is not None:
        candidate = Path(explicit_path).expanduser()
        if _looks_executable(candidate):
            return AdbResolution(candidate, "explicit")
        raise AdbNotFoundError(f"--adb-path does not point to a file: {candidate}")

    # 2. Environment variable.
    env_value = os.environ.get(ps.ENV_ADB_PATH)
    if env_value:
        candidate = Path(env_value).expanduser()
        if _looks_executable(candidate):
            return AdbResolution(candidate, "environment")
        raise AdbNotFoundError(
            f"{ps.ENV_ADB_PATH} does not point to a file: {candidate}"
        )

    # 3. An installation this application previously managed.
    managed = managed_adb_path()
    if _looks_executable(managed):
        return AdbResolution(managed, "managed")

    # 4. Anything already on the system PATH.
    found = shutil.which(ps.executable_name("adb"))
    if found:
        kind: AdbKind = "container" if ps.in_container() else "path"
        return AdbResolution(Path(found), kind)

    # 5. Common Android SDK install locations (never modified, only read).
    for candidate in ps.android_sdk_adb_candidates():
        if _looks_executable(candidate):
            return AdbResolution(candidate, "sdk")

    return None


def query_version(adb_path: Path, *, timeout: float = _QUICK_TIMEOUT) -> str:
    """Run ``adb version`` from a specific executable and return its first line.

    Used both to validate a freshly installed ADB and for diagnostics.
    """

    try:
        completed = subprocess.run(
            [str(adb_path), "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AdbNotFoundError(f"adb executable not found: {adb_path}") from exc
    except OSError as exc:
        raise AdbError(f"Could not run adb at {adb_path}: {exc}") from exc

    if completed.returncode != 0:
        raise AdbError(
            f"'adb version' failed (exit {completed.returncode}): "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return completed.stdout.strip()


@dataclass
class AdbClient:
    """A thin wrapper that runs ``adb`` with a fixed executable and server target.

    ``server_host``/``server_port`` map to adb's ``-H``/``-P`` options and are
    how the container reaches the host's ADB server. They are optional so native
    execution simply talks to the local server.
    """

    adb_path: Path
    server_host: str | None = None
    server_port: int | None = None
    _base: list[str] = field(init=False, repr=False, default_factory=list)

    def __post_init__(self) -> None:
        base = [str(self.adb_path)]
        if self.server_host:
            base += ["-H", self.server_host]
        if self.server_port is not None:
            base += ["-P", str(self.server_port)]
        self._base = base

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``adb`` with the given arguments as a real argument list."""

        command = [*self._base, *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AdbNotFoundError(f"adb executable not found: {self.adb_path}") from exc
        except OSError as exc:
            raise AdbError(f"Could not run adb: {exc}") from exc

        if check and completed.returncode != 0:
            raise AdbError(
                f"adb {' '.join(args)} failed (exit {completed.returncode}): "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        return completed

    def version(self, *, timeout: float = _QUICK_TIMEOUT) -> str:
        """Return adb's version line, honouring the configured server target."""

        completed = self.run(["version"], timeout=timeout)
        for line in completed.stdout.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return completed.stdout.strip()

    def devices(self, *, timeout: float = _QUICK_TIMEOUT) -> list[DeviceInfo]:
        """Return connected devices as parsed from ``adb devices -l``."""

        completed = self.run(["devices", "-l"], timeout=timeout)
        return parse_devices(completed.stdout)


def parse_devices(output: str) -> list[DeviceInfo]:
    """Parse the text output of ``adb devices -l`` into :class:`DeviceInfo`."""

    devices: list[DeviceInfo] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("list of devices"):
            continue
        if line.startswith("*"):  # adb server start-up chatter
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = parts[0]
        state = parts[1]
        # "no permissions" appears as two words followed by details.
        if state == "no" and len(parts) >= 3 and parts[2].startswith("permission"):
            state = "no permissions"
            description = " ".join(parts[3:])
        else:
            description = " ".join(parts[2:])
        devices.append(DeviceInfo(serial=serial, state=state, description=description))
    return devices
