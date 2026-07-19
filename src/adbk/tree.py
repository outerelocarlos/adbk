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


def _annotate_text(label: str, node: TreeNode, label_style: str = "") -> Text:
    text = Text(label, style=label_style)
    state = _STATE_LABEL.get(node.access_state)
    if state:
        text.append(f"  ({state})", style="yellow")
    if node.warning and node.access_state is AccessState.READABLE:
        text.append(f"  ({node.warning})", style="dim italic")
    if not node.included:
        text.append("  [excluded]", style="dim")
    return text


def render_lines(node: TreeNode, *, ascii_only: bool = False) -> list[Text]:
    """Render a directory tree with branch connectors and colour.

    Folders are bold, connectors and file sizes are dim, folder summaries are
    cyan, and access-state notes (empty/inaccessible/...) are yellow.
    """

    branches = _ASCII_BRANCHES if ascii_only else _UNICODE_BRANCHES
    label, effective = collapse_chain(node)
    root = Text()
    root.append_text(_annotate_text(label, effective, label_style="bold"))
    return [root, *_render_children(effective, "", branches)]


def _file_line(prefix: str, branch: str, file_node: TreeNode) -> Text:
    line = Text(prefix)
    line.append(branch, style="dim")
    line.append(file_node.name)
    if file_node.size is not None:
        line.append(f"   {human_size(file_node.size)}", style="dim")
    return line


def _render_children(effective: TreeNode, prefix: str, branches: tuple[str, ...]) -> list[Text]:
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

    lines: list[Text] = []
    index = 0
    for file_node in listed_files:
        lines.append(_file_line(prefix, branch(index), file_node))
        index += 1
    if show_summary:
        total_bytes = sum(f.size or 0 for f in files)
        summary = f"{len(files)} files"
        if dirs:
            summary += f", {len(dirs)} subdirs"
        summary += f", {human_size(total_bytes)}"
        line = Text(prefix)
        line.append(branch(index), style="dim")
        line.append(f"[{summary}]", style="cyan")
        lines.append(line)
        index += 1
    for dir_node in dirs:
        last = index == total - 1
        label, effective_child = collapse_chain(dir_node)
        line = Text(prefix)
        line.append(elbow if last else tee, style="dim")
        line.append_text(_annotate_text(label, effective_child, label_style="bold"))
        lines.append(line)
        lines.extend(
            _render_children(effective_child, prefix + (blank if last else vertical), branches)
        )
        index += 1
    return lines


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
