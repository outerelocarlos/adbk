"""Discover which configured paths exist on the device and build folder trees.

Classification never guesses: a path is reported as readable, inaccessible,
missing, root-only or empty, and a **failed listing is raised, not treated as an
empty directory**. Shared-storage aliases are normalized so the same data is not
discovered twice.
"""

from __future__ import annotations

from adbk import paths
from adbk.device import DeviceInterface
from adbk.errors import DeviceAccessError
from adbk.models import (
    AccessState,
    Category,
    DiscoveredPath,
    EntryType,
    TreeNode,
)

# A generous cap so a pathological symlink loop or very deep tree cannot recurse
# forever; nodes below it are marked rather than silently dropped.
MAX_TREE_DEPTH = 40


def classify_path(device: DeviceInterface, category: str, configured_path: str) -> DiscoveredPath:
    """Classify a single configured path into an :class:`AccessState`."""

    normalized = paths.normalize_device_path(configured_path)

    if paths.is_private_data(normalized):
        return DiscoveredPath(
            category=category,
            configured_path=configured_path,
            android_path=normalized,
            access_state=AccessState.ROOT_ONLY,
            entry_type=None,
            reason="app-private data requires root; use an app export path instead",
        )

    if not device.exists(normalized):
        return DiscoveredPath(
            category, configured_path, normalized, AccessState.MISSING, None, "path not found"
        )

    if device.is_dir(normalized):
        if not device.is_readable(normalized):
            return DiscoveredPath(
                category, configured_path, normalized,
                AccessState.INACCESSIBLE, EntryType.DIRECTORY, "not readable",
            )
        try:
            entries = device.list_dir(normalized)
        except DeviceAccessError as exc:
            return DiscoveredPath(
                category, configured_path, normalized,
                AccessState.INACCESSIBLE, EntryType.DIRECTORY, str(exc),
            )
        state = AccessState.EMPTY if not entries else AccessState.READABLE
        return DiscoveredPath(category, configured_path, normalized, state, EntryType.DIRECTORY)

    # A regular file.
    if not device.is_readable(normalized):
        return DiscoveredPath(
            category, configured_path, normalized,
            AccessState.INACCESSIBLE, EntryType.FILE, "not readable",
        )
    return DiscoveredPath(category, configured_path, normalized, AccessState.READABLE, EntryType.FILE)


def discover(device: DeviceInterface, categories: list[Category]) -> list[DiscoveredPath]:
    """Classify every configured path, de-duplicating normalized paths."""

    seen: set[str] = set()
    results: list[DiscoveredPath] = []
    for category in categories:
        for configured in category.paths:
            discovered = classify_path(device, category.name, configured)
            if discovered.android_path in seen:
                continue
            seen.add(discovered.android_path)
            results.append(discovered)
    return results


def build_tree(
    device: DeviceInterface,
    path: str,
    *,
    depth: int = 0,
    max_depth: int = MAX_TREE_DEPTH,
    max_children: int | None = None,
) -> TreeNode:
    """Recursively build a :class:`TreeNode` for a device path.

    Inaccessible sub-directories are marked, never treated as empty. For a
    shallow display pass a small ``max_depth`` and a ``max_children`` breadth cap
    so very wide folders (e.g. hundreds of packages under Android/data) are shown
    at one level instead of being fully walked.
    """

    normalized = paths.normalize_device_path(path)
    name = normalized.rsplit("/", 1)[-1] or normalized

    if not device.is_dir(normalized):
        entry = device.stat(normalized)
        return TreeNode(
            name=name,
            android_path=normalized,
            entry_type=EntryType.FILE,
            access_state=AccessState.READABLE,
            size=entry.size if entry else None,
            mtime=entry.mtime if entry else None,
        )

    node = TreeNode(name, normalized, EntryType.DIRECTORY, AccessState.READABLE)
    try:
        entries = device.list_dir(normalized)
    except DeviceAccessError as exc:
        node.access_state = AccessState.INACCESSIBLE
        node.warning = str(exc)
        return node

    if not entries:
        node.access_state = AccessState.EMPTY
        return node

    ordered = sorted(entries, key=lambda e: (e.entry_type is EntryType.FILE, e.name.lower()))
    subdir_count = sum(1 for e in ordered if e.entry_type is EntryType.DIRECTORY)
    too_wide = max_children is not None and subdir_count > max_children

    for entry in ordered:
        child_path = normalized.rstrip("/") + "/" + entry.name
        if entry.entry_type is EntryType.DIRECTORY:
            if too_wide:
                continue  # collapsed into a single summary line below
            if depth < max_depth:
                node.children.append(
                    build_tree(device, child_path, depth=depth + 1,
                               max_depth=max_depth, max_children=max_children)
                )
            else:
                node.children.append(
                    TreeNode(entry.name, child_path, EntryType.DIRECTORY,
                             AccessState.READABLE, warning="not expanded")
                )
        else:
            node.children.append(
                TreeNode(entry.name, child_path, EntryType.FILE, AccessState.READABLE,
                         size=entry.size, mtime=entry.mtime)
            )

    if too_wide:
        node.warning = f"{subdir_count} subfolders (not expanded)"
    return node
