"""Download and install a private, application-managed copy of ``adb``.

This is intentionally separate from :mod:`adbk.adb` (which only runs
ADB) so each half can be tested on its own. The installer:

* downloads only the official Google platform-tools archive,
* verifies it against the official checksum from the manifest,
* extracts it defensively (no path traversal, absolute paths or symlink escapes),
* validates the extracted ``adb`` actually runs, then
* moves it into place atomically, preserving any previous working install.

Everything uses portable Python APIs - :mod:`urllib`, :mod:`zipfile`,
:mod:`hashlib`, :mod:`tempfile` - so it never depends on ``curl``, ``unzip``,
PowerShell or a system package manager.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import ssl
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from adbk import adb, adb_sources
from adbk import platform_support as ps
from adbk.adb_sources import AdbSource
from adbk.errors import (
    AdbInstallError,
    ChecksumError,
    DownloadError,
    UnsafeArchiveError,
)

# Injected so tests never touch the network.
BytesFetcher = Callable[[str], bytes]
FileDownloader = Callable[[str, Path], None]

# Progress/diagnostic callback: ``on_event(stage, detail)``.
EventCallback = Callable[[str, str], None]

METADATA_FILENAME = "install-metadata.json"
_NETWORK_TIMEOUT = 60.0
_CHUNK = 1 << 20  # 1 MiB


@dataclass(frozen=True)
class InstallMetadata:
    """Recorded facts about the managed installation."""

    version: str
    checksum: str
    checksum_algorithm: str
    source_url: str
    archive_name: str
    installed_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "version": self.version,
            "checksum": self.checksum,
            "checksum_algorithm": self.checksum_algorithm,
            "source_url": self.source_url,
            "archive_name": self.archive_name,
            "installed_at": self.installed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> InstallMetadata:
        return cls(
            version=data.get("version", ""),
            checksum=data.get("checksum", ""),
            checksum_algorithm=data.get("checksum_algorithm", ""),
            source_url=data.get("source_url", ""),
            archive_name=data.get("archive_name", ""),
            installed_at=data.get("installed_at", ""),
        )


@dataclass(frozen=True)
class InstallResult:
    """Returned on a successful install."""

    adb_path: Path
    version: str
    metadata: InstallMetadata


@dataclass(frozen=True)
class UpdateStatus:
    """Result of comparing the managed install against the latest official release."""

    has_managed: bool
    installed_version: str | None
    latest_version: str
    update_available: bool


# --- Network helpers ----------------------------------------------------------


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def default_fetch_bytes(url: str) -> bytes:
    """Read a small resource (the manifest) fully into memory over HTTPS."""

    request = urllib.request.Request(url, headers={"User-Agent": "adbk"})
    try:
        with urllib.request.urlopen(
            request, timeout=_NETWORK_TIMEOUT, context=_ssl_context()
        ) as response:
            data = response.read()
            return bytes(data)
    except urllib.error.URLError as exc:
        raise DownloadError(f"Could not reach {url}: {exc.reason}") from exc


def default_download(url: str, destination: Path) -> None:
    """Stream a URL to a file on disk over HTTPS."""

    request = urllib.request.Request(url, headers={"User-Agent": "adbk"})
    try:
        with urllib.request.urlopen(
            request, timeout=_NETWORK_TIMEOUT, context=_ssl_context()
        ) as response, destination.open("wb") as handle:
            shutil.copyfileobj(response, handle, _CHUNK)
    except urllib.error.URLError as exc:
        raise DownloadError(f"Could not download {url}: {exc.reason}") from exc


# --- Verification and extraction ---------------------------------------------


def _hash_file(path: Path, algorithm: str) -> str:
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:  # unknown algorithm name
        raise ChecksumError(f"Unsupported checksum algorithm: {algorithm!r}") from exc
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksum(archive: Path, source: AdbSource) -> None:
    """Verify the archive against its official checksum; raise on mismatch."""

    if not source.checksum:
        # No official checksum available for this archive; nothing to check.
        return
    actual = _hash_file(archive, source.checksum_algorithm)
    if actual.lower() != source.checksum.lower():
        raise ChecksumError(
            f"Checksum mismatch for {source.archive_name}: "
            f"expected {source.checksum.lower()}, got {actual.lower()}"
        )


def _is_within(base: Path, target: Path) -> bool:
    """True if ``target`` resolves to a location inside ``base``."""

    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def safe_extract_zip(archive: Path, destination: Path) -> None:
    """Extract a zip into ``destination``, rejecting anything unsafe.

    Protects against absolute paths, ``..`` traversal and symlink escapes, and
    never writes outside ``destination``.
    """

    if not zipfile.is_zipfile(archive):
        raise UnsafeArchiveError(f"Downloaded file is not a valid zip archive: {archive}")

    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()

    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            name = member.filename
            member_path = Path(name)

            if member_path.is_absolute() or ".." in member_path.parts:
                raise UnsafeArchiveError(f"Unsafe path in archive: {name!r}")

            # Reject symlink entries (Unix mode 0o120000 in the high bits).
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UnsafeArchiveError(f"Archive contains a symlink: {name!r}")

            target = destination / member_path
            if not _is_within(base, target):
                raise UnsafeArchiveError(f"Archive entry escapes destination: {name!r}")

            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source_file, target.open("wb") as out_file:
                shutil.copyfileobj(source_file, out_file, _CHUNK)

            # Preserve the executable bit so adb stays runnable on POSIX systems.
            unix_mode = member.external_attr >> 16
            if unix_mode & 0o111:
                target.chmod(target.stat().st_mode | 0o111)


# --- Filesystem helpers -------------------------------------------------------


def _on_rm_error(func: Callable[[str], None], path: str, _exc: BaseException) -> None:
    """Make a read-only file writable and retry deletion (needed on Windows)."""

    Path(path).chmod(stat.S_IWRITE)
    func(path)


def _rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onexc=_on_rm_error)


def _atomic_replace_dir(staged: Path, final: Path) -> None:
    """Move ``staged`` to ``final`` atomically, preserving any existing install.

    ``staged`` must live on the same filesystem as ``final`` (the caller stages
    next to the destination) so the rename is atomic. If the final rename fails,
    the previous installation is restored.
    """

    final.parent.mkdir(parents=True, exist_ok=True)
    backup = final.with_name(f"{final.name}.old-{os.getpid()}")
    had_existing = final.exists()

    if had_existing:
        _rmtree(backup)
        final.replace(backup)

    try:
        staged.replace(final)
    except OSError:
        if had_existing:
            backup.replace(final)  # restore the previous install
        raise
    else:
        if had_existing:
            _rmtree(backup)


# --- Metadata -----------------------------------------------------------------


def metadata_path() -> Path:
    return ps.managed_tools_dir() / METADATA_FILENAME


def read_metadata() -> InstallMetadata | None:
    path = metadata_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return InstallMetadata.from_dict({str(k): str(v) for k, v in data.items()})


def is_managed_installed() -> bool:
    """True when a usable application-managed adb executable is present."""

    return adb.managed_adb_path().is_file()


def has_incomplete_install() -> bool:
    """True when the managed directory exists but has no usable adb executable."""

    tools_dir = ps.managed_tools_dir()
    return tools_dir.exists() and not adb.managed_adb_path().is_file()


# --- The install / update operations -----------------------------------------


def _emit(on_event: EventCallback | None, stage: str, detail: str) -> None:
    if on_event is not None:
        on_event(stage, detail)


def install_adb(
    *,
    fetch_bytes: BytesFetcher = default_fetch_bytes,
    download: FileDownloader = default_download,
    on_event: EventCallback | None = None,
) -> InstallResult:
    """Download, verify and install the official platform-tools ``adb``.

    Raises a subclass of :class:`AdbInstallError` on any failure, always leaving
    a previously working managed installation untouched.
    """

    os_name = ps.current_os()
    arch = ps.current_arch()

    _emit(on_event, "resolve", "Reading official platform-tools manifest")
    source = adb_sources.resolve_source(os_name, arch, fetch_bytes)

    final_dir = ps.managed_tools_dir()
    final_dir.parent.mkdir(parents=True, exist_ok=True)

    # Stage extraction *next to* the final directory so the final move is atomic.
    staging_root = Path(tempfile.mkdtemp(prefix=".adb-install-", dir=final_dir.parent))
    archive_fd, archive_name = tempfile.mkstemp(prefix="platform-tools-", suffix=".zip")
    os.close(archive_fd)
    archive_path = Path(archive_name)

    try:
        _emit(on_event, "download", f"Downloading {source.archive_name}")
        download(source.url, archive_path)

        _emit(on_event, "verify", f"Verifying {source.checksum_algorithm or 'archive'}")
        verify_checksum(archive_path, source)

        _emit(on_event, "extract", "Extracting archive")
        extract_dir = staging_root / "extract"
        safe_extract_zip(archive_path, extract_dir)

        # The archive contains a top-level "platform-tools" folder.
        tools_dir = extract_dir / "platform-tools"
        if not tools_dir.is_dir():
            raise AdbInstallError("Archive did not contain a 'platform-tools' folder.")

        staged_adb = tools_dir / ps.executable_name("adb")
        if not staged_adb.is_file():
            raise AdbInstallError("Extracted archive is missing the adb executable.")

        _emit(on_event, "validate", "Running 'adb version'")
        version = adb.query_version(staged_adb)

        metadata = InstallMetadata(
            version=source.version,
            checksum=source.checksum,
            checksum_algorithm=source.checksum_algorithm,
            source_url=source.url,
            archive_name=source.archive_name,
            installed_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        (tools_dir / METADATA_FILENAME).write_text(
            json.dumps(metadata.to_dict(), indent=2), encoding="utf-8"
        )

        _emit(on_event, "install", f"Installing into {ps.display_path(final_dir)}")
        _atomic_replace_dir(tools_dir, final_dir)

        return InstallResult(adb_path=adb.managed_adb_path(), version=version, metadata=metadata)
    finally:
        _emit(on_event, "cleanup", "Removing temporary files")
        _rmtree(staging_root)
        with contextlib.suppress(OSError):
            archive_path.unlink(missing_ok=True)


def check_update(fetch_bytes: BytesFetcher = default_fetch_bytes) -> UpdateStatus:
    """Compare the managed installation against the latest official version."""

    os_name = ps.current_os()
    arch = ps.current_arch()
    source = adb_sources.resolve_source(os_name, arch, fetch_bytes)
    metadata = read_metadata()
    installed = metadata.version if metadata else None
    update_available = installed is not None and installed != source.version
    return UpdateStatus(
        has_managed=metadata is not None,
        installed_version=installed,
        latest_version=source.version,
        update_available=update_available,
    )
