"""Tests for downloading, verifying, extracting and installing a managed ADB."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from adbk import adb, adb_installer
from adbk import platform_support as ps
from adbk.adb_sources import AdbSource
from adbk.errors import (
    AdbError,
    AdbInstallError,
    ChecksumError,
    UnsafeArchiveError,
)
from tests.helpers import build_manifest, make_platform_tools_zip

# --- Checksum verification ----------------------------------------------------


def test_verify_checksum_ok(tmp_path: Path) -> None:
    data = b"hello world"
    archive = tmp_path / "a.zip"
    archive.write_bytes(data)
    source = AdbSource("linux", "u", hashlib.sha256(data).hexdigest(), "sha256", 11, "1", "u")
    adb_installer.verify_checksum(archive, source)  # must not raise


def test_verify_checksum_mismatch(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"hello world")
    source = AdbSource("linux", "u", "00" * 32, "sha256", 11, "1", "u")
    with pytest.raises(ChecksumError):
        adb_installer.verify_checksum(archive, source)


def test_verify_checksum_absent_is_skipped(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"hello world")
    source = AdbSource("linux", "u", "", "", 11, "1", "u")
    adb_installer.verify_checksum(archive, source)  # no checksum -> nothing to verify


# --- Safe extraction ----------------------------------------------------------


def test_safe_extract_ok(tmp_path: Path) -> None:
    exe = ps.executable_name("adb")
    archive = tmp_path / "pt.zip"
    archive.write_bytes(make_platform_tools_zip(exe))
    out = tmp_path / "out"
    adb_installer.safe_extract_zip(archive, out)
    assert (out / "platform-tools" / exe).is_file()


def test_safe_extract_rejects_non_zip(tmp_path: Path) -> None:
    archive = tmp_path / "x.zip"
    archive.write_bytes(b"definitely not a zip")
    with pytest.raises(UnsafeArchiveError):
        adb_installer.safe_extract_zip(archive, tmp_path / "out")


def test_safe_extract_rejects_parent_traversal(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("../evil.txt", b"pwned")
    archive = tmp_path / "evil.zip"
    archive.write_bytes(buffer.getvalue())
    with pytest.raises(UnsafeArchiveError):
        adb_installer.safe_extract_zip(archive, tmp_path / "out")


def test_safe_extract_rejects_symlink(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        info = zipfile.ZipInfo("platform-tools/link")
        info.external_attr = 0o120777 << 16  # symlink mode bits
        zf.writestr(info, "/etc/passwd")
    archive = tmp_path / "link.zip"
    archive.write_bytes(buffer.getvalue())
    with pytest.raises(UnsafeArchiveError):
        adb_installer.safe_extract_zip(archive, tmp_path / "out")


# --- Atomic replacement -------------------------------------------------------


def test_atomic_replace_installs(tmp_path: Path) -> None:
    final = tmp_path / "tools"
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "new.txt").write_text("new", encoding="utf-8")
    adb_installer._atomic_replace_dir(staged, final)
    assert (final / "new.txt").read_text(encoding="utf-8") == "new"


def test_atomic_replace_restores_previous_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = tmp_path / "tools"
    final.mkdir()
    (final / "old.txt").write_text("old", encoding="utf-8")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "new.txt").write_text("new", encoding="utf-8")

    real_replace = Path.replace
    calls = {"n": 0}

    def flaky(self: Path, target: object) -> object:
        calls["n"] += 1
        if calls["n"] == 2:  # the staged -> final rename
            raise OSError("simulated failure")
        return real_replace(self, target)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "replace", flaky)
    with pytest.raises(OSError, match="simulated failure"):
        adb_installer._atomic_replace_dir(staged, final)

    assert (final / "old.txt").read_text(encoding="utf-8") == "old"


# --- Full install flow --------------------------------------------------------


def _manifest_for(host_os: str, checksum: str, size: int, url: str = "pt.zip") -> bytes:
    return build_manifest({host_os: {"url": url, "algo": "sha256", "checksum": checksum, "size": size}})


def test_install_success(
    isolated_tools_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = ps.executable_name("adb")
    zip_bytes = make_platform_tools_zip(exe, adb_content=b"ADB")
    checksum = hashlib.sha256(zip_bytes).hexdigest()
    host_os = adb_installer.adb_sources._HOST_OS[ps.current_os()]
    manifest = _manifest_for(host_os, checksum, len(zip_bytes))

    monkeypatch.setattr(
        adb_installer.adb, "query_version", lambda _p, **_k: "Android Debug Bridge version 1.0.41"
    )

    result = adb_installer.install_adb(
        fetch_bytes=lambda _u: manifest,
        download=lambda _u, dest: dest.write_bytes(zip_bytes),
    )

    assert result.adb_path == adb.managed_adb_path()
    assert result.adb_path.is_file()
    metadata = adb_installer.read_metadata()
    assert metadata is not None
    assert metadata.version == "35.0.2"
    assert metadata.checksum == checksum
    assert adb_installer.is_managed_installed()


def test_install_rejects_bad_checksum(
    isolated_tools_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = ps.executable_name("adb")
    zip_bytes = make_platform_tools_zip(exe)
    host_os = adb_installer.adb_sources._HOST_OS[ps.current_os()]
    manifest = _manifest_for(host_os, "00" * 32, len(zip_bytes))

    with pytest.raises(ChecksumError):
        adb_installer.install_adb(
            fetch_bytes=lambda _u: manifest,
            download=lambda _u, dest: dest.write_bytes(zip_bytes),
        )
    assert not adb_installer.is_managed_installed()


def test_install_preserves_existing_on_failure(
    isolated_tools_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pre-existing, working managed install.
    exe = ps.executable_name("adb")
    isolated_tools_dir.mkdir(parents=True)
    existing = isolated_tools_dir / exe
    existing.write_bytes(b"OLD-WORKING-ADB")

    zip_bytes = make_platform_tools_zip(exe)
    host_os = adb_installer.adb_sources._HOST_OS[ps.current_os()]
    manifest = _manifest_for(host_os, "00" * 32, len(zip_bytes))  # wrong checksum -> failure

    with pytest.raises(ChecksumError):
        adb_installer.install_adb(
            fetch_bytes=lambda _u: manifest,
            download=lambda _u, dest: dest.write_bytes(zip_bytes),
        )
    assert existing.read_bytes() == b"OLD-WORKING-ADB"  # untouched


def test_install_rejects_archive_without_adb(
    isolated_tools_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = ps.executable_name("adb")
    zip_bytes = make_platform_tools_zip(exe, include_adb=False)
    checksum = hashlib.sha256(zip_bytes).hexdigest()
    host_os = adb_installer.adb_sources._HOST_OS[ps.current_os()]
    manifest = _manifest_for(host_os, checksum, len(zip_bytes))

    with pytest.raises(AdbInstallError):
        adb_installer.install_adb(
            fetch_bytes=lambda _u: manifest,
            download=lambda _u, dest: dest.write_bytes(zip_bytes),
        )


def test_install_rejects_failed_version_validation(
    isolated_tools_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = ps.executable_name("adb")
    zip_bytes = make_platform_tools_zip(exe)
    checksum = hashlib.sha256(zip_bytes).hexdigest()
    host_os = adb_installer.adb_sources._HOST_OS[ps.current_os()]
    manifest = _manifest_for(host_os, checksum, len(zip_bytes))

    def boom(_p: Path, **_k: object) -> str:
        raise AdbError("adb version failed")

    monkeypatch.setattr(adb_installer.adb, "query_version", boom)
    with pytest.raises(AdbError):
        adb_installer.install_adb(
            fetch_bytes=lambda _u: manifest,
            download=lambda _u, dest: dest.write_bytes(zip_bytes),
        )
    assert not adb_installer.is_managed_installed()


# --- Update checks ------------------------------------------------------------


def test_check_update_reports_newer(
    isolated_tools_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = ps.executable_name("adb")
    zip_bytes = make_platform_tools_zip(exe)
    checksum = hashlib.sha256(zip_bytes).hexdigest()
    host_os = adb_installer.adb_sources._HOST_OS[ps.current_os()]

    # Install version 35.0.2 first.
    monkeypatch.setattr(adb_installer.adb, "query_version", lambda _p, **_k: "1.0.41")
    adb_installer.install_adb(
        fetch_bytes=lambda _u: _manifest_for(host_os, checksum, len(zip_bytes)),
        download=lambda _u, dest: dest.write_bytes(zip_bytes),
    )

    newer = build_manifest(
        {host_os: {"url": "pt.zip", "algo": "sha256", "checksum": checksum, "size": len(zip_bytes)}},
        version=(36, 0, 0),
    )
    status = adb_installer.check_update(fetch_bytes=lambda _u: newer)
    assert status.has_managed
    assert status.installed_version == "35.0.2"
    assert status.latest_version == "36.0.0"
    assert status.update_available
