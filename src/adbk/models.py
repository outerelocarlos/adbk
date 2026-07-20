"""Domain models for the backup/restore engine.

All enums subclass :class:`enum.StrEnum`, so they serialise straight to JSON as
their string value and read back cleanly. Everything here is plain data - no I/O,
no platform knowledge - which keeps the planner, tree, manifest and transfer
logic easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Operation(StrEnum):
    BACKUP = "backup"
    RESTORE = "restore"


class TransferMode(StrEnum):
    COPY = "copy"
    SAFE_MOVE = "safe_move"


class EntryType(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


class AccessState(StrEnum):
    """How a configured path presents on the device."""

    READABLE = "present_readable"
    INACCESSIBLE = "present_inaccessible"
    MISSING = "missing"
    ROOT_ONLY = "root_only"
    EMPTY = "empty"


class CopyResult(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    COPIED = "copied"
    SKIPPED = "skipped"
    FAILED = "failed"


class VerificationResult(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNVERIFIABLE = "unverifiable"


class DeletionResult(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    DELETED = "deleted"
    KEPT = "kept"
    FAILED = "failed"


class ManifestState(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AppAvailability(StrEnum):
    """How an installed app could be brought back on a new device."""

    BACKUP = "backup"  # its APK is stored in this backup, so we can install it
    STORE = "store"  # expected to be installable from the app store
    UNAVAILABLE = "unavailable"  # neither: it would have to be found elsewhere
    UNKNOWN = "unknown"  # could not be determined


class ConflictPolicy(StrEnum):
    SKIP_IDENTICAL = "skip_identical"
    OVERWRITE_IF_DIFFERENT = "overwrite_if_different"
    ALWAYS_OVERWRITE = "always_overwrite"
    RENAME = "rename"
    ASK = "ask"
    ABORT = "abort"


@dataclass(frozen=True)
class Category:
    """A named group of candidate Android paths, defined in configuration."""

    name: str
    paths: tuple[str, ...]
    description: str = ""
    default_selected: bool = True


@dataclass(frozen=True)
class DeviceIdentity:
    """Identity of the connected device, recorded in the manifest."""

    serial: str
    model: str = ""
    android_version: str = ""
    root_available: bool = False


@dataclass
class DiscoveredPath:
    """The result of classifying one configured category path on the device."""

    category: str
    configured_path: str
    android_path: str  # normalized
    access_state: AccessState
    entry_type: EntryType | None = None
    reason: str = ""


@dataclass
class TreeNode:
    """A node in the on-device folder tree used for display and selection."""

    name: str
    android_path: str
    entry_type: EntryType
    access_state: AccessState = AccessState.READABLE
    size: int | None = None
    mtime: int | None = None
    warning: str | None = None
    children: list[TreeNode] = field(default_factory=list)
    included: bool = True

    @property
    def direct_files(self) -> list[TreeNode]:
        return [c for c in self.children if c.entry_type is EntryType.FILE]

    @property
    def direct_dirs(self) -> list[TreeNode]:
        return [c for c in self.children if c.entry_type is EntryType.DIRECTORY]

    def total_size(self) -> int:
        if self.entry_type is EntryType.FILE:
            return self.size or 0
        return sum(child.total_size() for child in self.children)

    def file_count(self) -> int:
        if self.entry_type is EntryType.FILE:
            return 1
        return sum(child.file_count() for child in self.children)


@dataclass
class PlanEntry:
    """One file scheduled for transfer, with its portable local path."""

    android_path: str
    local_relative_path: str
    entry_type: EntryType
    category: str
    size: int | None = None
    mtime: int | None = None
    access_state: AccessState = AccessState.READABLE


@dataclass
class Plan:
    """The full set of files to transfer plus what was skipped and why."""

    operation: Operation
    mode: TransferMode
    entries: list[PlanEntry] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size or 0 for entry in self.entries)

    @property
    def file_count(self) -> int:
        return sum(1 for entry in self.entries if entry.entry_type is EntryType.FILE)
