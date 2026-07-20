"""Tests for the platform abstraction, exercised under all three OS values."""

from __future__ import annotations

from pathlib import Path

import pytest

from adbk import platform_support as ps
from adbk.errors import UnsupportedPlatformError


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(ps, "_home", lambda: tmp_path)
    return tmp_path


def _set_os(monkeypatch: pytest.MonkeyPatch, sys_platform: str) -> None:
    monkeypatch.setattr(ps.sys, "platform", sys_platform)


@pytest.mark.parametrize(
    ("sys_platform", "expected"),
    [("win32", "windows"), ("darwin", "macos"), ("linux", "linux"), ("linux2", "linux")],
)
def test_current_os(monkeypatch: pytest.MonkeyPatch, sys_platform: str, expected: str) -> None:
    _set_os(monkeypatch, sys_platform)
    assert ps.current_os() == expected


def test_current_os_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_os(monkeypatch, "sunos5")
    with pytest.raises(UnsupportedPlatformError):
        ps.current_os()


@pytest.mark.parametrize(
    ("machine", "expected"),
    [("AMD64", "x86_64"), ("x86_64", "x86_64"), ("arm64", "arm64"), ("aarch64", "arm64")],
)
def test_current_arch(monkeypatch: pytest.MonkeyPatch, machine: str, expected: str) -> None:
    monkeypatch.setattr(ps.platform, "machine", lambda: machine)
    assert ps.current_arch() == expected


def test_current_arch_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ps.platform, "machine", lambda: "riscv64")
    with pytest.raises(UnsupportedPlatformError):
        ps.current_arch()


def test_executable_name_windows_vs_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_os(monkeypatch, "win32")
    assert ps.executable_name("adb") == "adb.exe"
    _set_os(monkeypatch, "linux")
    assert ps.executable_name("adb") == "adb"


def test_windows_data_dir(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    _set_os(monkeypatch, "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(fake_home / "AppData" / "Local"))
    result = ps.user_data_dir()
    assert result.name == ps.APP_DIRNAME
    assert "Local" in result.parts


def test_macos_data_dir(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    _set_os(monkeypatch, "darwin")
    result = ps.user_data_dir()
    assert result == fake_home / "Library" / "Application Support" / ps.APP_DIRNAME


def test_linux_data_dir_respects_xdg(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    _set_os(monkeypatch, "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / "xdg"))
    assert ps.user_data_dir() == fake_home / "xdg" / ps.APP_DIRNAME


def test_managed_tools_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ps.ENV_TOOLS_DIRECTORY, str(tmp_path / "custom"))
    assert ps.managed_tools_dir() == tmp_path / "custom"


def test_default_backup_dir_container_vs_native(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr(ps, "in_container", lambda: True)
    assert ps.default_backup_dir() == Path("/data/android-backup")
    monkeypatch.setattr(ps, "in_container", lambda: False)
    assert ps.default_backup_dir() == fake_home / "android-backup"


def test_sdk_candidates_include_android_home(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    _set_os(monkeypatch, "linux")
    monkeypatch.setenv("ANDROID_HOME", str(fake_home / "sdk"))
    candidates = ps.android_sdk_adb_candidates()
    assert any("sdk" in str(c) and c.name == "adb" for c in candidates)


def test_display_path_contracts_home(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    _set_os(monkeypatch, "linux")
    shown = ps.display_path(fake_home / "documents" / "file.txt")
    assert shown.startswith("~")
    assert "file.txt" in shown
