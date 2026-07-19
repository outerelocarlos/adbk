"""Tests for parsing Google's manifest and resolving the official source."""

from __future__ import annotations

import pytest

from adbk import adb_sources
from adbk.errors import AdbInstallError, UnsupportedPlatformError
from tests.helpers import build_manifest

_ALL = {
    "windows": {"url": "pt-win.zip", "algo": "sha256", "checksum": "aa11", "size": 10},
    "macosx": {"url": "pt-mac.zip", "algo": "sha256", "checksum": "bb22", "size": 20},
    "linux": {"url": "pt-lin.zip", "algo": "sha1", "checksum": "cc33", "size": 30},
}


def test_parse_windows() -> None:
    source = adb_sources.parse_manifest(build_manifest(_ALL), "windows")
    assert source.archive_name == "pt-win.zip"
    assert source.url.endswith("pt-win.zip")
    assert source.url.startswith(adb_sources.REPOSITORY_BASE_URL)
    assert source.checksum == "aa11"
    assert source.checksum_algorithm == "sha256"
    assert source.size == 10
    assert source.version == "35.0.2"


def test_parse_macos_maps_to_macosx() -> None:
    source = adb_sources.parse_manifest(build_manifest(_ALL), "macos")
    assert source.archive_name == "pt-mac.zip"


def test_parse_linux_sha1() -> None:
    source = adb_sources.parse_manifest(build_manifest(_ALL), "linux")
    assert source.checksum_algorithm == "sha1"


def test_parse_missing_host() -> None:
    only_windows = {"windows": _ALL["windows"]}
    with pytest.raises(AdbInstallError):
        adb_sources.parse_manifest(build_manifest(only_windows), "linux")


def test_resolve_source_uses_manifest_url() -> None:
    seen: dict[str, str] = {}

    def fetch(url: str) -> bytes:
        seen["url"] = url
        return build_manifest(_ALL)

    source = adb_sources.resolve_source("windows", "x86_64", fetch)
    assert seen["url"] == adb_sources.MANIFEST_URL
    assert source.os_name == "windows"


def test_resolve_source_macos_arm64_ok() -> None:
    source = adb_sources.resolve_source("macos", "arm64", lambda _u: build_manifest(_ALL))
    assert source.archive_name == "pt-mac.zip"


def test_resolve_source_linux_arm64_unsupported() -> None:
    with pytest.raises(UnsupportedPlatformError):
        adb_sources.resolve_source("linux", "arm64", lambda _u: b"")
