"""Tests for ADB discovery, device parsing and the subprocess wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from adbk import adb
from adbk import platform_support as ps
from adbk.errors import AdbError, AdbNotFoundError


def _make_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


@pytest.fixture
def clean_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ps.ENV_ADB_PATH, raising=False)
    monkeypatch.setenv(ps.ENV_TOOLS_DIRECTORY, str(tmp_path / "empty"))
    monkeypatch.setattr("shutil.which", lambda *_a, **_k: None)
    monkeypatch.setattr(ps, "android_sdk_adb_candidates", list)
    monkeypatch.setattr(ps, "in_container", lambda: False)


def test_resolve_explicit(tmp_path: Path) -> None:
    exe = _make_file(tmp_path / "adb")
    resolution = adb.resolve_adb(explicit_path=exe)
    assert resolution is not None
    assert resolution.kind == "explicit"
    assert resolution.path == exe


def test_resolve_explicit_missing(tmp_path: Path) -> None:
    with pytest.raises(AdbNotFoundError):
        adb.resolve_adb(explicit_path=tmp_path / "nope")


def test_resolve_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _make_file(tmp_path / "envadb")
    monkeypatch.setenv(ps.ENV_ADB_PATH, str(exe))
    monkeypatch.setenv(ps.ENV_TOOLS_DIRECTORY, str(tmp_path / "empty"))
    resolution = adb.resolve_adb()
    assert resolution is not None
    assert resolution.kind == "environment"


def test_resolve_managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ps.ENV_ADB_PATH, raising=False)
    tools = tmp_path / "tools" / "platform-tools"
    monkeypatch.setenv(ps.ENV_TOOLS_DIRECTORY, str(tools))
    _make_file(tools / ps.executable_name("adb"))
    resolution = adb.resolve_adb()
    assert resolution is not None
    assert resolution.kind == "managed"


@pytest.mark.usefixtures("clean_discovery")
def test_resolve_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _make_file(tmp_path / "onpath" / ps.executable_name("adb"))
    monkeypatch.setattr("shutil.which", lambda *_a, **_k: str(exe))
    resolution = adb.resolve_adb()
    assert resolution is not None
    assert resolution.kind == "path"


@pytest.mark.usefixtures("clean_discovery")
def test_resolve_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _make_file(tmp_path / "onpath" / ps.executable_name("adb"))
    monkeypatch.setattr("shutil.which", lambda *_a, **_k: str(exe))
    monkeypatch.setattr(ps, "in_container", lambda: True)
    resolution = adb.resolve_adb()
    assert resolution is not None
    assert resolution.kind == "container"


@pytest.mark.usefixtures("clean_discovery")
def test_resolve_sdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _make_file(tmp_path / "sdk" / ps.executable_name("adb"))
    monkeypatch.setattr(ps, "android_sdk_adb_candidates", lambda: [exe])
    resolution = adb.resolve_adb()
    assert resolution is not None
    assert resolution.kind == "sdk"


@pytest.mark.usefixtures("clean_discovery")
def test_resolve_none() -> None:
    assert adb.resolve_adb() is None


def test_parse_devices_states() -> None:
    output = (
        "List of devices attached\n"
        "ABC123\tdevice product:x model:y device:z\n"
        "ZZZ999\tunauthorized\n"
        "* daemon started successfully *\n"
    )
    devices = adb.parse_devices(output)
    assert len(devices) == 2
    assert devices[0].serial == "ABC123"
    assert devices[0].is_ready
    assert devices[1].state == "unauthorized"
    assert not devices[1].is_ready


def test_parse_devices_no_permissions() -> None:
    output = "List of devices attached\nSER123\tno permissions; see [http://x]\n"
    devices = adb.parse_devices(output)
    assert devices[0].state == "no permissions"


def test_client_base_args() -> None:
    client = adb.AdbClient(Path("adb"), server_host="host.docker.internal", server_port=5037)
    assert client._base == ["adb", "-H", "host.docker.internal", "-P", "5037"]


def test_run_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise FileNotFoundError

    monkeypatch.setattr(adb.subprocess, "run", boom)
    client = adb.AdbClient(Path("adb"))
    with pytest.raises(AdbNotFoundError):
        client.run(["devices"])


def test_run_check_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(adb.subprocess, "run", lambda *_a, **_k: FakeCompleted())
    client = adb.AdbClient(Path("adb"))
    with pytest.raises(AdbError):
        client.run(["devices"])


def test_version_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "Android Debug Bridge version 1.0.41\nVersion 35.0.2\n"
        stderr = ""

    monkeypatch.setattr(adb.subprocess, "run", lambda *_a, **_k: FakeCompleted())
    client = adb.AdbClient(Path("adb"))
    assert client.version().startswith("Android Debug Bridge")
