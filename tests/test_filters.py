"""Tests for junk-folder / junk-file filtering."""

from __future__ import annotations

import pytest

from adbk import filters


@pytest.mark.parametrize(
    "name",
    [".Thumbs", ".StickerThumbs", ".thumbnails", "thumbnails", "composeCache",
     ".wamocache", "WhatsApp AI Editor Cache", ".trash", "logs", "tmp", "temp", "lost.dir",
     ".recycle", ".RecycleBin", "$RECYCLE.BIN", ".gs_fs0"],
)
def test_junk_dir_names(name: str) -> None:
    assert filters.is_junk_dir_name(name)


@pytest.mark.parametrize("name", ["Documents", "Camera", "Vlogs", "Template", "Music", "Samsung", "Catalog"])
def test_non_junk_dir_names(name: str) -> None:
    assert not filters.is_junk_dir_name(name)


def test_junk_file_names() -> None:
    assert filters.is_junk_file_name(".nomedia")
    assert filters.is_junk_file_name("Thumbs.db")
    assert not filters.is_junk_file_name("photo.jpg")


@pytest.mark.parametrize(
    "name",
    [".escheck.tmp", "cache.tmp", "IMG.TMP",
     ".trashed-1700000000-photo.jpg", ".pending-1700000000-video.mp4"],
)
def test_junk_temp_and_trashed_files(name: str) -> None:
    assert filters.is_junk_file_name(name)


@pytest.mark.parametrize("name", ["report.pdf", "song.mp3", "template.docx", "notes.txt"])
def test_non_junk_files(name: str) -> None:
    assert not filters.is_junk_file_name(name)


def test_path_has_junk_dir() -> None:
    assert filters.path_has_junk_dir("/sdcard/DCIM/.thumbnails/x.jpg")
    assert filters.path_has_junk_dir("/sdcard/W/.trash/g.bin")
    assert not filters.path_has_junk_dir("/sdcard/DCIM/Camera/x.jpg")


def test_gs_fs0_is_builtin_junk() -> None:
    assert filters.is_junk_dir_name(".gs_fs0")


def test_user_configurable_patterns() -> None:
    filters.configure(ignore_dirs=["MyAppCache*", ".Statuses"], ignore_files=["*.bak", "*.LOG"])
    assert filters.is_junk_dir_name("MyAppCache_v2")
    assert filters.is_junk_dir_name(".statuses")  # case-insensitive
    assert filters.is_junk_file_name("archive.BAK")
    assert filters.is_junk_file_name("output.log")
    assert not filters.is_junk_file_name("photo.jpg")
    # Built-in rules still apply alongside user patterns.
    assert filters.is_junk_file_name("x.tmp")
