"""Talking to the Android device over ADB.

The rest of the engine depends on the small :class:`DeviceInterface` protocol,
not on ADB directly, so discovery, planning, transfer and restore can all be
tested with an in-memory fake device and no real phone.

``AdbDevice`` is the real implementation. Every remote path is single-quoted for
the device shell so spaces and Unicode in filenames work, and a failed listing is
raised as :class:`DeviceAccessError` - never silently treated as "empty".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from adbk.adb import AdbClient
from adbk.errors import DeviceAccessError, DeviceError, TransferError
from adbk.models import DeviceIdentity, EntryType


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RawEntry:
    name: str
    entry_type: EntryType
    size: int | None = None
    mtime: int | None = None


@runtime_checkable
class DeviceInterface(Protocol):
    """The minimal surface the engine needs from a device."""

    def identity(self) -> DeviceIdentity: ...
    def has_root(self) -> bool: ...
    def exists(self, path: str) -> bool: ...
    def is_dir(self, path: str) -> bool: ...
    def is_readable(self, path: str) -> bool: ...
    def list_dir(self, path: str) -> list[RawEntry]: ...
    def list_recursive(self, root: str) -> DeviceListing: ...
    def stat(self, path: str) -> RawEntry | None: ...
    def disk_usage(self, path: str) -> int | None: ...
    def disk_usage_tree(self, path: str) -> dict[str, int]: ...
    def sha256(self, path: str) -> str | None: ...
    def pull(self, remote: str, local: Path) -> None: ...
    def push(self, local: Path, remote: str) -> None: ...
    def delete_file(self, remote: str) -> bool: ...
    def delete_dir(self, remote: str) -> bool: ...


def _parse_entry_line(line: str) -> RawEntry | None:
    """Parse a single ``ls -l`` entry line into a RawEntry (or None to skip)."""

    parts = line.split(None, 7)
    if len(parts) < 8:
        return None
    perms, _links, _user, _group, size_str, _date, _time, name = parts
    kind = perms[:1]
    if kind == "l":  # symlink: never follow
        return None
    if name in {".", ".."}:
        return None
    if kind == "d":
        return RawEntry(name, EntryType.DIRECTORY)
    try:
        size: int | None = int(size_str)
    except ValueError:
        size = None
    return RawEntry(name, EntryType.FILE, size)


def parse_ls_long(output: str) -> list[RawEntry]:
    """Parse ``ls -lA`` output into entries (one shell call per directory).

    Symlinks are skipped (we never follow them). Filenames may contain spaces:
    the name is everything after the 7 fixed metadata columns.
    """

    entries: list[RawEntry] = []
    for raw in output.splitlines():
        line = raw.rstrip("\n")
        if not line.strip() or line.startswith("total "):
            continue
        entry = _parse_entry_line(line)
        if entry is not None:
            entries.append(entry)
    return entries


@dataclass(frozen=True)
class FlatEntry:
    """A file or directory with its full device path (from a recursive listing)."""

    path: str
    entry_type: EntryType
    size: int | None = None


@dataclass
class DeviceListing:
    """The result of a one-shot recursive listing of a directory tree."""

    entries: list[FlatEntry]
    inaccessible: set[str]


def _looks_like_entry(line: str) -> bool:
    return bool(line) and line[0] in "-dlbcps" and len(line.split(None, 7)) >= 8


def parse_ls_recursive(stdout: str, root: str) -> list[FlatEntry]:
    """Parse ``ls -lAR`` output (headers + per-directory listings) into a flat list."""

    entries: list[FlatEntry] = []
    current = root.rstrip("/")
    for raw in stdout.splitlines():
        line = raw.rstrip("\n")
        if not line.strip() or line.startswith("total "):
            continue
        if not _looks_like_entry(line) and line.endswith(":"):
            current = line[:-1].rstrip("/")
            continue
        entry = _parse_entry_line(line)
        if entry is None:
            continue
        entries.append(FlatEntry(f"{current}/{entry.name}", entry.entry_type, entry.size))
    return entries


def parse_ls_errors(stderr: str) -> set[str]:
    """Extract paths that ``ls`` could not read (so they are never seen as empty)."""

    inaccessible: set[str] = set()
    for raw in stderr.splitlines():
        line = raw.strip()
        # e.g. "ls: /sdcard/x: Permission denied"
        if line.startswith("ls:") and ":" in line[3:]:
            middle = line[3:].rsplit(":", 1)[0].strip()
            if middle:
                inaccessible.add(middle)
    return inaccessible


def shell_quote(value: str) -> str:
    """Single-quote a string for a POSIX device shell (handles spaces/Unicode)."""

    return "'" + value.replace("'", "'\\''") + "'"


class AdbDevice:
    """A :class:`DeviceInterface` backed by a real ``adb`` connection."""

    def __init__(self, client: AdbClient, serial: str | None = None) -> None:
        self._client = client
        self._serial = serial
        self._sha_command: list[str] | None | bool = False  # False = not yet detected

    # -- low-level ------------------------------------------------------------

    def _shell(self, command: str) -> CommandResult:
        args = ["shell"]
        if self._serial:
            args = ["-s", self._serial, *args]
        completed = self._client.run([*args, command], check=False)
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    def _getprop(self, prop: str) -> str:
        result = self._shell(f"getprop {shell_quote(prop)}")
        return result.stdout.strip()

    # -- identity / root ------------------------------------------------------

    def identity(self) -> DeviceIdentity:
        serial = self._serial or "unknown"
        return DeviceIdentity(
            serial=serial,
            model=self._getprop("ro.product.model"),
            android_version=self._getprop("ro.build.version.release"),
            root_available=self.has_root(),
        )

    def has_root(self) -> bool:
        result = self._shell("su -c 'id -u' 2>/dev/null")
        return result.returncode == 0 and result.stdout.strip() == "0"

    # -- classification -------------------------------------------------------

    def exists(self, path: str) -> bool:
        result = self._shell(f"[ -e {shell_quote(path)} ] && echo __YES__ || echo __NO__")
        return "__YES__" in result.stdout

    def is_dir(self, path: str) -> bool:
        result = self._shell(f"[ -d {shell_quote(path)} ] && echo __YES__ || echo __NO__")
        return "__YES__" in result.stdout

    def is_readable(self, path: str) -> bool:
        result = self._shell(f"[ -r {shell_quote(path)} ] && echo __YES__ || echo __NO__")
        return "__YES__" in result.stdout

    def list_dir(self, path: str) -> list[RawEntry]:
        # One `ls -lA` call per directory yields names, types and sizes together,
        # so we avoid a separate `stat` per file (crucial for multi-GB folders).
        result = self._shell(f"ls -lA {shell_quote(path)}")
        if result.returncode != 0:
            raise DeviceAccessError(
                f"Cannot list {path}: {result.stderr.strip() or 'permission denied'}"
            )
        return parse_ls_long(result.stdout)

    def list_recursive(self, root: str) -> DeviceListing:
        # A single `ls -lAR` walks the whole tree in one round trip. Errors go to
        # stderr, so unreadable sub-dirs are recorded, never seen as empty.
        result = self._shell(f"ls -lAR {shell_quote(root)}")
        entries = parse_ls_recursive(result.stdout, root)
        return DeviceListing(entries, parse_ls_errors(result.stderr))

    def disk_usage(self, path: str) -> int | None:
        # `du -sk` returns a whole-folder size in one call (kibibytes).
        result = self._shell(f"du -sk {shell_quote(path)}")
        if result.returncode != 0:
            return None
        token = result.stdout.strip().split()
        if not token:
            return None
        try:
            return int(token[0]) * 1024
        except ValueError:
            return None

    def disk_usage_tree(self, path: str) -> dict[str, int]:
        # `du -k -d 1` walks the folder once and reports its own size plus every
        # immediate sub-folder's size (empty ones show as a few KiB).
        result = self._shell(f"du -k -d 1 {shell_quote(path)}")
        sizes: dict[str, int] = {}
        for line in result.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            kib, entry = parts
            try:
                sizes[entry.strip()] = int(kib) * 1024
            except ValueError:
                continue
        return sizes

    def stat(self, path: str) -> RawEntry | None:
        # '%s' size, '%Y' mtime epoch, '%F' human type
        result = self._shell(f"stat -c '%s|%Y|%F' {shell_quote(path)}")
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split("|")
        if len(parts) != 3:
            return None
        size_text, mtime_text, kind = parts
        entry_type = EntryType.DIRECTORY if "directory" in kind else EntryType.FILE
        name = path.rstrip("/").rsplit("/", 1)[-1]
        try:
            size = int(size_text)
            mtime = int(mtime_text)
        except ValueError:
            size, mtime = None, None
        return RawEntry(name, entry_type, size, mtime)

    def _detect_sha_command(self) -> list[str] | None:
        for candidate in (["sha256sum"], ["toybox", "sha256sum"]):
            probe = self._shell(f"{' '.join(candidate)} /dev/null")
            if probe.returncode == 0:
                return candidate
        return None

    def sha256(self, path: str) -> str | None:
        if self._sha_command is False:
            self._sha_command = self._detect_sha_command()
        command = self._sha_command
        if not command or command is True:
            return None
        result = self._shell(f"{' '.join(command)} {shell_quote(path)}")
        if result.returncode != 0:
            return None
        token = result.stdout.strip().split()
        if not token:
            return None
        digest = token[0].lower()
        return digest if len(digest) == 64 else None

    # -- transfer -------------------------------------------------------------

    def pull(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        args = ["pull", remote, str(local)]
        if self._serial:
            args = ["-s", self._serial, *args]
        completed = self._client.run(args, check=False)
        if completed.returncode != 0 or not local.exists():
            raise TransferError(
                f"adb pull failed for {remote}: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )

    def push(self, local: Path, remote: str) -> None:
        args = ["push", str(local), remote]
        if self._serial:
            args = ["-s", self._serial, *args]
        completed = self._client.run(args, check=False)
        if completed.returncode != 0:
            raise TransferError(
                f"adb push failed for {local}: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )

    def delete_file(self, remote: str) -> bool:
        self._shell(f"rm -f {shell_quote(remote)}")
        return not self.exists(remote)

    def delete_dir(self, remote: str) -> bool:
        # rmdir only removes an empty directory, which is exactly what we want:
        # a directory is deleted only after its children are already gone.
        self._shell(f"rmdir {shell_quote(remote)}")
        return not self.exists(remote)


def make_device(client: AdbClient, serial: str | None) -> DeviceInterface:
    """Factory returning a :class:`DeviceInterface` for a resolved client."""

    device: DeviceInterface = AdbDevice(client, serial)
    if not isinstance(device, DeviceInterface):  # pragma: no cover - sanity guard
        raise DeviceError("AdbDevice does not satisfy DeviceInterface")
    return device
