"""Tests for loading the optional TOML configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from adbk import config
from adbk.config import AppConfig
from adbk.errors import ConfigError


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    assert config.load_config(tmp_path / "nope.toml") == AppConfig()


def test_none_path_returns_defaults() -> None:
    assert config.load_config(None) == AppConfig()


def test_valid_config(tmp_path: Path) -> None:
    path = tmp_path / "backup-config.toml"
    path.write_text(
        '[backup]\n'
        'destination = "/data/backup"\n'
        '[adb]\n'
        'server_host = "host.docker.internal"\n'
        'server_port = 5555\n'
        'auto_install = true\n',
        encoding="utf-8",
    )
    loaded = config.load_config(path)
    assert loaded.backup_dir == Path("/data/backup")
    assert loaded.adb_server_host == "host.docker.internal"
    assert loaded.adb_server_port == 5555
    assert loaded.auto_install_adb is True


def test_invalid_toml_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("this is = = not valid", encoding="utf-8")
    with pytest.raises(ConfigError):
        config.load_config(path)


def test_wrong_type_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text('[adb]\nserver_port = "not-a-number"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        config.load_config(path)


def test_filters_section(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text(
        '[filters]\nignore_dirs = ["a", "b*"]\nignore_files = ["*.bak"]\n', encoding="utf-8"
    )
    loaded = config.load_config(path)
    assert loaded.ignore_dirs == ("a", "b*")
    assert loaded.ignore_files == ("*.bak",)


def test_filters_wrong_type_raises(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('[filters]\nignore_dirs = "notalist"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        config.load_config(path)


def test_unicode_and_spaces_in_paths(tmp_path: Path) -> None:
    path = tmp_path / "u.toml"
    path.write_text('[backup]\ndestination = "/home/José Álvarez/mis copias"\n', encoding="utf-8")
    loaded = config.load_config(path)
    assert loaded.backup_dir == Path("/home/José Álvarez/mis copias")
