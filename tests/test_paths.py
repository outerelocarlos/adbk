"""Tests for Android path normalization and portable local paths."""

from __future__ import annotations

from pathlib import Path

from adbk import paths


def test_normalize_sdcard_alias() -> None:
    assert paths.normalize_device_path("/sdcard/DCIM/") == "/storage/emulated/0/DCIM"
    assert paths.normalize_device_path("/storage/emulated/0/DCIM") == "/storage/emulated/0/DCIM"
    assert paths.normalize_device_path("/sdcard") == "/storage/emulated/0"


def test_is_shared_and_private() -> None:
    assert paths.is_shared_storage("/sdcard/Documents")
    assert not paths.is_shared_storage("/data/data/com.x")
    assert paths.is_private_data("/data/data/com.x/files")
    assert paths.is_private_data("/data/user/0/com.x")


def test_logical_relative_shared() -> None:
    logical = paths.logical_relative_path("ABC123", "/sdcard/Documents/report.pdf")
    assert logical == "devices/ABC123/shared-storage/Documents/report.pdf"


def test_logical_relative_private() -> None:
    logical = paths.logical_relative_path("ABC", "/data/data/com.x/files/save.dat")
    assert logical == "devices/ABC/private/com.x/files/save.dat"


def test_logical_relative_sanitizes_serial() -> None:
    logical = paths.logical_relative_path("192.168.1.5:5555", "/sdcard/a.txt")
    assert logical == "devices/192.168.1.5_5555/shared-storage/a.txt"


def test_logical_handles_spaces_and_unicode() -> None:
    logical = paths.logical_relative_path("S", "/sdcard/Mis Cosas/José Álvarez.txt")
    assert logical == "devices/S/shared-storage/Mis Cosas/José Álvarez.txt"


def test_logical_relative_sanitizes_illegal_windows_characters() -> None:
    # The ':' in a Xiaomi dump dir is legal on Android but breaks mkdir on Windows.
    logical = paths.logical_relative_path(
        "S",
        "/sdcard/Android/data/com.xiaomi.account/files/dump/process-com.xiaomi:accountservice/0",
    )
    assert ":" not in logical
    assert logical.endswith("process-com.xiaomi_accountservice/0")


def test_sanitize_component_rules() -> None:
    assert paths._sanitize_component('a:b*c?"') == "a_b_c__"
    assert paths._sanitize_component("trailing. ") == "trailing__"  # dot+space -> "__"
    assert paths._sanitize_component("CON").startswith("_")  # reserved device name
    assert paths._sanitize_component("normal.dat") == "normal.dat"  # untouched


def test_apk_relative_path_sanitizes() -> None:
    assert paths.apk_relative_path("S", "com.x", "/data/app/base.apk") == (
        "devices/S/apks/com.x/base.apk"
    )


def test_drop_nested_paths_keeps_ancestors() -> None:
    kept = paths.drop_nested_paths([
        "/sdcard/Android/data",
        "/sdcard/Android/data/org.thunderdog.challegram",
        "/sdcard/Documents",
        "/sdcard/Android/data/com.x/files",
    ])
    assert set(kept) == {"/storage/emulated/0/Android/data", "/storage/emulated/0/Documents"}


def test_drop_nested_paths_normalizes_and_dedupes() -> None:
    kept = paths.drop_nested_paths(["/sdcard/DCIM", "/storage/emulated/0/DCIM"])
    assert kept == ["/storage/emulated/0/DCIM"]


def test_logical_to_local_is_cross_platform(tmp_path: Path) -> None:
    logical = "devices/S/shared-storage/DCIM/Camera/a.jpg"
    local = paths.logical_to_local(tmp_path, logical)
    # The local path uses the host separator but always ends with the same parts.
    assert local == tmp_path / "devices" / "S" / "shared-storage" / "DCIM" / "Camera" / "a.jpg"
    assert local.name == "a.jpg"
