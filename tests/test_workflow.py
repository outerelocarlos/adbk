"""End-to-end backup/restore workflow tests using the in-memory fake device."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from rich.console import Console

from adbk import ui, workflow
from adbk.manifest import MANIFEST_FILENAME, ManifestEntry, load_manifest
from adbk.models import Category, EntryType, ManifestState, TransferMode
from tests.fakedevice import FakeDevice


def _console() -> Console:
    return ui.make_console(plain=True)


def _docs_device() -> FakeDevice:
    device = FakeDevice(serial="S")
    device.add_file("/sdcard/Docs/a.txt", b"hello")
    device.add_file("/sdcard/Docs/sub/b.txt", b"world")
    return device


_CATEGORIES = [Category("Docs", ("/sdcard/Docs",))]


def test_backup_copy_end_to_end(tmp_path: Path) -> None:
    device = _docs_device()
    outcome = workflow.run_backup(
        device, backup_root=tmp_path, categories=_CATEGORIES,
        mode=TransferMode.COPY, console=_console(), assume_yes=True,
    )
    assert outcome.state is ManifestState.COMPLETED
    assert outcome.copied == 2
    assert (tmp_path / "devices" / "S" / "shared-storage" / "Docs" / "a.txt").read_bytes() == b"hello"
    assert device.exists("/sdcard/Docs/a.txt")  # copy leaves source

    manifest = load_manifest(tmp_path / MANIFEST_FILENAME)
    assert manifest.state is ManifestState.COMPLETED
    assert len(manifest.entries) == 2


def test_backup_safe_move_deletes_and_prunes(tmp_path: Path) -> None:
    device = FakeDevice(serial="S")
    device.add_file("/sdcard/Docs/a.txt", b"hello")
    outcome = workflow.run_backup(
        device, backup_root=tmp_path, categories=_CATEGORIES,
        mode=TransferMode.SAFE_MOVE, console=_console(), assume_yes=True,
    )
    assert outcome.deleted == 1
    assert not device.exists("/sdcard/Docs/a.txt")
    assert not device.exists("/sdcard/Docs")  # emptied directory pruned


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    device = _docs_device()
    workflow.run_backup(
        device, backup_root=tmp_path, categories=_CATEGORIES,
        mode=TransferMode.SAFE_MOVE, console=_console(), dry_run=True, assume_yes=True,
    )
    assert not (tmp_path / "devices").exists()
    assert not (tmp_path / MANIFEST_FILENAME).exists()
    assert device.exists("/sdcard/Docs/a.txt")


def test_resume_reverifies_and_retries_missing(tmp_path: Path) -> None:
    device = _docs_device()
    workflow.run_backup(
        device, backup_root=tmp_path, categories=_CATEGORIES,
        mode=TransferMode.COPY, console=_console(), assume_yes=True,
    )
    # Simulate a lost local file so resume must re-copy exactly one entry.
    (tmp_path / "devices" / "S" / "shared-storage" / "Docs" / "sub" / "b.txt").unlink()

    outcome = workflow.run_backup(
        device, backup_root=tmp_path, categories=_CATEGORIES,
        mode=TransferMode.COPY, console=_console(), assume_yes=True, resume=True,
    )
    assert outcome.copied == 1  # only the missing file was re-copied


def test_child_dirs_hides_empty_and_junk() -> None:
    device = FakeDevice()
    device.add_file("/sdcard/W/Media/a.bin", b"x" * 100_000)
    device.add_dir("/sdcard/W/.Thumbs")  # junk
    device.add_dir("/sdcard/W/empty")  # empty
    device.add_file("/sdcard/W/note.txt", b"hi")
    items, files = workflow._child_dirs(
        device, "/storage/emulated/0/W", {}, _console()
    )
    names = [workflow._basename(path) for path, _ in items]
    assert names == ["Media"]  # .Thumbs (junk) and empty (tiny) are hidden
    assert [f.name for f in files] == ["note.txt"]  # loose file kept


def test_files_entry_toggles_loose_files() -> None:
    exclusions: dict[str, int] = {}
    entry = workflow._DrillEntry(
        "2 files", 300, "files", files=[("/X/a.txt", 100), ("/X/b.txt", 200)]
    )
    assert not workflow._entry_excluded(entry, exclusions)
    workflow._toggle_entry(entry, exclusions)  # exclude the loose files
    assert exclusions == {"/X/a.txt": 100, "/X/b.txt": 200}
    assert workflow._entry_excluded(entry, exclusions)
    workflow._toggle_entry(entry, exclusions)  # include them again
    assert exclusions == {}


def test_dir_entry_toggles_folder() -> None:
    exclusions: dict[str, int] = {}
    entry = workflow._DrillEntry("Media", 1000, "dir", dir_path="/X/Media")
    workflow._toggle_entry(entry, exclusions)
    assert exclusions == {"/X/Media": 1000}
    assert workflow._entry_excluded(entry, exclusions)
    workflow._toggle_entry(entry, exclusions)
    assert exclusions == {}


def test_safe_move_not_confirmed_makes_no_changes(tmp_path: Path) -> None:
    device = FakeDevice(serial="S")
    device.add_file("/sdcard/Docs/a.txt", b"hello")
    outcome = workflow.run_backup(
        device, backup_root=tmp_path, categories=_CATEGORIES,
        mode=TransferMode.SAFE_MOVE, console=_console(),
        interactive=False, assume_yes=False,  # no confirmation possible
    )
    assert outcome.state is ManifestState.CANCELLED
    assert device.exists("/sdcard/Docs/a.txt")
    assert not (tmp_path / MANIFEST_FILENAME).exists()


# --- Restore workflow ---------------------------------------------------------

_RESTORE_CATEGORIES = [
    Category("Docs", ("/sdcard/Docs",)),
    Category("Pics", ("/sdcard/Pics",)),
]


def _make_backup(tmp_path: Path) -> None:
    """Produce a real manifest + local files by running a copy backup."""

    device = FakeDevice(serial="OLD")
    device.add_file("/sdcard/Docs/a.txt", b"hello")
    device.add_file("/sdcard/Pics/p.jpg", b"jpeg-bytes")
    workflow.run_backup(
        device, backup_root=tmp_path, categories=_RESTORE_CATEGORIES,
        mode=TransferMode.COPY, console=_console(), assume_yes=True,
    )


def test_backup_records_category_on_entries(tmp_path: Path) -> None:
    _make_backup(tmp_path)
    manifest = load_manifest(tmp_path / MANIFEST_FILENAME)
    categories = {e.category for e in manifest.entries.values()}
    assert categories == {"Docs", "Pics"}


def test_build_restore_groups_by_category(tmp_path: Path) -> None:
    _make_backup(tmp_path)
    manifest = load_manifest(tmp_path / MANIFEST_FILENAME)
    groups = workflow.build_restore_groups(manifest)
    assert [g.name for g in groups] == ["Docs", "Pics"]  # sorted, case-insensitive
    assert sum(g.count() for g in groups) == 2


def test_restore_end_to_end_onto_new_device(tmp_path: Path) -> None:
    _make_backup(tmp_path)
    target = FakeDevice(serial="NEW")  # a different, empty phone
    code = workflow.run_restore(
        target, backup_root=tmp_path, manifest_path=None,
        policy=None, console=_console(), assume_yes=True,
    )
    assert code == 0
    assert target.files["/storage/emulated/0/Docs/a.txt"] == b"hello"
    assert target.files["/storage/emulated/0/Pics/p.jpg"] == b"jpeg-bytes"


def test_restore_dry_run_writes_nothing(tmp_path: Path) -> None:
    _make_backup(tmp_path)
    target = FakeDevice(serial="NEW")
    workflow.run_restore(
        target, backup_root=tmp_path, manifest_path=None,
        policy=None, console=_console(), dry_run=True, assume_yes=True,
    )
    assert not target.files


def test_restore_requires_confirmation(tmp_path: Path) -> None:
    _make_backup(tmp_path)
    target = FakeDevice(serial="NEW")
    code = workflow.run_restore(
        target, backup_root=tmp_path, manifest_path=None,
        policy=None, console=_console(), interactive=False, assume_yes=False,
    )
    assert code == 0
    assert not target.files  # nothing written without an explicit "yes"


def test_backup_records_apps_and_restore_installs_them(tmp_path: Path) -> None:
    device = FakeDevice(serial="OLD")
    device.add_file("/sdcard/Docs/a.txt", b"hello")
    device.add_package("com.store.app", installer="com.android.vending")
    device.add_package("com.side.app", installer="null")

    workflow.run_backup(
        device, backup_root=tmp_path, categories=_CATEGORIES,
        mode=TransferMode.COPY, console=_console(), assume_yes=True,
    )
    manifest = load_manifest(tmp_path / MANIFEST_FILENAME)
    recorded = {app.package: app.availability for app in manifest.apps}
    assert recorded == {"com.store.app": "store", "com.side.app": "backup"}

    # Restoring onto a fresh phone installs only the app we hold an APK for.
    target = FakeDevice(serial="NEW")
    workflow.run_restore(
        target, backup_root=tmp_path, manifest_path=None,
        policy=None, console=_console(), assume_yes=True,
    )
    assert len(target.installed_apks) == 1


# --- Dated backup directories -------------------------------------------------


def _write_manifest(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MANIFEST_FILENAME).write_text("{}", encoding="utf-8")


def test_next_backup_dir_uses_the_date(tmp_path: Path) -> None:
    assert workflow.next_backup_dir(tmp_path, today=date(2026, 7, 20)) == tmp_path / "2026-07-20"


def test_next_backup_dir_disambiguates_same_day(tmp_path: Path) -> None:
    _write_manifest(tmp_path / "2026-07-20")
    assert workflow.next_backup_dir(tmp_path, today=date(2026, 7, 20)) == tmp_path / "2026-07-20-2"
    _write_manifest(tmp_path / "2026-07-20-2")
    assert workflow.next_backup_dir(tmp_path, today=date(2026, 7, 20)) == tmp_path / "2026-07-20-3"


def test_find_latest_backup_picks_newest_dated_dir(tmp_path: Path) -> None:
    assert workflow.find_latest_backup(tmp_path) is None
    for day in ("2026-07-18", "2026-07-20", "2026-07-19"):
        _write_manifest(tmp_path / day)
    assert workflow.find_latest_backup(tmp_path) == tmp_path / "2026-07-20"


def test_find_latest_backup_accepts_a_direct_backup_dir(tmp_path: Path) -> None:
    _write_manifest(tmp_path)  # the root itself holds a manifest
    assert workflow.find_latest_backup(tmp_path) == tmp_path


def _group(name: str, *sizes: int) -> workflow.RestoreGroup:
    entries = [
        ManifestEntry(f"/storage/emulated/0/{name}/{i}", f"l/{name}/{i}", EntryType.FILE, size=size)
        for i, size in enumerate(sizes)
    ]
    return workflow.RestoreGroup(name, entries)


def test_select_restore_groups_toggles(monkeypatch: pytest.MonkeyPatch) -> None:
    groups = [_group("Docs", 100), _group("Pics", 200)]
    answers = iter(["2", ""])  # toggle group 2 off, then continue
    monkeypatch.setattr("builtins.input", lambda *_a: next(answers))
    workflow._select_restore_groups(_console(), groups)
    assert groups[0].included is True
    assert groups[1].included is False
    assert workflow._restore_selected_bytes(groups) == 100
