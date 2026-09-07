"""Per-file transfer: verified copy and verified safe move.

The rules are enforced strictly:

* copy leaves the source untouched;
* safe move deletes the source **only** when the destination exists, the sizes
  match, and the source's device-side SHA-256 equals the local SHA-256;
* if the source cannot be hashed on the device, it is never deleted - the file
  falls back to copy-only and the reason is recorded;
* no new deletion is started once cancellation has been requested.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from adbk import filters
from adbk.device import DeviceInterface
from adbk.errors import TransferError
from adbk.manifest import Manifest, ManifestEntry
from adbk.models import (
    CopyResult,
    DeletionResult,
    EntryType,
    PlanEntry,
    TransferMode,
    TreeNode,
    VerificationResult,
)
from adbk.paths import logical_relative_path, logical_to_local

_CHUNK = 1 << 20


class CancellationToken:
    """A simple, thread-free flag checked between and within file operations."""

    def __init__(self) -> None:
        self._requested = False

    def request(self) -> None:
        self._requested = True

    @property
    def requested(self) -> bool:
        return self._requested


def hash_local(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def transfer_file(
    device: DeviceInterface,
    entry: PlanEntry,
    backup_root: Path,
    mode: TransferMode,
    *,
    cancel: CancellationToken | None = None,
) -> ManifestEntry:
    """Copy (and, in safe-move mode, verify + delete) a single file."""

    record = ManifestEntry(
        android_path=entry.android_path,
        local_relative_path=entry.local_relative_path,
        entry_type=EntryType.FILE,
        category=entry.category,
        size=entry.size,
        mtime=entry.mtime,
    )
    destination = logical_to_local(backup_root, entry.local_relative_path)

    # 1. Copy.
    try:
        device.pull(entry.android_path, destination)
    except TransferError as exc:
        record.copy_result = CopyResult.FAILED
        record.error = str(exc)
        return record

    # 2. Confirm the destination exists.
    if not destination.exists():
        record.copy_result = CopyResult.FAILED
        record.error = "destination missing after pull"
        return record
    record.copy_result = CopyResult.COPIED

    # 3 + 4. Compare sizes and hash both sides.
    local_size = destination.stat().st_size
    record.size = local_size
    local_hash = hash_local(destination)
    record.sha256 = local_hash

    source_stat = device.stat(entry.android_path)
    source_size = source_stat.size if source_stat else None
    device_hash = device.sha256(entry.android_path)

    if device_hash is None:
        record.verification_result = VerificationResult.UNVERIFIABLE
    elif source_size is not None and source_size != local_size:
        record.verification_result = VerificationResult.MISMATCH
    elif device_hash == local_hash:
        record.verification_result = VerificationResult.VERIFIED
    else:
        record.verification_result = VerificationResult.MISMATCH

    # 5 + 6. Delete the source only on a verified match, and only in safe move.
    if mode is not TransferMode.SAFE_MOVE:
        record.deletion_result = DeletionResult.NOT_ATTEMPTED
        return record

    if record.verification_result is not VerificationResult.VERIFIED:
        record.deletion_result = DeletionResult.KEPT
        record.error = f"kept (not deleted): {record.verification_result}"
        return record

    if cancel is not None and cancel.requested:
        record.deletion_result = DeletionResult.KEPT
        record.error = "kept: cancellation requested before deletion"
        return record

    removed = device.delete_file(entry.android_path)
    if removed:
        record.deletion_result = DeletionResult.DELETED
        record.source_removed = True
    else:
        record.deletion_result = DeletionResult.FAILED
        record.error = "source deletion did not remove the file"
    return record


def is_excluded(path: str, exclusions: frozenset[str] | set[str]) -> bool:
    """True if ``path`` is one of, or sits under, an excluded path."""

    return any(path == ex or path.startswith(ex.rstrip("/") + "/") for ex in exclusions)


def entry_satisfied(entry: ManifestEntry, backup_root: Path) -> bool:
    """Resume check: is this manifest entry already done and still valid locally?"""

    if entry.copy_result is not CopyResult.COPIED:
        return False
    local = logical_to_local(backup_root, entry.local_relative_path)
    try:
        local_size = local.stat().st_size
    except OSError:
        return False  # missing or unreadable -> re-copy
    # A size mismatch (e.g. the file the interruption left half-written) is a
    # definite mismatch: reject it without paying for a full re-hash first.
    if entry.size is not None and local_size != entry.size:
        return False
    if entry.sha256:
        return hash_local(local) == entry.sha256
    return True


@dataclass
class RootResult:
    """Tally for one backed-up category root."""

    total: int = 0
    copied: int = 0
    verified: int = 0
    deleted: int = 0
    kept: int = 0
    failed: int = 0
    skipped_resume: int = 0
    cancelled: bool = False


def _tally(result: RootResult, record: ManifestEntry) -> None:
    if record.copy_result is CopyResult.COPIED:
        result.copied += 1
    if record.copy_result is CopyResult.FAILED:
        result.failed += 1
    if record.verification_result is VerificationResult.VERIFIED:
        result.verified += 1
    if record.deletion_result is DeletionResult.DELETED:
        result.deleted += 1
    elif record.deletion_result is DeletionResult.KEPT:
        result.kept += 1


def transfer_root(
    device: DeviceInterface,
    root: str,
    category: str,
    serial: str,
    backup_root: Path,
    mode: TransferMode,
    manifest: Manifest,
    manifest_path: Path,
    *,
    exclusions: frozenset[str] | set[str] = frozenset(),
    cancel: CancellationToken | None = None,
    resume: bool = False,
    on_file: Callable[[int, int], None] | None = None,
) -> RootResult:
    """Enumerate one root (single recursive listing) and transfer its files.

    Enumeration and transfer are interleaved, so a huge folder shows progress
    instead of stalling. In safe-move mode, emptied source directories are pruned
    afterwards (``rmdir`` only removes empty ones).
    """

    listing = device.list_recursive(root)
    files = [
        entry for entry in listing.entries
        if entry.entry_type is EntryType.FILE
        and not is_excluded(entry.path, exclusions)
        and not filters.path_has_junk_dir(entry.path)
        and not filters.is_junk_file_name(entry.path.rsplit("/", 1)[-1])
    ]
    result = RootResult(total=len(files))

    for index, flat in enumerate(files, 1):
        if cancel is not None and cancel.requested:
            result.cancelled = True
            break
        plan_entry = PlanEntry(
            android_path=flat.path,
            local_relative_path=logical_relative_path(serial, flat.path),
            entry_type=EntryType.FILE,
            category=category,
            size=flat.size,
        )
        if resume:
            existing = manifest.get(plan_entry.local_relative_path)
            if existing is not None and entry_satisfied(existing, backup_root):
                result.skipped_resume += 1
                if on_file is not None:
                    on_file(index, result.total)
                continue

        record = transfer_file(device, plan_entry, backup_root, mode, cancel=cancel)
        manifest.upsert(record)
        manifest.save(manifest_path)
        _tally(result, record)
        if on_file is not None:
            on_file(index, result.total)

    if mode is TransferMode.SAFE_MOVE and not result.cancelled:
        dirs = sorted(
            (e.path for e in listing.entries
             if e.entry_type is EntryType.DIRECTORY and not is_excluded(e.path, exclusions)),
            key=len,
            reverse=True,
        )
        for directory in dirs:
            device.delete_dir(directory)
        device.delete_dir(root)

    return result


def prune_source_dirs(device: DeviceInterface, node: TreeNode) -> None:
    """Remove now-empty source directories bottom-up (safe move only).

    ``delete_dir`` uses ``rmdir``, which only removes an empty directory, so a
    folder still holding skipped, kept, failed or inaccessible entries is left
    untouched automatically.
    """

    if node.entry_type is not EntryType.DIRECTORY:
        return
    for child in node.direct_dirs:
        prune_source_dirs(device, child)
    device.delete_dir(node.android_path)
