"""Tests for the install-policy layer (when may we download ADB?)."""

from __future__ import annotations

from pathlib import Path

import pytest

from adbk import adb_installer, adb_setup, ui
from adbk import platform_support as ps
from adbk.adb import AdbResolution
from adbk.adb_installer import InstallMetadata, InstallResult
from adbk.adb_setup import AdbOptions
from adbk.errors import AdbNotFoundError


def _console() -> object:
    return ui.make_console(plain=True)


def _fake_install_result(tmp_path: Path) -> InstallResult:
    adb_path = tmp_path / ps.executable_name("adb")
    adb_path.write_text("x", encoding="utf-8")
    metadata = InstallMetadata("1.0", "abc", "sha256", "url", "pt.zip", "2026-01-01T00:00:00")
    return InstallResult(adb_path=adb_path, version="1.0", metadata=metadata)


def test_returns_existing_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    resolution = AdbResolution(Path("/usr/bin/adb"), "path")
    monkeypatch.setattr(adb_setup.adb, "resolve_adb", lambda _p: resolution)
    result = adb_setup.resolve_or_install_adb(AdbOptions(), _console(), interactive=False)
    assert result is resolution


def test_dry_run_reports_and_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adb_setup.adb, "resolve_adb", lambda _p: None)
    result = adb_setup.resolve_or_install_adb(
        AdbOptions(dry_run=True), _console(), interactive=False
    )
    assert result is None


def test_no_install_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adb_setup.adb, "resolve_adb", lambda _p: None)
    with pytest.raises(AdbNotFoundError):
        adb_setup.resolve_or_install_adb(
            AdbOptions(no_install=True), _console(), interactive=False
        )


def test_container_without_install_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adb_setup.adb, "resolve_adb", lambda _p: None)
    monkeypatch.setattr(ps, "in_container", lambda: True)
    with pytest.raises(AdbNotFoundError):
        adb_setup.resolve_or_install_adb(AdbOptions(), _console(), interactive=False)


def test_non_interactive_without_permission_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adb_setup.adb, "resolve_adb", lambda _p: None)
    monkeypatch.setattr(ps, "in_container", lambda: False)
    with pytest.raises(AdbNotFoundError):
        adb_setup.resolve_or_install_adb(AdbOptions(), _console(), interactive=False)


def test_assume_yes_installs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(adb_setup.adb, "resolve_adb", lambda _p: None)
    monkeypatch.setattr(ps, "in_container", lambda: False)
    monkeypatch.setattr(adb_setup.adb_installer, "install_adb", lambda **_k: _fake_install_result(tmp_path))
    result = adb_setup.resolve_or_install_adb(
        AdbOptions(assume_yes=True), _console(), interactive=False
    )
    assert result is not None
    assert result.kind == "managed"


def test_interactive_confirm_installs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(adb_setup.adb, "resolve_adb", lambda _p: None)
    monkeypatch.setattr(ps, "in_container", lambda: False)
    monkeypatch.setattr(adb_setup.ui, "confirm", lambda *_a, **_k: True)
    monkeypatch.setattr(adb_setup.adb_installer, "install_adb", lambda **_k: _fake_install_result(tmp_path))
    result = adb_setup.resolve_or_install_adb(AdbOptions(), _console(), interactive=True)
    assert result is not None
    assert result.kind == "managed"


def test_interactive_decline_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adb_setup.adb, "resolve_adb", lambda _p: None)
    monkeypatch.setattr(ps, "in_container", lambda: False)
    monkeypatch.setattr(adb_setup.ui, "confirm", lambda *_a, **_k: False)
    with pytest.raises(AdbNotFoundError):
        adb_setup.resolve_or_install_adb(AdbOptions(), _console(), interactive=True)


# --- update-adb ---------------------------------------------------------------


def test_update_no_managed(monkeypatch: pytest.MonkeyPatch) -> None:
    status = adb_installer.UpdateStatus(False, None, "36.0.0", False)
    monkeypatch.setattr(adb_setup.adb_installer, "check_update", lambda: status)
    assert adb_setup.run_update_adb(AdbOptions(), _console(), interactive=False) == 0


def test_update_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    status = adb_installer.UpdateStatus(True, "36.0.0", "36.0.0", False)
    monkeypatch.setattr(adb_setup.adb_installer, "check_update", lambda: status)
    assert adb_setup.run_update_adb(AdbOptions(), _console(), interactive=False) == 0


def test_update_available_requires_confirmation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    status = adb_installer.UpdateStatus(True, "35.0.2", "36.0.0", True)
    monkeypatch.setattr(adb_setup.adb_installer, "check_update", lambda: status)
    installed = {"n": 0}

    def fake_install(**_k: object) -> InstallResult:
        installed["n"] += 1
        return _fake_install_result(tmp_path)

    monkeypatch.setattr(adb_setup.adb_installer, "install_adb", fake_install)

    # Non-interactive, no permission -> does not update.
    adb_setup.run_update_adb(AdbOptions(), _console(), interactive=False)
    assert installed["n"] == 0

    # With --yes -> updates.
    adb_setup.run_update_adb(AdbOptions(assume_yes=True), _console(), interactive=False)
    assert installed["n"] == 1
