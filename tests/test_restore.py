"""Tests for restore conflict resolution and pushing files back."""

from __future__ import annotations

import hashlib
from pathlib import Path

from adbk import restore
from adbk.manifest import Manifest, ManifestEntry
from adbk.models import (
    ConflictPolicy,
    CopyResult,
    DeviceIdentity,
    EntryType,
    Operation,
    TransferMode,
)
from tests.fakedevice import FakeDevice


def test_decide_action_default_skips_identical() -> None:
    assert restore.decide_action(None, True, interactive=False, prompt=None, remote="/x") == "skip"


def test_decide_action_default_different_non_interactive_skips() -> None:
    # A different file is never silently overwritten.
    assert restore.decide_action(None, False, interactive=False, prompt=None, remote="/x") == "skip"


def test_decide_action_always_overwrite() -> None:
    action = restore.decide_action(
        ConflictPolicy.ALWAYS_OVERWRITE, True, interactive=False, prompt=None, remote="/x"
    )
    assert action == "write"


def test_decide_action_rename() -> None:
    action = restore.decide_action(
        ConflictPolicy.RENAME, False, interactive=False, prompt=None, remote="/x"
    )
    assert action == "rename"


def test_decide_action_abort() -> None:
    action = restore.decide_action(
        ConflictPolicy.ABORT, False, interactive=False, prompt=None, remote="/x"
    )
    assert action == "abort"


def _manifest_with_entry(content: bytes, tmp_path: Path) -> tuple[Manifest, ManifestEntry]:
    local = tmp_path / "devices" / "S" / "shared-storage" / "a.txt"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(content)
    entry = ManifestEntry(
        "/storage/emulated/0/a.txt",
        "devices/S/shared-storage/a.txt",
        EntryType.FILE,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        copy_result=CopyResult.COPIED,
    )
    manifest = Manifest(Operation.RESTORE, TransferMode.COPY, DeviceIdentity("S"))
    manifest.upsert(entry)
    return manifest, entry


def test_restore_pushes_missing_file(tmp_path: Path) -> None:
    manifest, _ = _manifest_with_entry(b"backup-content", tmp_path)
    device = FakeDevice()  # remote empty
    summary = restore.restore(device, manifest, tmp_path)
    assert "/storage/emulated/0/a.txt" in summary.restored
    assert device.exists("/storage/emulated/0/a.txt")


def test_restore_skips_identical(tmp_path: Path) -> None:
    manifest, _ = _manifest_with_entry(b"same", tmp_path)
    device = FakeDevice()
    device.add_file("/sdcard/a.txt", b"same")
    summary = restore.restore(device, manifest, tmp_path)
    assert any(path == "/storage/emulated/0/a.txt" for path, _ in summary.skipped)
    assert not summary.restored


def test_restore_different_default_does_not_overwrite(tmp_path: Path) -> None:
    manifest, _ = _manifest_with_entry(b"new-version", tmp_path)
    device = FakeDevice()
    device.add_file("/sdcard/a.txt", b"old-version")
    summary = restore.restore(device, manifest, tmp_path)  # default policy, non-interactive
    assert device.files["/storage/emulated/0/a.txt"] == b"old-version"  # untouched
    assert not summary.restored


def test_restore_always_overwrite(tmp_path: Path) -> None:
    manifest, _ = _manifest_with_entry(b"new-version", tmp_path)
    device = FakeDevice()
    device.add_file("/sdcard/a.txt", b"old-version")
    summary = restore.restore(device, manifest, tmp_path, policy=ConflictPolicy.ALWAYS_OVERWRITE)
    assert device.files["/storage/emulated/0/a.txt"] == b"new-version"
    assert summary.restored


def test_restore_reports_missing_backup_file(tmp_path: Path) -> None:
    manifest = Manifest(Operation.RESTORE, TransferMode.COPY, DeviceIdentity("S"))
    manifest.upsert(
        ManifestEntry("/storage/emulated/0/gone.txt", "devices/S/shared-storage/gone.txt",
                      EntryType.FILE, copy_result=CopyResult.COPIED)
    )
    device = FakeDevice()
    summary = restore.restore(device, manifest, tmp_path)
    assert any("missing" in reason for _, reason in summary.skipped)


# --- Group naming -------------------------------------------------------------


def test_group_name_prefers_recorded_category() -> None:
    entry = ManifestEntry("/storage/emulated/0/DCIM/x.jpg", "l", EntryType.FILE, category="Camera")
    assert restore.group_name(entry) == "Camera"


def test_group_name_falls_back_to_top_folder() -> None:
    entry = ManifestEntry("/storage/emulated/0/DCIM/x.jpg", "l", EntryType.FILE)
    assert restore.group_name(entry) == "DCIM"


def test_group_name_groups_per_app_android_data() -> None:
    entry = ManifestEntry(
        "/storage/emulated/0/Android/media/com.whatsapp/WhatsApp/a.bin", "l", EntryType.FILE
    )
    assert restore.group_name(entry) == "Android/media/com.whatsapp"


# --- Subset restore + progress ------------------------------------------------


def _two_entry_manifest(tmp_path: Path) -> Manifest:
    manifest = Manifest(Operation.RESTORE, TransferMode.COPY, DeviceIdentity("S"))
    for name, content in (("a.txt", b"aaa"), ("b.txt", b"bbbb")):
        local = tmp_path / "devices" / "S" / "shared-storage" / name
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content)
        manifest.upsert(
            ManifestEntry(
                f"/storage/emulated/0/{name}", f"devices/S/shared-storage/{name}",
                EntryType.FILE, size=len(content),
                sha256=hashlib.sha256(content).hexdigest(), copy_result=CopyResult.COPIED,
            )
        )
    return manifest


def test_restore_only_restricts_to_selected_entries(tmp_path: Path) -> None:
    manifest = _two_entry_manifest(tmp_path)
    device = FakeDevice()
    summary = restore.restore(
        device, manifest, tmp_path, only={"devices/S/shared-storage/a.txt"}
    )
    assert device.exists("/storage/emulated/0/a.txt")
    assert not device.exists("/storage/emulated/0/b.txt")  # excluded from this restore
    assert summary.restored == ["/storage/emulated/0/a.txt"]


def test_restore_reports_progress_per_entry(tmp_path: Path) -> None:
    manifest = _two_entry_manifest(tmp_path)
    device = FakeDevice()
    seen: list[tuple[int, int]] = []
    restore.restore(device, manifest, tmp_path, on_file=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2)]
