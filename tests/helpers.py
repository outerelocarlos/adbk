"""Helpers for building fake platform-tools archives and manifests in tests.

These let the whole test suite avoid the network and avoid needing a real
``adb`` binary or Android device.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping


def make_platform_tools_zip(
    exe_name: str,
    *,
    adb_content: bytes = b"fake-adb-binary",
    include_adb: bool = True,
    make_executable: bool = True,
    extra_files: Mapping[str, bytes] | None = None,
) -> bytes:
    """Return the bytes of a zip shaped like Google's platform-tools archive.

    The archive always contains a top-level ``platform-tools/`` folder. Set
    ``include_adb=False`` to simulate a broken archive missing the executable.
    """

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if include_adb:
            info = zipfile.ZipInfo(f"platform-tools/{exe_name}")
            if make_executable:
                info.external_attr = 0o100755 << 16
            zf.writestr(info, adb_content)
        zf.writestr("platform-tools/NOTICE.txt", b"notice")
        for name, content in (extra_files or {}).items():
            zf.writestr(name, content)
    return buffer.getvalue()


def build_manifest(
    archives: Mapping[str, Mapping[str, object]],
    *,
    version: tuple[int, int, int] = (35, 0, 2),
) -> bytes:
    """Build a small repository manifest for the given host-os archives.

    ``archives`` maps a host-os value (``windows`` / ``macosx`` / ``linux``) to a
    mapping with ``url``, ``algo``, ``checksum`` and ``size``.
    """

    major, minor, micro = version
    blocks = []
    for host_os, archive in archives.items():
        blocks.append(
            f"""
      <archive>
        <complete>
          <size>{archive["size"]}</size>
          <checksum type="{archive["algo"]}">{archive["checksum"]}</checksum>
          <url>{archive["url"]}</url>
        </complete>
        <host-os>{host_os}</host-os>
      </archive>"""
        )
    xml = f"""<?xml version="1.0"?>
<sdk:sdk-repository xmlns:sdk="http://schemas.android.com/sdk/android/repo/repository2/03">
  <remotePackage path="platform-tools">
    <revision><major>{major}</major><minor>{minor}</minor><micro>{micro}</micro></revision>
    <display-name>Android SDK Platform-Tools</display-name>
    <archives>{"".join(blocks)}
    </archives>
  </remotePackage>
</sdk:sdk-repository>"""
    return xml.encode("utf-8")
