"""Where the managed ADB comes from, and nothing else.

The installer downloads **only** the official Android SDK Platform-Tools archive
published by Google. All of the knowledge about that source - the base URL, the
manifest that lists the current archives, and how to read an archive's official
checksum - lives here so it stays in one maintainable place instead of being
scattered through the code.

We never download from mirrors, GitHub releases or package aggregators.
"""

from __future__ import annotations

# We parse only a trusted, HTTPS-only Google manifest; no untrusted XML input.
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

from adbk.errors import AdbInstallError, UnsupportedPlatformError
from adbk.platform_support import Architecture, OperatingSystem

# Official Google endpoints. The manifest lists the current platform-tools
# archives together with their size and checksum for each host operating system.
REPOSITORY_BASE_URL = "https://dl.google.com/android/repository/"
MANIFEST_URL = REPOSITORY_BASE_URL + "repository2-3.xml"

# A known-good revision, recorded for documentation and for pinning the version
# baked into the Docker image. The live installer still reads the manifest so it
# always fetches a matching official checksum.
PINNED_PLATFORM_TOOLS_VERSION = "35.0.2"

# Map our normalized OS name to the "host-os" value used inside Google's manifest.
_HOST_OS: dict[OperatingSystem, str] = {
    "windows": "windows",
    "macos": "macosx",
    "linux": "linux",
}

# A ``fetch`` reads the bytes at a URL. It is injected so tests never touch the
# network and the real network code stays in :mod:`adbk.adb_installer`.
Fetcher = Callable[[str], bytes]


@dataclass(frozen=True)
class AdbSource:
    """A single, resolved, official download for one operating system."""

    os_name: OperatingSystem
    url: str
    checksum: str
    checksum_algorithm: str
    size: int
    version: str
    archive_name: str


def _local(tag: str) -> str:
    """Return an XML tag name without its namespace prefix."""

    return tag.rsplit("}", 1)[-1]


def _find_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _find_text(element: ET.Element, name: str) -> str | None:
    child = _find_child(element, name)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _read_revision(package: ET.Element) -> str:
    """Turn a ``<revision>`` element into a dotted version string like ``35.0.2``."""

    revision = _find_child(package, "revision")
    if revision is None:
        return PINNED_PLATFORM_TOOLS_VERSION
    parts: list[str] = []
    for name in ("major", "minor", "micro"):
        value = _find_text(revision, name)
        if value is None:
            break
        parts.append(value)
    return ".".join(parts) if parts else PINNED_PLATFORM_TOOLS_VERSION


def parse_manifest(xml_bytes: bytes, os_name: OperatingSystem) -> AdbSource:
    """Parse Google's repository manifest and return the platform-tools source.

    Raises :class:`AdbInstallError` if the manifest does not contain a
    platform-tools archive for the requested operating system.
    """

    host_os = _HOST_OS[os_name]
    root = ET.fromstring(xml_bytes)

    for package in root.iter():
        if _local(package.tag) != "remotePackage":
            continue
        if package.get("path") != "platform-tools":
            continue
        version = _read_revision(package)
        for archive in package.iter():
            if _local(archive.tag) != "archive":
                continue
            if _find_text(archive, "host-os") != host_os:
                continue
            complete = _find_child(archive, "complete")
            if complete is None:
                continue
            relative_url = _find_text(complete, "url")
            size_text = _find_text(complete, "size")
            checksum_element = _find_child(complete, "checksum")
            if relative_url is None or size_text is None or checksum_element is None:
                continue
            checksum = (checksum_element.text or "").strip()
            algorithm = checksum_element.get("type", "sha1").lower()
            return AdbSource(
                os_name=os_name,
                url=REPOSITORY_BASE_URL + relative_url,
                checksum=checksum,
                checksum_algorithm=algorithm,
                size=int(size_text),
                version=version,
                archive_name=relative_url,
            )

    raise AdbInstallError(
        f"Google's platform-tools manifest has no archive for host {host_os!r}."
    )


def resolve_source(os_name: OperatingSystem, arch: Architecture, fetch: Fetcher) -> AdbSource:
    """Resolve the official platform-tools download for this OS and architecture.

    Windows and macOS use a single 64-bit/universal archive regardless of CPU.
    Linux platform-tools are published for x86_64 only, so Linux on arm64 is
    reported as unsupported with actionable guidance.
    """

    if os_name == "linux" and arch == "arm64":
        raise UnsupportedPlatformError(
            "Google does not publish Linux arm64 Android platform-tools. "
            "Install adb from your distribution's package manager, or run the "
            "container in host ADB-server mode instead."
        )

    xml_bytes = fetch(MANIFEST_URL)
    return parse_manifest(xml_bytes, os_name)
