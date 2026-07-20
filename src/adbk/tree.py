"""Pure adaptive rendering of the folder tree, plus selection walking.

The rules:

* files directly in a folder: 0 -> none, 1-3 -> list them, 4+ -> a summary
  (file count, subdirectory count, total size);
* subdirectories are still shown adaptively;
* a chain of single-child directories collapses onto one line using ``>``,
  stopping at files, branches, inaccessible/empty entries or metadata.

This module has no I/O, so all of it is unit-tested directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from rich.text import Text

from adbk import filters
from adbk.models import AccessState, EntryType, TreeNode

# Stop collapsing a single-child chain once it would be this many segments long.
MAX_CHAIN_SEGMENTS = 6

_STATE_LABEL = {
    AccessState.EMPTY: "empty",
    AccessState.INACCESSIBLE: "inaccessible",
    AccessState.ROOT_ONLY: "root only",
    AccessState.MISSING: "missing",
}


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _is_plain_readable_dir(node: TreeNode) -> bool:
    """A directory that carries no metadata worth showing on its own line."""

    return (
        node.entry_type is EntryType.DIRECTORY
        and node.access_state is AccessState.READABLE
        and node.warning is None
    )


def is_worthwhile(node: TreeNode) -> bool:
    """Whether a node is worth displaying / backing up.

    Junk (thumbnail/cache/trash folders, ``.nomedia`` and similar files) is
    always hidden. Otherwise files count, and a directory is hidden when it
    (recursively) contains no worthwhile files -- i.e. empty folders and folders
    made only of empty/junk folders -- but an inaccessible, root-only or
    not-yet-expanded folder is kept, because it may hold data we cannot see.
    """

    if node.entry_type is EntryType.FILE:
        return not filters.is_junk_file_name(node.name)
    if filters.is_junk_dir_name(node.name):
        return False
    if node.access_state not in (AccessState.READABLE, AccessState.EMPTY):
        return True
    if node.warning is not None:
        return True
    return any(is_worthwhile(child) for child in node.children)


def worthwhile_dirs(node: TreeNode) -> list[TreeNode]:
    return [child for child in node.direct_dirs if is_worthwhile(child)]


def worthwhile_files(node: TreeNode) -> list[TreeNode]:
    return [child for child in node.direct_files if not filters.is_junk_file_name(child.name)]


def collapse_chain(node: TreeNode) -> tuple[str, TreeNode]:
    """Collapse a single-child directory chain, returning (label, effective node)."""

    names = [node.name]
    current = node
    while len(names) < MAX_CHAIN_SEGMENTS and _is_plain_readable_dir(current):
        children = worthwhile_dirs(current)
        if worthwhile_files(current) or len(children) != 1:
            break
        child = children[0]
        if not _is_plain_readable_dir(child):
            break
        names.append(child.name)
        current = child
    return " > ".join(names), current


# Branch connectors: (tee, elbow, vertical, blank). Unicode by default with an
# ASCII fallback for terminals/streams that cannot encode box-drawing.
_UNICODE_BRANCHES = ("├─ ", "└─ ", "│  ", "   ")
_ASCII_BRANCHES = ("|- ", "`- ", "|  ", "   ")


@dataclass
class _Row:
    """One line before layout: the label, its size, and any trailing notes.

    Sizes are kept apart from the label so they can share one right-aligned
    column across the whole tree, the way the selection screen lines them up.
    """

    head: Text
    size: int | None = None
    notes: Text | None = None


def _notes(node: TreeNode) -> Text | None:
    """Access state, warnings and exclusion markers, or None when there are none."""

    text = Text()
    state = _STATE_LABEL.get(node.access_state)
    if state:
        text.append(f"  ({state})", style="yellow")
    if node.warning and node.access_state is AccessState.READABLE:
        text.append(f"  ({node.warning})", style="dim italic")
    if not node.included:
        text.append("  [excluded]", style="dim")
    return text if text.plain else None


def _dir_size(node: TreeNode) -> int | None:
    """A folder total, when one was measured (files carry their own size).

    Nothing is shown for a folder we could not read: its state note says why,
    and a number we could not verify would only mislead.
    """

    if node.entry_type is not EntryType.DIRECTORY:
        return None
    if node.access_state is not AccessState.READABLE:
        return None
    return node.size


def _lay_out(rows: list[_Row]) -> list[Text]:
    """Pad every label to the same width so the sizes form one column."""

    head_width = max((len(row.head.plain) for row in rows), default=0)
    size_width = max(
        (len(human_size(row.size)) for row in rows if row.size is not None), default=0
    )

    lines: list[Text] = []
    for row in rows:
        line = row.head.copy()
        if row.size is None and row.notes is None:
            lines.append(line)  # nothing follows, so do not pad
            continue
        line.pad_right(head_width - len(row.head.plain))
        if size_width:
            if row.size is None:
                line.append("  " + " " * size_width)
            else:
                line.append("  " + human_size(row.size).rjust(size_width), style="dim")
        if row.notes is not None:
            line.append_text(row.notes)
        lines.append(line)
    return lines


def render_lines(node: TreeNode, *, ascii_only: bool = False) -> list[Text]:
    """Render a directory tree with branch connectors, colour and aligned sizes.

    Folders are bold, connectors and sizes are dim, folder summaries are cyan,
    and access-state notes (empty/inaccessible/...) are yellow.
    """

    branches = _ASCII_BRANCHES if ascii_only else _UNICODE_BRANCHES
    label, effective = collapse_chain(node)
    root = _Row(Text(label, style="bold"), _dir_size(effective), _notes(effective))
    return _lay_out([root, *_collect_children(effective, "", branches)])


def _collect_children(effective: TreeNode, prefix: str, branches: tuple[str, ...]) -> list[_Row]:
    if effective.access_state is not AccessState.READABLE:
        return []

    files = worthwhile_files(effective)
    dirs = worthwhile_dirs(effective)
    tee, elbow, vertical, blank = branches

    listed_files = files if 1 <= len(files) <= 3 else []
    show_summary = len(files) >= 4
    total = len(listed_files) + (1 if show_summary else 0) + len(dirs)

    def branch(index: int) -> str:
        return elbow if index == total - 1 else tee

    rows: list[_Row] = []
    index = 0
    for file_node in listed_files:
        head = Text(prefix)
        head.append(branch(index), style="dim")
        head.append(file_node.name)
        rows.append(_Row(head, file_node.size))
        index += 1
    if show_summary:
        summary = f"{len(files)} files"
        if dirs:
            summary += f", {len(dirs)} subdirs"
        head = Text(prefix)
        head.append(branch(index), style="dim")
        head.append(f"[{summary}]", style="cyan")
        rows.append(_Row(head, sum(file.size or 0 for file in files)))
        index += 1
    for dir_node in dirs:
        last = index == total - 1
        label, child = collapse_chain(dir_node)
        head = Text(prefix)
        head.append(elbow if last else tee, style="dim")
        head.append(label, style="bold")
        rows.append(_Row(head, _dir_size(child), _notes(child)))
        rows.extend(
            _collect_children(child, prefix + (blank if last else vertical), branches)
        )
        index += 1
    return rows


def iter_included_files(node: TreeNode) -> Iterator[TreeNode]:
    """Yield every included, readable file under a node (respecting exclusions)."""

    if not node.included:
        return
    if node.entry_type is EntryType.FILE:
        if node.access_state is AccessState.READABLE:
            yield node
        return
    for child in node.children:
        yield from iter_included_files(child)


def iter_dirs(node: TreeNode) -> Iterator[TreeNode]:
    """Yield every directory node in the tree (including the root)."""

    if node.entry_type is EntryType.DIRECTORY:
        yield node
    for child in node.children:
        yield from iter_dirs(child)


def set_included(node: TreeNode, included: bool) -> None:
    """Set inclusion on a node and everything beneath it."""

    node.included = included
    for child in node.children:
        set_included(child, included)


def find_node(node: TreeNode, android_path: str) -> TreeNode | None:
    if node.android_path == android_path:
        return node
    for child in node.children:
        found = find_node(child, android_path)
        if found is not None:
            return found
    return None
