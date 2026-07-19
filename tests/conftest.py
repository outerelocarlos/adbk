"""Shared fixtures. All fixtures keep tests offline and device-free."""

from __future__ import annotations

from pathlib import Path

import pytest

from adbk import filters
from adbk import platform_support as ps


@pytest.fixture(autouse=True)
def _reset_filters() -> None:
    """Keep user-configurable junk patterns from leaking between tests."""

    filters.configure([], [])


@pytest.fixture
def isolated_tools_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the managed-tools directory at a clean temporary location."""

    tools_dir = tmp_path / "managed" / "platform-tools"
    monkeypatch.setenv(ps.ENV_TOOLS_DIRECTORY, str(tools_dir))
    monkeypatch.delenv(ps.ENV_ADB_PATH, raising=False)
    return tools_dir


@pytest.fixture
def no_discoverable_adb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ADB discovery find nothing: no PATH, no SDK, no managed, no container."""

    monkeypatch.delenv(ps.ENV_ADB_PATH, raising=False)
    monkeypatch.setenv(ps.ENV_TOOLS_DIRECTORY, str(tmp_path / "empty-tools"))
    monkeypatch.setattr("shutil.which", lambda *_a, **_k: None)
    monkeypatch.setattr(ps, "android_sdk_adb_candidates", list)
    monkeypatch.setattr(ps, "in_container", lambda: False)
