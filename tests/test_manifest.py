"""Tests for the versioned, atomically-written manifest."""

from __future__ import annotations

from pathlib import Path

from adbk.manifest import (
    MANIFEST_VERSION,
    Manifest,
    ManifestEntry,
    load_manifest,
)
from adbk.models import (
    CopyResult,
    DeletionResult,
    DeviceIdentity,
    ManifestState,
    Operation,
    TransferMode,
    VerificationResult,
)


def _manifest() -> Manifest:
    return Manifest(Operation.BACKUP, TransferMode.SAFE_MOVE, DeviceIdentity("S", "Model", "14", False))


def test_roundtrip_preserves_everything(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest.upsert(
        ManifestEntry(
            "/storage/emulated/0/a",
            "devices/S/shared-storage/a",
            size=3,
            sha256="abc123",
            copy_result=CopyResult.COPIED,
            verification_result=VerificationResult.VERIFIED,
            deletion_result=DeletionResult.DELETED,
            source_removed=True,
        )
    )
    manifest.mark_skipped("/data/data/x", "root only")
    manifest.selected_categories = ["Docs"]
    manifest.finalize(ManifestState.COMPLETED)

    path = tmp_path / "manifest.json"
    manifest.save(path)
    loaded = load_manifest(path)

    assert loaded.manifest_version == MANIFEST_VERSION
    assert loaded.state is ManifestState.COMPLETED
    assert loaded.device.serial == "S"
    assert loaded.selected_categories == ["Docs"]
    entry = loaded.get("devices/S/shared-storage/a")
    assert entry is not None
    assert entry.copy_result is CopyResult.COPIED
    assert entry.verification_result is VerificationResult.VERIFIED
    assert entry.source_removed is True
    assert loaded.skipped[0]["reason"] == "root only"


def test_save_is_utf8_and_atomic(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest.upsert(ManifestEntry("/storage/emulated/0/José.txt", "devices/S/shared-storage/José.txt"))
    path = tmp_path / "m.json"
    manifest.save(path)
    text = path.read_text(encoding="utf-8")
    assert "José" in text
    # No stray temp files were left behind by the atomic write.
    assert list(tmp_path.glob(".manifest-*")) == []


def test_new_manifest_starts_in_progress() -> None:
    assert _manifest().state is ManifestState.IN_PROGRESS
