"""Android path normalization and logical (portable) local paths.

These helpers are pure and platform independent. The manifest stores **logical**
relative paths built here (always forward-slash, never absolute), so a backup is
identical whether it was made natively on Windows/macOS/Linux or inside a
container, and can be restored from any of them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

# The canonical shared-storage root; ``/sdcard`` and others alias to this so the
# same files are never captured twice.
CANONICAL_SHARED_ROOT = "/storage/emulated/0"
_SHARED_ALIASES = ("/storage/emulated/0", "/sdcard", "/mnt/sdcard")

# Private app-data roots (inaccessible without root).
PRIVATE_ROOTS = ("/data/data", "/data/user/0", "/data/user_de/0")

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_device_path(path: str) -> str:
    """Normalize an Android path: collapse slashes, drop trailing slash, alias sdcard.

    ``/sdcard/DCIM/`` and ``/storage/emulated/0/DCIM`` both become
    ``/storage/emulated/0/DCIM``.
    """

    text = path.strip()
    if not text:
        return text
    # Use PurePosixPath so behaviour never depends on the host OS separator.
    pure = PurePosixPath(text)
    normalized = str(pure)
    for alias in _SHARED_ALIASES:
        if normalized == alias:
            return CANONICAL_SHARED_ROOT
        if normalized.startswith(alias + "/"):
            return CANONICAL_SHARED_ROOT + normalized[len(alias) :]
    return normalized


def is_shared_storage(path: str) -> bool:
    normalized = normalize_device_path(path)
    return normalized == CANONICAL_SHARED_ROOT or normalized.startswith(
        CANONICAL_SHARED_ROOT + "/"
    )


def is_private_data(path: str) -> bool:
    normalized = normalize_device_path(path)
    return any(
        normalized == root or normalized.startswith(root + "/") for root in PRIVATE_ROOTS
    )


def sanitize_serial(serial: str) -> str:
    """Make a device serial safe to use as a directory name on any OS."""

    cleaned = _UNSAFE.sub("_", serial.strip())
    return cleaned or "device"


def _relative_within(path: str, root: str) -> str:
    normalized = normalize_device_path(path)
    if normalized == root:
        return ""
    return normalized[len(root) + 1 :]  # strip "root/"


def logical_relative_path(serial: str, android_path: str) -> str:
    """Build the portable, forward-slash relative path stored in the manifest.

    Examples::

        /storage/emulated/0/Documents/a.pdf
            -> devices/<serial>/shared-storage/Documents/a.pdf
        /data/data/com.example/files/x
            -> devices/<serial>/private/com.example/files/x
    """

    safe_serial = sanitize_serial(serial)
    normalized = normalize_device_path(android_path)

    if is_shared_storage(normalized):
        rest = _relative_within(normalized, CANONICAL_SHARED_ROOT)
        section = "shared-storage"
    elif is_private_data(normalized):
        for root in PRIVATE_ROOTS:
            if normalized == root or normalized.startswith(root + "/"):
                rest = _relative_within(normalized, root)
                break
        else:  # pragma: no cover - guarded by is_private_data
            rest = normalized.lstrip("/")
        section = "private"
    else:
        rest = normalized.lstrip("/")
        section = "other"

    parts = ["devices", safe_serial, section]
    if rest:
        parts.append(rest)
    return "/".join(parts)


def apk_relative_path(serial: str, package: str, apk_path: str) -> str:
    """Where a pulled APK is stored: ``devices/<serial>/apks/<package>/<file>``.

    Split apps contribute several APKs, so the file name is kept to tell them
    apart (``base.apk``, ``split_config.arm64_v8a.apk``, ...).
    """

    filename = PurePosixPath(apk_path).name or "base.apk"
    return "/".join(["devices", sanitize_serial(serial), "apks", package, filename])


def drop_nested_paths(paths: Iterable[str]) -> list[str]:
    """Keep only top-level paths, dropping any that sit inside another.

    So selecting both ``/sdcard/Android/data`` and
    ``/sdcard/Android/data/org.thunderdog.challegram`` keeps only the former,
    and the same files are never backed up (or counted) twice.
    """

    unique = sorted({normalize_device_path(p) for p in paths}, key=len)
    kept: list[str] = []
    for candidate in unique:
        if any(candidate == k or candidate.startswith(k + "/") for k in kept):
            continue
        kept.append(candidate)
    return kept


def logical_to_local(backup_root: Path, logical_relative: str) -> Path:
    """Resolve a logical relative path to a real local path under ``backup_root``."""

    segments = [segment for segment in logical_relative.split("/") if segment]
    return backup_root.joinpath(*segments)
