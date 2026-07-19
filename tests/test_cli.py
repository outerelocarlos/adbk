"""Tests for argument parsing and option precedence in the CLI."""

from __future__ import annotations

import pytest

from adbk import cli
from adbk.config import AppConfig


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "adbk" in capsys.readouterr().out


def test_install_and_no_install_conflict() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--install-adb", "--no-install-adb"])
    assert exc.value.code == 2


@pytest.mark.usefixtures("no_discoverable_adb")
def test_doctor_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["doctor", "--plain"]) == 0
    out = capsys.readouterr().out
    assert "Environment" in out
    assert "ADB" in out


@pytest.mark.usefixtures("no_discoverable_adb")
def test_doctor_via_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--doctor", "--plain"]) == 0
    assert "Environment" in capsys.readouterr().out


def test_cli_flag_beats_env_and_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADBK_ADB_SERVER_HOST", "envhost")
    config = AppConfig(adb_server_host="cfghost")
    args = cli.build_parser().parse_args(["--adb-server-host", "flaghost"])
    assert cli._build_options(args, config).server_host == "flaghost"


def test_env_beats_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADBK_ADB_SERVER_HOST", "envhost")
    config = AppConfig(adb_server_host="cfghost")
    args = cli.build_parser().parse_args([])
    assert cli._build_options(args, config).server_host == "envhost"


def test_config_used_when_no_flag_or_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADBK_ADB_SERVER_HOST", raising=False)
    monkeypatch.delenv("ADBK_ADB_SERVER_PORT", raising=False)
    config = AppConfig(adb_server_host="cfghost", adb_server_port=5999)
    args = cli.build_parser().parse_args([])
    options = cli._build_options(args, config)
    assert options.server_host == "cfghost"
    assert options.server_port == 5999


def test_config_auto_install_maps_to_flags() -> None:
    args = cli.build_parser().parse_args([])
    assert cli._build_options(args, AppConfig(auto_install_adb=True)).install is True
    assert cli._build_options(args, AppConfig(auto_install_adb=False)).no_install is True
