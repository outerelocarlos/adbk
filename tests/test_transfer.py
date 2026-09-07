"""Tests for verified copy, verified safe move and directory pruning."""

from __future__ import annotations

from pathlib import Path

from adbk import paths, transfer
from adbk.manifest import Manifest, ManifestEntry
from adbk.models import (
    CopyResult,
    DeletionResult,
    DeviceIdentity,
    EntryType,
    Operation,
    PlanEntry,
    TransferMode,
    TreeNode,
    VerificationResult,
)
from tests.fakedevice import FakeDevice


def _manifest(mode: TransferMode) -> Manifest:
    return Manifest(Operation.BACKUP, mode, DeviceIdentity("S"))


def _entry(android: str, logical: str, size: int) -> PlanEntry:
    return PlanEntry(android, logical, EntryType.FILE, "cat", size=size)


def test_copy_leaves_source(tmp_path: Path) -> None:
    device = FakeDevice()
    device.add_file("/sdcard/a.txt", b"hello")
    entry = _entry("/storage/emulated/0/a.txt", "devices/F/shared-storage/a.txt", 5)
    record = transfer.transfer_file(device, entry, tmp_path, TransferMode.COPY)
    assert record.copy_result is CopyResult.COPIED
    assert record.verification_result is VerificationResult.VERIFIED
    assert record.deletion_result is DeletionResult.NOT_ATTEMPTED
    assert device.exists("/sdcard/a.txt")
    assert (tmp_path / "devices" / "F" / "shared-storage" / "a.txt").read_bytes() == b"hello"


def test_safe_move_deletes_verified_source(tmp_path: Path) -> None:
    device = FakeDevice()
    device.add_file("/sdcard/a.txt", b"hello")
    entry = _entry("/storage/emulated/0/a.txt", "devices/F/shared-storage/a.txt", 5)
    record = transfer.transfer_file(device, entry, tmp_path, TransferMode.SAFE_MOVE)
    assert record.verification_result is VerificationResult.VERIFIED
    assert record.deletion_result is DeletionResult.DELETED
    assert record.source_removed
    assert not device.exists("/sdcard/a.txt")


def test_safe_move_keeps_when_source_unhashable(tmp_path: Path) -> None:
    device = FakeDevice(sha_available=False)
    device.add_file("/sdcard/a.txt", b"hello")
    entry = _entry("/storage/emulated/0/a.txt", "devices/F/shared-storage/a.txt", 5)
    record = transfer.transfer_file(device, entry, tmp_path, TransferMode.SAFE_MOVE)
    assert record.verification_result is VerificationResult.UNVERIFIABLE
    assert record.deletion_result is DeletionResult.KEPT
    assert device.exists("/sdcard/a.txt")  # never deleted without verification


def test_safe_move_keeps_on_cancellation(tmp_path: Path) -> None:
    device = FakeDevice()
    device.add_file("/sdcard/a.txt", b"hello")
    entry = _entry("/storage/emulated/0/a.txt", "devices/F/shared-storage/a.txt", 5)
    token = transfer.CancellationToken()
    token.request()
    record = transfer.transfer_file(device, entry, tmp_path, TransferMode.SAFE_MOVE, cancel=token)
    assert record.deletion_result is DeletionResult.KEPT
    assert device.exists("/sdcard/a.txt")


def test_pull_failure_recorded(tmp_path: Path) -> None:
    device = FakeDevice()  # file does not exist
    entry = _entry("/storage/emulated/0/missing.txt", "devices/F/shared-storage/missing.txt", 1)
    record = transfer.transfer_file(device, entry, tmp_path, TransferMode.COPY)
    assert record.copy_result is CopyResult.FAILED


def test_prune_removes_empty_source_dirs() -> None:
    device = FakeDevice()
    device.add_dir("/sdcard/DCIM/Camera")
    camera = TreeNode("Camera", "/storage/emulated/0/DCIM/Camera", EntryType.DIRECTORY)
    dcim = TreeNode("DCIM", "/storage/emulated/0/DCIM", EntryType.DIRECTORY, children=[camera])
    transfer.prune_source_dirs(device, dcim)
    assert not device.exists("/storage/emulated/0/DCIM/Camera")
    assert not device.exists("/storage/emulated/0/DCIM")


def test_prune_keeps_nonempty_source_dir() -> None:
    device = FakeDevice()
    device.add_file("/sdcard/DCIM/keep.txt", b"x")
    dcim = TreeNode("DCIM", "/storage/emulated/0/DCIM", EntryType.DIRECTORY)
    transfer.prune_source_dirs(device, dcim)
    assert device.exists("/storage/emulated/0/DCIM")


def _copied_entry(backup_root: Path, logical: str, data: bytes) -> ManifestEntry:
    """Write ``data`` at ``logical`` under ``backup_root`` and record it as copied."""

    local = paths.logical_to_local(backup_root, logical)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    return ManifestEntry(
        android_path="/storage/emulated/0/x",
        local_relative_path=logical,
        category="cat",
        size=len(data),
        sha256=transfer.hash_local(local),
        copy_result=CopyResult.COPIED,
    )


def test_entry_satisfied_true_when_size_and_hash_match(tmp_path: Path) -> None:
    entry = _copied_entry(tmp_path, "devices/S/shared-storage/a.txt", b"hello")
    assert transfer.entry_satisfied(entry, tmp_path)


def test_entry_satisfied_false_when_local_missing(tmp_path: Path) -> None:
    entry = _copied_entry(tmp_path, "devices/S/shared-storage/a.txt", b"hello")
    paths.logical_to_local(tmp_path, entry.local_relative_path).unlink()
    assert not transfer.entry_satisfied(entry, tmp_path)


def test_entry_satisfied_false_on_size_mismatch(tmp_path: Path) -> None:
    # A half-written file (wrong size) is rejected on the cheap size check alone.
    entry = _copied_entry(tmp_path, "devices/S/shared-storage/a.txt", b"hello")
    paths.logical_to_local(tmp_path, entry.local_relative_path).write_bytes(b"hel")
    assert not transfer.entry_satisfied(entry, tmp_path)


def test_entry_satisfied_false_on_hash_mismatch(tmp_path: Path) -> None:
    # Same size but different bytes: the full re-hash still catches it.
    entry = _copied_entry(tmp_path, "devices/S/shared-storage/a.txt", b"hello")
    paths.logical_to_local(tmp_path, entry.local_relative_path).write_bytes(b"world")
    assert not transfer.entry_satisfied(entry, tmp_path)


def test_is_excluded() -> None:
    assert transfer.is_excluded("/a/b/c.txt", {"/a/b"})
    assert transfer.is_excluded("/a/b", {"/a/b"})
    assert not transfer.is_excluded("/a/bc.txt", {"/a/b"})


def test_transfer_root_copies_all(tmp_path: Path) -> None:
    device = FakeDevice(serial="S")
    device.add_file("/sdcard/Docs/a.txt", b"aa")
    device.add_file("/sdcard/Docs/sub/b.txt", b"bbb")
    manifest = _manifest(TransferMode.COPY)
    result = transfer.transfer_root(
        device, "/storage/emulated/0/Docs", "Docs", "S", tmp_path,
        TransferMode.COPY, manifest, tmp_path / "manifest.json",
    )
    assert result.total == 2
    assert result.copied == 2
    assert (tmp_path / "devices" / "S" / "shared-storage" / "Docs" / "a.txt").read_bytes() == b"aa"
    assert device.exists("/sdcard/Docs/a.txt")  # copy leaves sources


def test_transfer_root_respects_exclusions(tmp_path: Path) -> None:
    device = FakeDevice(serial="S")
    device.add_file("/sdcard/Docs/a.txt", b"aa")
    device.add_file("/sdcard/Docs/Stickers/s.txt", b"z")
    manifest = _manifest(TransferMode.COPY)
    result = transfer.transfer_root(
        device, "/storage/emulated/0/Docs", "Docs", "S", tmp_path,
        TransferMode.COPY, manifest, tmp_path / "manifest.json",
        exclusions={"/storage/emulated/0/Docs/Stickers"},
    )
    assert result.total == 1  # excluded file not transferred
    assert not (tmp_path / "devices" / "S" / "shared-storage" / "Docs" / "Stickers").exists()


def test_transfer_root_safe_move_deletes_and_prunes(tmp_path: Path) -> None:
    device = FakeDevice(serial="S")
    device.add_file("/sdcard/Docs/a.txt", b"hello")
    manifest = _manifest(TransferMode.SAFE_MOVE)
    result = transfer.transfer_root(
        device, "/storage/emulated/0/Docs", "Docs", "S", tmp_path,
        TransferMode.SAFE_MOVE, manifest, tmp_path / "manifest.json",
    )
    assert result.deleted == 1
    assert not device.exists("/sdcard/Docs/a.txt")
    assert not device.exists("/sdcard/Docs")  # emptied dir pruned
