"""Restore files from a manifest back onto a device, honouring conflict policy.

Conflict policies: skip identical, overwrite-if-different, always overwrite,
rename, ask, abort. The default is *skip identical, ask before overwriting a
different file* - a different file is never silently overwritten.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from adbk.device import DeviceInterface
from adbk.errors import OperationCancelled, TransferError
from adbk.manifest import Manifest, ManifestEntry
from adbk.models import ConflictPolicy, EntryType
from adbk.paths import logical_to_local
from adbk.transfer import CancellationToken, hash_local

# Returns one of "skip", "write", "rename" for an interactive conflict prompt.
ConflictPrompt = Callable[[str, bool], str]

# Shared-storage roots stripped when deriving a group label from a path.
_SHARED_PREFIXES = ("/storage/emulated/0/", "/sdcard/")
_ANDROID_SUBTREES = ("data", "media", "obb")


def group_name(entry: ManifestEntry) -> str:
    """A stable group label for an entry: its backup category, or a path fallback.

    New backups record the category on each entry. For older manifests (no
    category) we derive a sensible group from the Android path so the restore
    picker still has meaningful groups: the top-level shared-storage folder, or
    ``Android/<data|media|obb>/<package>`` for per-app data.
    """

    if entry.category:
        return entry.category
    rel = entry.android_path
    for prefix in _SHARED_PREFIXES:
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    parts = [segment for segment in rel.split("/") if segment]
    if not parts:
        return "Other"
    if len(parts) >= 3 and parts[0] == "Android" and parts[1] in _ANDROID_SUBTREES:
        return "/".join(parts[:3])
    return parts[0]


@dataclass
class RestoreSummary:
    restored: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    conflicts: list[tuple[str, str]] = field(default_factory=list)


def is_identical(device: DeviceInterface, remote: str, entry: ManifestEntry) -> bool:
    """True when the on-device file already matches the backed-up file."""

    if not device.exists(remote):
        return False
    remote_hash = device.sha256(remote)
    if remote_hash is not None and entry.sha256 is not None:
        return remote_hash == entry.sha256
    # Fall back to a size comparison when device-side hashing is unavailable.
    stat = device.stat(remote)
    return stat is not None and entry.size is not None and stat.size == entry.size


def decide_action(
    policy: ConflictPolicy | None,
    identical: bool,
    *,
    interactive: bool,
    prompt: ConflictPrompt | None,
    remote: str,
) -> str:
    """Return "skip", "write", "rename" or "abort" for a conflicting file.

    ``policy is None`` means the default: skip identical, ask about different.
    """

    if policy is None:
        if identical:
            return "skip"
        if interactive and prompt is not None:
            return prompt(remote, identical)
        return "skip"  # never silently overwrite a different file
    if policy in (ConflictPolicy.SKIP_IDENTICAL, ConflictPolicy.OVERWRITE_IF_DIFFERENT):
        return "skip" if identical else "write"
    if policy is ConflictPolicy.ALWAYS_OVERWRITE:
        return "write"
    if policy is ConflictPolicy.RENAME:
        return "skip" if identical else "rename"
    if policy is ConflictPolicy.ASK:
        if identical:
            return "skip"
        if interactive and prompt is not None:
            return prompt(remote, identical)
        return "skip"
    return "skip" if identical else "abort"  # ConflictPolicy.ABORT


def _renamed_remote(remote: str) -> str:
    pure = PurePosixPath(remote)
    stem = pure.stem
    suffix = pure.suffix
    return str(pure.with_name(f"{stem} (restored){suffix}"))


def restore(
    device: DeviceInterface,
    manifest: Manifest,
    backup_root: Path,
    *,
    policy: ConflictPolicy | None = None,
    interactive: bool = False,
    prompt: ConflictPrompt | None = None,
    cancel: CancellationToken | None = None,
    only: set[str] | None = None,
    on_file: Callable[[int, int], None] | None = None,
) -> RestoreSummary:
    """Push backed-up files onto the device according to the conflict policy.

    ``only`` restricts the restore to entries whose ``local_relative_path`` is in
    the set (``None`` restores every file entry). ``on_file(done, total)`` is
    called after each entry so callers can show progress.
    """

    summary = RestoreSummary()
    entries = [
        entry for entry in manifest.entries.values()
        if entry.entry_type is EntryType.FILE
        and (only is None or entry.local_relative_path in only)
    ]
    total = len(entries)

    for index, entry in enumerate(entries, 1):
        if cancel is not None and cancel.requested:
            break
        _restore_one(device, summary, entry, backup_root, policy, interactive, prompt)
        if on_file is not None:
            on_file(index, total)

    return summary


def _restore_one(
    device: DeviceInterface,
    summary: RestoreSummary,
    entry: ManifestEntry,
    backup_root: Path,
    policy: ConflictPolicy | None,
    interactive: bool,
    prompt: ConflictPrompt | None,
) -> None:
    """Restore a single manifest entry, recording the outcome on ``summary``."""

    local_src = logical_to_local(backup_root, entry.local_relative_path)
    if not local_src.exists():
        summary.skipped.append((entry.android_path, "backup file missing locally"))
        return

    remote = entry.android_path
    if device.exists(remote):
        identical = is_identical(device, remote, entry)
        action = decide_action(
            policy, identical, interactive=interactive, prompt=prompt, remote=remote
        )
        entry.conflict_decision = action
        if action == "skip":
            summary.skipped.append((remote, "identical" if identical else "conflict: skipped"))
            return
        if action == "abort":
            raise OperationCancelled(f"Restore aborted at conflict: {remote}")
        if action == "rename":
            remote = _renamed_remote(remote)
            summary.conflicts.append((entry.android_path, f"renamed to {remote}"))

    try:
        device.push(local_src, remote)
    except TransferError as exc:
        summary.failed.append((remote, str(exc)))
        return

    # Verify the pushed file where device-side hashing is available.
    device_hash = device.sha256(remote)
    local_hash = hash_local(local_src)
    if device_hash is not None and device_hash != local_hash:
        summary.failed.append((remote, "verification mismatch after push"))
        return
    summary.restored.append(remote)
