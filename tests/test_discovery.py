"""Tests for path classification and tree building."""

from __future__ import annotations

from adbk import discovery
from adbk.models import AccessState, Category
from tests.fakedevice import FakeDevice


def test_classify_missing() -> None:
    device = FakeDevice()
    result = discovery.classify_path(device, "c", "/sdcard/none")
    assert result.access_state is AccessState.MISSING


def test_classify_readable_dir() -> None:
    device = FakeDevice()
    device.add_file("/sdcard/Docs/a.txt", b"x")
    result = discovery.classify_path(device, "c", "/sdcard/Docs")
    assert result.access_state is AccessState.READABLE


def test_classify_empty_dir() -> None:
    device = FakeDevice()
    device.add_dir("/sdcard/Empty")
    result = discovery.classify_path(device, "c", "/sdcard/Empty")
    assert result.access_state is AccessState.EMPTY


def test_classify_inaccessible_dir() -> None:
    device = FakeDevice()
    device.add_dir("/sdcard/Secret", readable=False)
    result = discovery.classify_path(device, "c", "/sdcard/Secret")
    assert result.access_state is AccessState.INACCESSIBLE


def test_failed_listing_is_not_empty() -> None:
    device = FakeDevice()
    device.add_dir("/sdcard/Weird")  # readable flag stays True...
    device.list_errors.add("/storage/emulated/0/Weird")  # ...but listing fails
    result = discovery.classify_path(device, "c", "/sdcard/Weird")
    assert result.access_state is AccessState.INACCESSIBLE  # never EMPTY


def test_classify_root_only() -> None:
    device = FakeDevice()
    result = discovery.classify_path(device, "c", "/data/data/com.example")
    assert result.access_state is AccessState.ROOT_ONLY


def test_discover_dedupes_aliases() -> None:
    device = FakeDevice()
    device.add_file("/sdcard/Docs/a.txt", b"x")
    categories = [
        Category("A", ("/sdcard/Docs",)),
        Category("B", ("/storage/emulated/0/Docs",)),
    ]
    results = discovery.discover(device, categories)
    matching = [r for r in results if r.android_path == "/storage/emulated/0/Docs"]
    assert len(matching) == 1


def test_build_tree_marks_inaccessible_child() -> None:
    device = FakeDevice()
    device.add_file("/sdcard/Root/ok.txt", b"x")
    device.add_dir("/sdcard/Root/blocked")
    device.list_errors.add("/storage/emulated/0/Root/blocked")
    node = discovery.build_tree(device, "/sdcard/Root")
    blocked = next(c for c in node.children if c.name == "blocked")
    assert blocked.access_state is AccessState.INACCESSIBLE
