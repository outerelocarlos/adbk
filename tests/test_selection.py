"""Tests for size-estimated category selection and the ls -lA parser."""

from __future__ import annotations

from adbk import discovery, selection, workflow
from adbk.device import parse_ls_errors, parse_ls_long, parse_ls_recursive
from adbk.models import Category, EntryType
from tests.fakedevice import FakeDevice


def test_estimate_sizes_per_category_and_total() -> None:
    device = FakeDevice()
    device.add_file("/sdcard/Docs/a.txt", b"x" * 100)
    device.add_file("/sdcard/Docs/b.txt", b"x" * 50)
    device.add_file("/sdcard/DCIM/photo.jpg", b"y" * 1000)
    categories = [
        Category("Docs", ("/sdcard/Docs",)),
        Category("Camera", ("/sdcard/DCIM",), default_selected=False),
    ]
    discovered = discovery.discover(device, categories)
    estimates = selection.estimate_categories(device, discovered, categories)

    by_name = {e.name: e for e in estimates}
    assert by_name["Docs"].size_bytes == 150
    assert by_name["Docs"].included is True
    assert by_name["Camera"].size_bytes == 1000
    assert by_name["Camera"].included is False  # respects default_selected

    # Only selected categories count toward the total.
    assert selection.selected_total_bytes(estimates) == 150
    assert selection.selected_paths(estimates) == {"/storage/emulated/0/Docs"}


def test_toggling_updates_total() -> None:
    device = FakeDevice()
    device.add_file("/sdcard/DCIM/photo.jpg", b"y" * 1000)
    categories = [Category("Camera", ("/sdcard/DCIM",), default_selected=False)]
    estimates = selection.estimate_categories(
        device, discovery.discover(device, categories), categories
    )
    assert selection.selected_total_bytes(estimates) == 0
    estimates[0].included = True
    assert selection.selected_total_bytes(estimates) == 1000


def test_exclusions_reduce_effective_size_and_total() -> None:
    device = FakeDevice()
    device.add_file("/sdcard/W/Media/a.bin", b"x" * 1000)
    device.add_file("/sdcard/W/Databases/b.bin", b"y" * 500)
    categories = [Category("W", ("/sdcard/W",))]
    estimate = selection.estimate_categories(
        device, discovery.discover(device, categories), categories
    )[0]
    assert estimate.size_bytes == 1500

    estimate.exclusions["/storage/emulated/0/W/Media"] = 1000
    assert estimate.excluded_bytes() == 1000
    assert estimate.effective_size() == 500
    assert selection.selected_total_bytes([estimate]) == 500
    assert selection.all_exclusions([estimate]) == {"/storage/emulated/0/W/Media"}


def test_selected_total_dedupes_nested_categories() -> None:
    device = FakeDevice()
    device.add_file("/sdcard/Android/data/app/f.bin", b"z" * 200)
    categories = [
        Category("All", ("/sdcard/Android/data",)),
        Category("App", ("/sdcard/Android/data/app",)),
    ]
    estimates = selection.estimate_categories(device, discovery.discover(device, categories), categories)
    # Both selected: the nested "App" path must not be double-counted.
    assert selection.selected_total_bytes(estimates) == 200


def test_estimate_skips_categories_without_readable_paths() -> None:
    device = FakeDevice()  # nothing exists
    categories = [Category("Docs", ("/sdcard/Docs",))]
    estimates = selection.estimate_categories(
        device, discovery.discover(device, categories), categories
    )
    assert estimates == []


def test_parse_ls_long_handles_dirs_files_spaces_symlinks() -> None:
    output = (
        "total 40\n"
        "drwxrwx--- 2 u0_a1 media_rw    3452 2025-06-11 21:12 Camera\n"
        "-rw-rw---- 1 u0_a1 media_rw 1234567 2025-06-11 21:12 IMG_0001.jpg\n"
        "-rw-rw---- 1 u0_a1 media_rw     123 2025-06-11 21:12 file with spaces.txt\n"
        "lrwxrwxrwx 1 root  root          21 2025-06-11 21:12 link -> /somewhere\n"
    )
    entries = parse_ls_long(output)
    names = {e.name: e for e in entries}
    assert names["Camera"].entry_type is EntryType.DIRECTORY
    assert names["IMG_0001.jpg"].entry_type is EntryType.FILE
    assert names["IMG_0001.jpg"].size == 1234567
    assert names["file with spaces.txt"].size == 123
    assert "link" not in names  # symlinks are skipped


def test_parse_ls_long_ignores_total_and_blank_lines() -> None:
    assert parse_ls_long("total 0\n\n") == []


def test_parse_ls_recursive_flattens_tree() -> None:
    output = (
        "/sdcard/D:\n"
        "total 8\n"
        "-rw-rw---- 1 u0 g       5 2025-01-01 12:00 a.txt\n"
        "drwxrwx--- 2 u0 g    3452 2025-01-01 12:00 sub\n"
        "\n"
        "/sdcard/D/sub:\n"
        "-rw-rw---- 1 u0 g       3 2025-01-01 12:00 b.txt\n"
    )
    entries = {e.path: e for e in parse_ls_recursive(output, "/sdcard/D")}
    assert entries["/sdcard/D/a.txt"].entry_type is EntryType.FILE
    assert entries["/sdcard/D/a.txt"].size == 5
    assert entries["/sdcard/D/sub"].entry_type is EntryType.DIRECTORY
    assert entries["/sdcard/D/sub/b.txt"].size == 3


def test_parse_ls_errors_extracts_inaccessible_paths() -> None:
    stderr = "ls: /sdcard/secret: Permission denied\nls: /sdcard/x: Permission denied\n"
    assert parse_ls_errors(stderr) == {"/sdcard/secret", "/sdcard/x"}


def test_selection_rows_show_markers_and_total() -> None:
    device = FakeDevice()
    device.add_file("/sdcard/Docs/a.txt", b"x" * 100)
    device.add_file("/sdcard/DCIM/p.jpg", b"y" * 1000)
    categories = [
        Category("Docs", ("/sdcard/Docs",)),
        Category("Camera", ("/sdcard/DCIM",), default_selected=False),
    ]
    estimates = selection.estimate_categories(
        device, discovery.discover(device, categories), categories
    )
    text = "\n".join(row.plain for row in workflow.build_selection_rows(estimates))
    assert "[x]  Docs" in text  # included marker no longer swallowed
    assert "[ ]  Camera" in text  # excluded marker
    assert "Total selected" in text

