"""Junk-folder / junk-file filtering.

Thumbnail caches, trash folders, temp and cache directories, and marker files
like ``.nomedia`` are worthless in a migration backup. They are hidden from the
tree, skipped in the drill-in picker, and never transferred.

Built-in matching is deliberately conservative (substrings only for clearly
cache/thumb folders, exact names otherwise). Users can extend it via the
``[filters]`` section of ``backup-config.toml`` -- see :func:`configure`.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable

# Substrings that mark a directory as junk regardless of surrounding text
# (e.g. ".Thumbs", ".StickerThumbs", ".thumbnails", ".wamocache", "composeCache").
_JUNK_DIR_SUBSTRINGS = ("thumb", "cache")

# Exact directory names (case-insensitive) that are junk.
_JUNK_DIR_EXACT = frozenset({
    ".trash", ".trashed", ".trashed-1000", "lost.dir",
    ".recycle", ".recyclebin", "$recycle.bin",  # file-manager recycle bins (deleted files)
    "log", "logs", ".log", ".logs",
    "tmp", ".tmp", "temp", ".temp",
    ".face", ".faces",
    ".gs_fs0",  # Glide image-loading library disk cache (regenerable)
})

# Files that carry no migration value.
_JUNK_FILE_EXACT = frozenset({
    ".nomedia", "thumbs.db", ".ds_store", "desktop.ini", ".directory",
})
# Temp files, and Android MediaStore trash/pending files (e.g. ".escheck.tmp",
# ".trashed-1700000000-photo.jpg", ".pending-1700000000-video.mp4").
_JUNK_FILE_SUFFIXES = (".tmp",)
_JUNK_FILE_PREFIXES = (".trashed-", ".pending-")

# User-supplied patterns (from config); replaced by configure().
_extra_dir_patterns: list[str] = []
_extra_file_patterns: list[str] = []


def configure(ignore_dirs: Iterable[str], ignore_files: Iterable[str]) -> None:
    """Set the user's extra junk patterns (from ``[filters]`` in config).

    Patterns extend the built-in defaults, may use shell wildcards (``*``, ``?``,
    ``[]``) and are matched case-insensitively against a single path component.
    """

    _extra_dir_patterns[:] = [pattern.lower() for pattern in ignore_dirs]
    _extra_file_patterns[:] = [pattern.lower() for pattern in ignore_files]


def _matches_extra(name: str, patterns: list[str]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in patterns)


def is_junk_dir_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in _JUNK_DIR_EXACT:
        return True
    if any(token in lowered for token in _JUNK_DIR_SUBSTRINGS):
        return True
    return _matches_extra(name, _extra_dir_patterns)


def is_junk_file_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in _JUNK_FILE_EXACT:
        return True
    if lowered.endswith(_JUNK_FILE_SUFFIXES) or lowered.startswith(_JUNK_FILE_PREFIXES):
        return True
    return _matches_extra(name, _extra_file_patterns)


def path_has_junk_dir(path: str) -> bool:
    """True if any directory segment of ``path`` is a junk folder."""

    return any(is_junk_dir_name(segment) for segment in path.split("/") if segment)
