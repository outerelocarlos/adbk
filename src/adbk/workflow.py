"""High-level backup and restore workflows.

Design for responsiveness on large devices:

* the plan **summary** and **dry-run** use the ``du`` size estimates already
  computed for selection, so they are instant -- no file walk;
* the optional **tree review** builds only a shallow, width-bounded tree;
* the real **transfer** enumerates each root once and streams files as they are
  pulled, showing progress instead of stalling.

Everything takes a :class:`DeviceInterface`, so the whole flow is testable with
an in-memory fake device.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

from rich.console import Console
from rich.text import Text

from adbk import apps, discovery, filters, paths, selection, ui
from adbk import platform_support as ps
from adbk import tree as treemod
from adbk.device import DeviceInterface, RawEntry
from adbk.errors import DeviceAccessError
from adbk.manifest import (
    MANIFEST_FILENAME,
    AppRecord,
    Manifest,
    ManifestEntry,
    load_manifest,
)
from adbk.models import (
    AccessState,
    AppAvailability,
    Category,
    ConflictPolicy,
    DeviceIdentity,
    DiscoveredPath,
    EntryType,
    ManifestState,
    Operation,
    TransferMode,
    TreeNode,
)
from adbk.restore import RestoreSummary, group_name
from adbk.restore import restore as run_restore_files
from adbk.selection import CategoryEstimate
from adbk.transfer import CancellationToken, RootResult, transfer_root

# Shallow-tree display limits (only used when the user asks to review).
_REVIEW_MAX_DEPTH = 2
_REVIEW_MAX_CHILDREN = 25
_PROGRESS_EVERY = 500

_SKIP_REASONS = {
    AccessState.MISSING: "missing",
    AccessState.INACCESSIBLE: "inaccessible",
    AccessState.ROOT_ONLY: "root only",
    AccessState.EMPTY: "empty",
}


# --- Backup directory layout --------------------------------------------------
#
# The backup root holds one dated sub-directory per backup, e.g.
# ``~/adbk/2026-07-20/``. A new run gets today's date (with a suffix
# if that date already holds a backup); restore and resume look up the most
# recent dated backup under the root.


def next_backup_dir(root: Path, *, today: date | None = None) -> Path:
    """A fresh dated backup directory ``<root>/YYYY-MM-DD`` for a new run.

    Same-day reruns get a numeric suffix (``-2``, ``-3``, ...) so an existing
    backup is never written into.
    """

    stamp = (today or date.today()).isoformat()
    candidate = root / stamp
    suffix = 2
    while (candidate / MANIFEST_FILENAME).exists():
        candidate = root / f"{stamp}-{suffix}"
        suffix += 1
    return candidate


def find_latest_backup(root: Path) -> Path | None:
    """The most recent backup under ``root`` (or ``root`` itself if it holds one).

    A directory "holds a backup" when it contains a manifest. Dated
    sub-directories sort chronologically by name, so the last one is the newest.
    """

    if (root / MANIFEST_FILENAME).is_file():
        return root
    if not root.is_dir():
        return None
    dated = sorted(
        (child for child in root.iterdir() if (child / MANIFEST_FILENAME).is_file()),
        key=lambda path: path.name,
    )
    return dated[-1] if dated else None


def backup_date(backup_dir: Path) -> date | None:
    """The date a dated backup directory records, parsed from its name.

    ``2026-08-07`` and same-day reruns like ``2026-08-07-2`` both read as that
    day; anything that is not a dated directory returns ``None``.
    """

    try:
        return date.fromisoformat(backup_dir.name[:10])
    except ValueError:
        return None


def describe_age(days: int) -> str:
    """A human phrase for how long ago a backup was made."""

    if days <= 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def existing_backup_notice(root: Path, *, today: date | None = None) -> tuple[Path, str] | None:
    """If a backup already exists under ``root``, its path and a human age phrase.

    ``None`` when the root holds no backup yet, so the caller only warns when
    there is genuinely something already there.
    """

    existing = find_latest_backup(root)
    if existing is None:
        return None
    made = backup_date(existing)
    if made is None:
        return existing, "previously"
    return existing, describe_age(((today or date.today()) - made).days)


@dataclass
class BackupOutcome:
    state: ManifestState
    manifest_path: Path | None
    copied: int = 0
    verified: int = 0
    deleted: int = 0
    kept: int = 0
    failed: int = 0
    skipped_paths: list[tuple[str, str]] = field(default_factory=list)


# --- Selection rendering ------------------------------------------------------

_NAME_PREFIX = 11
_SIZE_WIDTH = 11


def build_selection_rows(estimates: list[CategoryEstimate]) -> list[Text]:
    """Build the styled selection table as rich Text rows (pure; unit-tested)."""

    name_width = max([len(e.name) for e in estimates] + [len("Total selected")])
    rows: list[Text] = [Text("Select categories to back up:", style="bold")]

    for index, estimate in enumerate(estimates, 1):
        checked = estimate.included
        row = Text()
        row.append(f"  {index:>2}  ")
        row.append("[x]" if checked else "[ ]", style="bold green" if checked else "dim")
        row.append("  ")
        row.append(estimate.name.ljust(name_width), style=None if checked else "dim")
        row.append("  ")
        row.append(
            treemod.human_size(estimate.effective_size()).rjust(_SIZE_WIDTH),
            style="cyan" if checked else "dim",
        )
        if estimate.exclusions:
            row.append(
                f"  (-{treemod.human_size(estimate.excluded_bytes())} excluded)", style="dim"
            )
        rows.append(row)

    rows.append(Text("  " + "-" * (name_width + _SIZE_WIDTH + 6), style="dim"))
    total_row = Text(" " * _NAME_PREFIX)
    total_row.append("Total selected".ljust(name_width), style="bold")
    total_row.append("  ")
    total_row.append(
        treemod.human_size(selection.selected_total_bytes(estimates)).rjust(_SIZE_WIDTH),
        style="bold cyan",
    )
    rows.append(total_row)
    return rows


def _print_legend(console: Console, entries: list[tuple[str, str]]) -> None:
    """Print an aligned command legend: keys highlighted, descriptions dim.

    A leading blank line gives the legend room to breathe under the content above.
    """

    key_width = max(len(key) for key, _ in entries)
    console.print()
    console.print(Text("  Commands", style="bold"))
    for key, description in entries:
        line = Text("    ")
        line.append(key.ljust(key_width), style="bold cyan")
        line.append("   ")
        line.append(description, style="dim")
        console.print(line)


def _select_categories(
    console: Console, device: DeviceInterface, estimates: list[CategoryEstimate]
) -> None:
    """Interactive category picker with per-category drill-in sub-selection."""

    while True:
        console.print()
        for row in build_selection_rows(estimates):
            console.print(row)
        _print_legend(console, [
            ("<num>", "toggle that category on / off"),
            ("d <num>", "drill into that category to pick sub-folders"),
            ("a / n", "select all / none"),
            ("Enter", "continue"),
        ])
        console.print()
        try:
            raw = input("> ").strip().lower()
        except EOFError:
            return
        if raw == "":
            return
        if raw == "a":
            for estimate in estimates:
                estimate.included = True
        elif raw == "n":
            for estimate in estimates:
                estimate.included = False
        elif raw.startswith("d") and raw[1:].strip().isdigit():
            number = int(raw[1:].strip())
            if 1 <= number <= len(estimates):
                _drill_into(console, device, estimates[number - 1], estimates)
        elif raw.isdigit():
            number = int(raw)
            if 1 <= number <= len(estimates):
                estimates[number - 1].included = not estimates[number - 1].included
        else:
            console.print("  (unrecognized command)")


_MAX_DESCEND = 40  # safety bound for single-child-chain descent
_EMPTY_DU_MAX = 16 * 1024  # a truly empty folder is ~7-8 KiB here; hide those from the picker

# A drilled item is a (path, size-or-None) pair.
DrillItem = tuple[str, int | None]
# Per-session cache of ``du -d 1`` results, keyed by folder path.
SizeCache = dict[str, dict[str, int]]


def _basename(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] or path


def _is_excluded_path(path: str, exclusions: dict[str, int]) -> bool:
    return any(path == ex or path.startswith(ex.rstrip("/") + "/") for ex in exclusions)


def _files_label(files: list[RawEntry]) -> str:
    """A short description of the loose files directly in a folder."""

    return ", ".join(f.name for f in files) if len(files) <= 3 else f"{len(files)} files"


@dataclass
class _DrillEntry:
    """A selectable row in the drill view: a sub-folder or the loose-files group."""

    label: str
    size: int
    kind: str  # "dir" or "files"
    dir_path: str | None = None
    files: list[tuple[str, int]] = field(default_factory=list)  # (path, size)


def _entry_excluded(entry: _DrillEntry, exclusions: dict[str, int]) -> bool:
    if entry.kind == "dir":
        return entry.dir_path is not None and _is_excluded_path(entry.dir_path, exclusions)
    return bool(entry.files) and all(_is_excluded_path(fp, exclusions) for fp, _ in entry.files)


def _toggle_entry(entry: _DrillEntry, exclusions: dict[str, int]) -> None:
    if entry.kind == "dir" and entry.dir_path is not None:
        if entry.dir_path in exclusions:
            del exclusions[entry.dir_path]
        else:
            exclusions[entry.dir_path] = entry.size
    elif entry.kind == "files":
        if all(fp in exclusions for fp, _ in entry.files):
            for fp, _ in entry.files:
                exclusions.pop(fp, None)
        else:
            for fp, size in entry.files:
                exclusions[fp] = size


def _list_subdir_paths(device: DeviceInterface, path: str) -> list[str]:
    """Immediate, non-junk sub-folder paths from a single ``ls`` (no sizes)."""

    try:
        entries = device.list_dir(path)
    except DeviceAccessError:
        return []
    return [
        f"{path.rstrip('/')}/{e.name}"
        for e in entries
        if e.entry_type is EntryType.DIRECTORY and not filters.is_junk_dir_name(e.name)
    ]


def _child_dirs(
    device: DeviceInterface, path: str, size_cache: SizeCache, console: Console
) -> tuple[list[DrillItem], list[RawEntry]]:
    """Sub-folders (with sizes, empty/junk hidden) + the loose files in a folder.

    Sizes come from a single ``du -d 1`` walk (cached), so a folder is measured
    at most once per session. Hiding empty folders is cosmetic -- a hidden folder
    is still backed up unless explicitly excluded.
    """

    try:
        files = [
            e for e in device.list_dir(path)
            if e.entry_type is EntryType.FILE and not filters.is_junk_file_name(e.name)
        ]
    except DeviceAccessError:
        return [], []

    tree_sizes = size_cache.get(path)
    if tree_sizes is None:
        console.print(Text("  Measuring sub-folder sizes ...", style="dim"))
        tree_sizes = device.disk_usage_tree(path)
        size_cache[path] = tree_sizes

    items: list[DrillItem] = []
    for entry, size in tree_sizes.items():
        if entry.rstrip("/") == path.rstrip("/"):
            continue  # the folder's own total
        if filters.is_junk_dir_name(_basename(entry)) or size <= _EMPTY_DU_MAX:
            continue
        items.append((entry, size))
    items.sort(key=lambda item: _basename(item[0]).lower())
    return items, files


def _descend_single(device: DeviceInterface, root: str) -> str:
    current = root
    for _ in range(_MAX_DESCEND):
        subs = _list_subdir_paths(device, current)
        if len(subs) != 1:
            return current
        current = subs[0]
    return current


def _tree_connector(console: Console) -> str:
    """The elegant box-drawing connector, or an ASCII fallback if unencodable."""

    encoding = getattr(console.file, "encoding", None) or "utf-8"
    try:
        "└─".encode(encoding)  # └─
        return "└─ "
    except (UnicodeEncodeError, LookupError):
        return "|_ "


def _footer_row(console: Console, label: str, size_bytes: int, content_width: int) -> None:
    row = Text(("  " + label).ljust(content_width), style="bold")
    row.append("  ")
    row.append(treemod.human_size(size_bytes).rjust(_SIZE_WIDTH), style="bold cyan")
    console.print(row)


def _print_drill_footer(
    console: Console,
    estimate: CategoryEstimate,
    estimates: list[CategoryEstimate],
    content_width: int,
    breadcrumb_rows: list[tuple[str, int]],
) -> None:
    console.print(Text("  " + "-" * (content_width + _SIZE_WIDTH), style="dim"))
    _footer_row(console, estimate.name, estimate.effective_size(), content_width)
    for label, size in breadcrumb_rows:
        _footer_row(console, label, size, content_width)
    _footer_row(console, "Total selected", selection.selected_total_bytes(estimates), content_width)


def _drill_into(
    console: Console,
    device: DeviceInterface,
    estimate: CategoryEstimate,
    estimates: list[CategoryEstimate],
) -> None:
    """Navigate a category's sub-folders and toggle which are included."""

    size_cache: SizeCache = {}
    roots = list(estimate.readable_paths)
    # Each stack entry is (path-or-None, size-of-that-folder). The first entry is
    # the category root; deeper entries form the footer breadcrumb.
    start: str | None = None if len(roots) != 1 else _descend_single(device, roots[0])
    stack: list[tuple[str | None, int]] = [(start, estimate.effective_size())]

    while stack:
        current = stack[-1][0]
        if current is None:
            items: list[DrillItem] = [(root, estimate.path_sizes.get(root)) for root in roots]
            files: list[RawEntry] = []
        else:
            items, files = _child_dirs(device, current, size_cache, console)

        console.print()

        # Build the selectable rows: sub-folders, then the loose-files group.
        entries: list[_DrillEntry] = [
            _DrillEntry(_basename(path), size or 0, "dir", dir_path=path) for path, size in items
        ]
        if files and current is not None:
            file_pairs = [(f"{current.rstrip('/')}/{f.name}", f.size or 0) for f in files]
            entries.append(
                _DrillEntry(_files_label(files), sum(s for _, s in file_pairs), "files", files=file_pairs)
            )

        if not entries:
            console.print(Text("  (nothing to pick here)", style="dim"))

        # One size column for the whole screen: wide enough for the longest row
        # label AND the longest footer label (deep breadcrumbs can be long).
        connector = _tree_connector(console)
        breadcrumb_rows = [
            ("   " * depth + connector + _basename(path), size)
            for depth, (path, size) in enumerate(stack[1:])
            if path is not None
        ]
        label_width = max((len(entry.label) for entry in entries), default=0)
        footer_labels = [estimate.name, *(label for label, _ in breadcrumb_rows), "Total selected"]
        content_width = max(
            [_NAME_PREFIX + label_width] + [2 + len(label) for label in footer_labels]
        )

        for index, entry in enumerate(entries, 1):
            excluded = _entry_excluded(entry, estimate.exclusions)
            name_style: str | None
            if excluded:
                name_style = "dim italic" if entry.kind == "files" else "dim"
            else:
                name_style = "italic" if entry.kind == "files" else None
            row = Text(f"  {index:>2}  ")
            row.append("[ ]" if excluded else "[x]", style="dim" if excluded else "bold green")
            row.append("  ")
            row.append(entry.label.ljust(content_width - _NAME_PREFIX), style=name_style)
            row.append("  ")
            row.append(
                treemod.human_size(entry.size).rjust(_SIZE_WIDTH),
                style="dim" if excluded else "cyan",
            )
            console.print(row)

        _print_drill_footer(console, estimate, estimates, content_width, breadcrumb_rows)
        _print_legend(console, [
            ("<num>", "include / exclude"),
            ("d <num>", "drill into that sub-folder"),
            ("b", "back"),
            ("Enter", "done"),
        ])
        console.print()
        try:
            raw = input("> ").strip().lower()
        except EOFError:
            return
        if raw == "":
            return
        if raw == "b":
            if len(stack) > 1:
                stack.pop()
            else:
                return
        elif raw.startswith("d") and raw[1:].strip().isdigit():
            idx = int(raw[1:].strip()) - 1
            if 0 <= idx < len(entries):
                entry = entries[idx]
                if entry.kind == "dir" and entry.dir_path is not None:
                    stack.append((entry.dir_path, entry.size))
                else:
                    console.print(
                        Text("  Cannot drill into files -- they have no sub-folders.", style="yellow")
                    )
        elif raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(entries):
                _toggle_entry(entries[idx], estimate.exclusions)
        else:
            console.print(Text("  (unrecognized command)", style="dim"))


def _report_selection(console: Console, estimates: list[CategoryEstimate]) -> None:
    included = [e for e in estimates if e.included]
    total = selection.selected_total_bytes(estimates)
    noun = "category" if len(included) == 1 else "categories"
    console.print(f"\nSelected {len(included)} {noun}, estimated {treemod.human_size(total)}.")


# --- Shallow review + sub-folder exclusion ------------------------------------


def group_roots_by_category(kept_pairs: list[tuple[str, str]]) -> list[tuple[str, list[str]]]:
    """Collect each category's roots together, keeping the original order."""

    grouped: dict[str, list[str]] = {}
    for category, root in kept_pairs:
        grouped.setdefault(category, []).append(root)
    return list(grouped.items())


def common_parent(roots: list[str]) -> str | None:
    """The deepest folder several roots share, when it is worth showing.

    ``Android/data`` and ``Android/obb`` share ``Android``, which makes a useful
    single tree. ``DCIM`` and ``Pictures`` only share the shared-storage root
    itself, which would add a meaningless level, so that returns ``None``.
    """

    if len(roots) < 2:
        return None
    shared: list[str] = []
    for segments in zip(*(PurePosixPath(root).parts for root in roots), strict=False):
        if len(set(segments)) != 1:
            break
        shared.append(segments[0])
    if not shared:
        return None
    candidate = str(PurePosixPath(*shared))
    # Only worthwhile when deeper than the shared-storage root.
    return candidate if candidate.startswith(paths.CANONICAL_SHARED_ROOT + "/") else None


def annotate_dir_sizes(node: TreeNode, sizes: dict[str, int]) -> None:
    """Attach ``du`` totals to every directory in the tree, in place.

    A folder's total covers what the tree does not expand, so an unexpanded
    folder still shows how much it holds.
    """

    if node.entry_type is EntryType.DIRECTORY:
        size = sizes.get(node.android_path)
        if size is not None:
            node.size = size
    for child in node.children:
        annotate_dir_sizes(child, sizes)


def _review_tree(
    console: Console,
    device: DeviceInterface,
    kept_pairs: list[tuple[str, str]],
    *,
    force_apk: tuple[str, ...] = (),
) -> None:
    """Optionally show the shallow tree of what will be backed up (display only)."""

    console.print()
    if not ui.confirm(
        console, "Want to review what's about to be backed up?", interactive=True, default=False
    ):
        return
    ascii_only = _tree_connector(console) == "|_ "
    console.print(Text("  Measuring folder sizes ...", style="dim"))

    for category, roots in group_roots_by_category(kept_pairs):
        nodes = []
        for root in roots:
            node = discovery.build_tree(
                device, root, max_depth=_REVIEW_MAX_DEPTH, max_children=_REVIEW_MAX_CHILDREN
            )
            # Sizes are a nicety; never fail the review over them.
            with contextlib.suppress(DeviceAccessError):
                annotate_dir_sizes(node, device.disk_usage_tree(root, depth=_REVIEW_MAX_DEPTH))
            nodes.append(node)
        # Several roots under one folder read better as a single tree.
        shared = common_parent(roots)
        if shared is not None:
            total = sum(child.size or 0 for child in nodes)
            nodes = [
                TreeNode(
                    name=PurePosixPath(shared).name or shared,
                    android_path=shared,
                    entry_type=EntryType.DIRECTORY,
                    size=total or None,
                    children=nodes,
                )
            ]

        console.print(Text(f"\n{category}:", style="bold"))
        for node in nodes:
            for line in treemod.render_lines(node, ascii_only=ascii_only):
                console.print(line)

    _review_apks(console, device, force_apk)


def _review_apks(
    console: Console, device: DeviceInterface, force_apk: tuple[str, ...]
) -> None:
    """List the app APKs that will be kept, so the review covers apps too."""

    try:
        installers = device.list_packages_with_installer()
    except DeviceAccessError:
        return
    packages = apps.apks_to_back_up(installers, force_apk)
    if not packages:
        return

    console.print(Text(f"\nApp APKs to keep: {len(packages)}", style="bold"))
    for package in packages[:_APP_LIST_LIMIT]:
        console.print(f"  {package}")
    remaining = len(packages) - _APP_LIST_LIMIT
    if remaining > 0:
        console.print(Text(f"  ... and {remaining} more", style="cyan"))
    console.print(Text(
        "  (these are sideloaded apps; store apps reinstall themselves, and the "
        "store check adds any delisted ones)",
        style="dim",
    ))


# --- Skips --------------------------------------------------------------------


def _skip_reason(path: DiscoveredPath) -> str:
    base = _SKIP_REASONS.get(path.access_state, "skipped")
    return f"{base}: {path.reason}" if path.reason else base


# --- Backup -------------------------------------------------------------------


def run_backup(
    device: DeviceInterface,
    *,
    backup_root: Path,
    categories: list[Category],
    mode: TransferMode | None,
    console: Console,
    dry_run: bool = False,
    interactive: bool = False,
    assume_yes: bool = False,
    cancel: CancellationToken | None = None,
    resume: bool = False,
    config_snapshot: dict[str, str] | None = None,
    force_apk: tuple[str, ...] = (),
    check_store: bool | None = None,
) -> BackupOutcome:
    """Run a full backup (or a dry-run plan) against ``device``."""

    identity = device.identity()
    console.print(
        f"Device: {identity.model or identity.serial} "
        f"(Android {identity.android_version or '?'})"
    )
    discovered = discovery.discover(device, categories)
    estimates = selection.estimate_categories(device, discovered, categories)

    if interactive and estimates:
        _select_categories(console, device, estimates)
    if estimates:
        _report_selection(console, estimates)

    included_categories = {e.name for e in estimates if e.included}
    selected = selection.selected_paths(estimates)
    exclusions = selection.all_exclusions(estimates)
    readable_selected = [
        path.android_path for path in discovered
        if path.access_state is AccessState.READABLE and path.android_path in selected
    ]
    kept = set(paths.drop_nested_paths(readable_selected))
    kept_pairs = [
        (path.category, path.android_path) for path in discovered
        if path.access_state is AccessState.READABLE and path.android_path in kept
    ]
    skipped = [
        (path.android_path, _skip_reason(path)) for path in discovered
        if path.access_state is not AccessState.READABLE and path.category in included_categories
    ]

    # The selection report above already states the size, so no plan line here.
    total_bytes = selection.selected_total_bytes(estimates)

    # ``None`` means no --copy/--safe-move flag was given, so ask.
    if mode is None:
        mode = _select_mode(console, interactive=interactive)

    if interactive and kept_pairs:
        _review_tree(console, device, kept_pairs, force_apk=force_apk)

    if dry_run:
        console.print(
            f"\n(dry-run, mode {_MODE_LABEL[mode]}: "
            "no files were pulled, deleted or written.)"
        )
        return BackupOutcome(ManifestState.CANCELLED, None, skipped_paths=skipped)

    if not kept_pairs:
        console.print("Nothing to back up.")
        return BackupOutcome(ManifestState.COMPLETED, None, skipped_paths=skipped)

    if mode is TransferMode.SAFE_MOVE and not _confirm_safe_move(
        console, total_bytes, interactive, assume_yes
    ):
        console.print("Safe move not confirmed; aborting before any deletion.")
        return BackupOutcome(ManifestState.CANCELLED, None, skipped_paths=skipped)

    manifest_path = backup_root / MANIFEST_FILENAME
    manifest = _new_or_resume_manifest(
        manifest_path, resume, identity, mode, included_categories, skipped, config_snapshot
    )

    outcome = _run_transfers(
        device, kept_pairs, backup_root, mode, manifest, manifest_path,
        exclusions, cancel, resume, console, identity.serial, skipped,
    )

    _record_apps(
        device, manifest, backup_root, identity.serial, console,
        force_apk=force_apk, check_store=check_store,
        interactive=interactive, assume_yes=assume_yes,
    )

    manifest.finalize(outcome.state)
    manifest.save(manifest_path)
    outcome.manifest_path = manifest_path
    _report_outcome(console, outcome)
    return outcome


def _record_apps(
    device: DeviceInterface,
    manifest: Manifest,
    backup_root: Path,
    serial: str,
    console: Console,
    *,
    force_apk: tuple[str, ...],
    check_store: bool | None,
    interactive: bool,
    assume_yes: bool,
) -> None:
    """Inventory the installed apps, keeping the APKs we could not re-download."""

    console.print("\nRecording installed apps so they can be reinstalled on restore ...")

    # ``None`` means the user did not force the choice with a flag, so ask.
    if check_store is None:
        check_store = ui.confirm(
            console,
            "  Also ask the app store which apps are still listed? "
            "(slower, but keeps the APK of delisted apps)",
            assume_yes=assume_yes, interactive=interactive, default=True,
        )

    def progress(done: int, total: int) -> None:
        if done == total or done % 25 == 0:
            console.print(f"    apps: {done}/{total}")

    try:
        records = apps.collect(
            device,
            serial=serial,
            backup_root=backup_root,
            force_packages=force_apk,
            check_store=check_store,
            on_progress=progress,
        )
    except DeviceAccessError as exc:
        console.print(f"    (could not list installed apps: {exc})")
        return

    manifest.apps = records
    if check_store:
        answered = sum(1 for record in records if record.store_checked)
        if answered < len(records):
            console.print(Text(
                f"    (the store answered for {answered}/{len(records)}; the rest "
                "fell back to the installer)", style="yellow",
            ))
    grouped = apps.summarize(records)
    console.print(
        f"    {len(records)} app(s): "
        f"{len(grouped[AppAvailability.BACKUP])} kept as APK, "
        f"{len(grouped[AppAvailability.STORE])} from the store, "
        f"{len(grouped[AppAvailability.UNAVAILABLE])} unavailable."
    )


def _new_or_resume_manifest(
    manifest_path: Path,
    resume: bool,
    identity: DeviceIdentity,
    mode: TransferMode,
    categories: set[str],
    skipped: list[tuple[str, str]],
    config_snapshot: dict[str, str] | None,
) -> Manifest:
    if resume and manifest_path.is_file():
        manifest = load_manifest(manifest_path)
        manifest.finalize(ManifestState.IN_PROGRESS)
        return manifest

    manifest = Manifest(operation=Operation.BACKUP, mode=mode, device=identity)
    manifest.selected_categories = sorted(categories)
    manifest.config_snapshot = config_snapshot or {"mode": str(mode)}
    for path_str, reason in skipped:
        manifest.mark_skipped(path_str, reason)
    return manifest


def _run_transfers(
    device: DeviceInterface,
    kept_pairs: list[tuple[str, str]],
    backup_root: Path,
    mode: TransferMode,
    manifest: Manifest,
    manifest_path: Path,
    exclusions: set[str],
    cancel: CancellationToken | None,
    resume: bool,
    console: Console,
    serial: str,
    skipped: list[tuple[str, str]],
) -> BackupOutcome:
    outcome = BackupOutcome(ManifestState.IN_PROGRESS, manifest_path, skipped_paths=list(skipped))
    cancelled = False

    for category, root in kept_pairs:
        if cancel is not None and cancel.requested:
            cancelled = True
            break
        console.print(f"\n{category}: scanning {ps.display_path(Path(root))} ...")

        def progress(done: int, total: int, _category: str = category) -> None:
            if done == total or done % _PROGRESS_EVERY == 0:
                console.print(f"    {_category}: {done}/{total} files")

        result = transfer_root(
            device, root, category, serial, backup_root, mode, manifest, manifest_path,
            exclusions=exclusions, cancel=cancel, resume=resume, on_file=progress,
        )
        _merge(outcome, result)
        if result.cancelled:
            cancelled = True
            break

    if cancelled:
        outcome.state = ManifestState.CANCELLED
    elif outcome.failed or outcome.kept:
        outcome.state = ManifestState.COMPLETED_WITH_WARNINGS
    else:
        outcome.state = ManifestState.COMPLETED
    return outcome


def _merge(outcome: BackupOutcome, result: RootResult) -> None:
    outcome.copied += result.copied
    outcome.verified += result.verified
    outcome.deleted += result.deleted
    outcome.kept += result.kept
    outcome.failed += result.failed


_MODE_LABEL = {TransferMode.COPY: "copy", TransferMode.SAFE_MOVE: "safe move"}

_MODE_CHOICES: tuple[tuple[TransferMode, str, str], ...] = (
    (TransferMode.COPY, "Copy", "leave everything on the phone"),
    (
        TransferMode.SAFE_MOVE,
        "Safe move",
        "delete each file from the phone once its copy is verified",
    ),
)


def build_mode_rows(selected: TransferMode) -> list[Text]:
    """The transfer-mode table as styled rows (pure; unit-tested)."""

    name_width = max(len(name) for _, name, _ in _MODE_CHOICES)
    rows: list[Text] = [Text("How should the files be transferred?", style="bold")]
    for index, (mode, name, description) in enumerate(_MODE_CHOICES, 1):
        chosen = mode is selected
        row = Text()
        row.append(f"  {index:>2}  ")
        row.append("[x]" if chosen else "[ ]", style="bold green" if chosen else "dim")
        row.append("  ")
        row.append(name.ljust(name_width), style=None if chosen else "dim")
        row.append("   ")
        row.append(description, style="dim")
        rows.append(row)
    return rows


def _select_mode(console: Console, *, interactive: bool) -> TransferMode:
    """Ask how to transfer the files. Copy is the default: it never deletes.

    Non-interactive runs get copy too, so an unattended backup can never delete
    anything from the phone without ``--safe-move`` being asked for explicitly.
    """

    if not interactive:
        return TransferMode.COPY

    while True:
        console.print()
        for row in build_mode_rows(TransferMode.COPY):
            console.print(row)
        _print_legend(console, [
            ("<num>", "choose that mode"),
            ("Enter", "continue with copy"),
        ])
        console.print()
        try:
            raw = input("> ").strip()
        except EOFError:
            return TransferMode.COPY
        if raw == "":
            return TransferMode.COPY
        if raw.isdigit() and 1 <= int(raw) <= len(_MODE_CHOICES):
            return _MODE_CHOICES[int(raw) - 1][0]
        console.print(Text("  (unrecognized command)", style="dim"))


def _confirm_safe_move(
    console: Console, total_bytes: int, interactive: bool, assume_yes: bool
) -> bool:
    console.print(
        f"\nWARNING: verified safe move will DELETE each source file (~"
        f"{treemod.human_size(total_bytes)}) from the device after it is copied and "
        "its SHA-256 is confirmed."
    )
    return ui.confirm(
        console, "Proceed with verified safe move?",
        assume_yes=assume_yes, interactive=interactive, default=False,
    )


def _report_outcome(console: Console, outcome: BackupOutcome) -> None:
    console.print(
        f"\nDone [{outcome.state}]: copied {outcome.copied}, verified {outcome.verified}, "
        f"deleted {outcome.deleted}, kept {outcome.kept}, failed {outcome.failed}."
    )
    if outcome.manifest_path is not None:
        console.print(f"Manifest: {ps.display_path(outcome.manifest_path)}")


# --- Restore ------------------------------------------------------------------
#
# Restore mirrors the backup flow: a header, an interactive selection screen
# (grouped like the backup categories), a plan summary, an optional review, an
# explicit confirmation before anything is written to the device, then per-group
# progress and a final report.


@dataclass
class RestoreGroup:
    """A selectable group of manifest file entries (the restore-side "category")."""

    name: str
    entries: list[ManifestEntry] = field(default_factory=list)
    included: bool = True

    def size(self) -> int:
        return sum(entry.size or 0 for entry in self.entries)

    def count(self) -> int:
        return len(self.entries)


def build_restore_groups(manifest: Manifest) -> list[RestoreGroup]:
    """Group a manifest's file entries by category (path-derived for old manifests)."""

    groups: dict[str, RestoreGroup] = {}
    for entry in manifest.entries.values():
        if entry.entry_type is not EntryType.FILE:
            continue
        name = group_name(entry)
        groups.setdefault(name, RestoreGroup(name)).entries.append(entry)
    return [groups[name] for name in sorted(groups, key=str.lower)]


def _restore_selected_bytes(groups: list[RestoreGroup]) -> int:
    return sum(group.size() for group in groups if group.included)


def build_restore_rows(groups: list[RestoreGroup]) -> list[Text]:
    """Styled restore-selection table (pure; unit-tested), mirroring the backup picker."""

    name_width = max([len(group.name) for group in groups] + [len("Total selected")])
    rows: list[Text] = [Text("Select what to restore:", style="bold")]

    for index, group in enumerate(groups, 1):
        checked = group.included
        row = Text()
        row.append(f"  {index:>2}  ")
        row.append("[x]" if checked else "[ ]", style="bold green" if checked else "dim")
        row.append("  ")
        row.append(group.name.ljust(name_width), style=None if checked else "dim")
        row.append("  ")
        row.append(
            treemod.human_size(group.size()).rjust(_SIZE_WIDTH),
            style="cyan" if checked else "dim",
        )
        count = group.count()
        row.append(f"   {count} file{'s' if count != 1 else ''}", style="dim")
        rows.append(row)

    rows.append(Text("  " + "-" * (name_width + _SIZE_WIDTH + 6), style="dim"))
    total_row = Text(" " * _NAME_PREFIX)
    total_row.append("Total selected".ljust(name_width), style="bold")
    total_row.append("  ")
    total_row.append(
        treemod.human_size(_restore_selected_bytes(groups)).rjust(_SIZE_WIDTH), style="bold cyan"
    )
    rows.append(total_row)
    return rows


def _select_restore_groups(console: Console, groups: list[RestoreGroup]) -> None:
    """Interactive picker for which restore groups to include."""

    while True:
        console.print()
        for row in build_restore_rows(groups):
            console.print(row)
        _print_legend(console, [
            ("<num>", "toggle that group on / off"),
            ("a / n", "select all / none"),
            ("Enter", "continue"),
        ])
        console.print()
        try:
            raw = input("> ").strip().lower()
        except EOFError:
            return
        if raw == "":
            return
        if raw == "a":
            for group in groups:
                group.included = True
        elif raw == "n":
            for group in groups:
                group.included = False
        elif raw.isdigit():
            number = int(raw)
            if 1 <= number <= len(groups):
                groups[number - 1].included = not groups[number - 1].included
        else:
            console.print(Text("  (unrecognized command)", style="dim"))


def _review_restore(console: Console, groups: list[RestoreGroup]) -> None:
    """Optionally list the files that will be restored (display only)."""

    console.print()
    if not ui.confirm(
        console, "Want to review the files that will be restored?",
        interactive=True, default=False,
    ):
        return
    for group in groups:
        if not group.included:
            continue
        console.print(Text(f"\n{group.name}:", style="bold"))
        shown = group.entries[:_REVIEW_MAX_CHILDREN]
        for entry in shown:
            line = Text("  ")
            line.append(entry.android_path)
            if entry.size is not None:
                line.append(f"   {treemod.human_size(entry.size)}", style="dim")
            console.print(line)
        remaining = group.count() - len(shown)
        if remaining > 0:
            console.print(Text(f"  ... and {remaining} more file(s)", style="cyan"))


_CONFLICT_NOTE: dict[ConflictPolicy | None, str] = {
    None: "skip identical; ask before overwriting a different file",
    ConflictPolicy.SKIP_IDENTICAL: "skip identical; overwrite a different file",
    ConflictPolicy.OVERWRITE_IF_DIFFERENT: "skip identical; overwrite a different file",
    ConflictPolicy.ALWAYS_OVERWRITE: "overwrite every existing file",
    ConflictPolicy.RENAME: "skip identical; restore a different file alongside as '(restored)'",
    ConflictPolicy.ASK: "skip identical; ask before overwriting a different file",
    ConflictPolicy.ABORT: "abort at the first conflicting file",
}


def _confirm_restore(
    console: Console,
    total_bytes: int,
    file_count: int,
    target: DeviceIdentity,
    policy: ConflictPolicy | None,
    interactive: bool,
    assume_yes: bool,
) -> bool:
    where = target.model or target.serial
    console.print(
        f"\nRestore will WRITE {file_count} file(s) (~{treemod.human_size(total_bytes)}) "
        f"onto {where}."
    )
    console.print(f"Conflicts: {_CONFLICT_NOTE.get(policy, 'skip identical; ask about different')}.")
    return ui.confirm(
        console, "Proceed with restore?",
        assume_yes=assume_yes, interactive=interactive, default=False,
    )


_APP_LIST_LIMIT = 20


def _print_app_list(console: Console, heading: str, records: list[AppRecord]) -> None:
    console.print(Text(f"\n  {heading}", style="bold"))
    for record in records[:_APP_LIST_LIMIT]:
        console.print(f"    {record.package}")
    remaining = len(records) - _APP_LIST_LIMIT
    if remaining > 0:
        console.print(Text(f"    ... and {remaining} more", style="cyan"))


def _restore_apps(
    device: DeviceInterface,
    manifest: Manifest,
    backup_root: Path,
    console: Console,
    *,
    interactive: bool,
    assume_yes: bool,
) -> None:
    """Report the recorded apps and offer to install those we hold APKs for."""

    if not manifest.apps:
        return

    grouped = apps.summarize(manifest.apps)
    from_backup = grouped[AppAvailability.BACKUP]
    from_store = grouped[AppAvailability.STORE]
    missing = grouped[AppAvailability.UNAVAILABLE] + grouped[AppAvailability.UNKNOWN]

    console.print(Text(f"\nApps recorded in this backup: {len(manifest.apps)}", style="bold"))
    console.print(f"  {len(from_backup)} installable from the backup")
    console.print(f"  {len(from_store)} to reinstall from the store")
    console.print(f"  {len(missing)} with no recorded source")

    if from_backup and ui.confirm(
        console, f"\nInstall {len(from_backup)} app(s) from the backup now?",
        assume_yes=assume_yes, interactive=interactive, default=False,
    ):
        installed = 0
        failed = 0
        for record in from_backup:
            local = [paths.logical_to_local(backup_root, rel) for rel in record.apk_files]
            present = [path for path in local if path.is_file()]
            if present and device.install_apks(present):
                installed += 1
            else:
                failed += 1
                console.print(Text(f"    failed: {record.package}", style="yellow"))
        console.print(f"  Installed {installed}, failed {failed}.")

    if from_store:
        _print_app_list(console, "Reinstall these from the store:", from_store)
    if missing:
        _print_app_list(console, "No source recorded (track these down manually):", missing)


def _merge_restore(summary: RestoreSummary, part: RestoreSummary) -> None:
    summary.restored.extend(part.restored)
    summary.skipped.extend(part.skipped)
    summary.failed.extend(part.failed)
    summary.conflicts.extend(part.conflicts)


def run_restore(
    device: DeviceInterface,
    *,
    backup_root: Path,
    manifest_path: Path | None,
    policy: ConflictPolicy | None,
    console: Console,
    dry_run: bool = False,
    interactive: bool = False,
    assume_yes: bool = False,
    cancel: CancellationToken | None = None,
) -> int:
    """Restore files described by a manifest back onto the device."""

    path = manifest_path or (backup_root / MANIFEST_FILENAME)
    manifest = load_manifest(path)
    target = device.identity()
    source = manifest.device

    console.print(f"Restoring from {ps.display_path(path)}")
    console.print(
        f"  backed up from {source.model or source.serial} "
        f"(Android {source.android_version or '?'}) on {manifest.created_at}"
    )
    if source.serial and target.serial and source.serial != target.serial:
        console.print(f"  restoring onto a different device: {target.model or target.serial}")

    groups = build_restore_groups(manifest)
    if not groups:
        console.print("Nothing to restore (the manifest has no file entries).")
        return 0

    if interactive:
        _select_restore_groups(console, groups)

    selected = [group for group in groups if group.included]
    file_count = sum(group.count() for group in selected)
    total_bytes = _restore_selected_bytes(groups)
    noun = "group" if len(selected) == 1 else "groups"
    console.print(
        f"\nPlan: restore ~{treemod.human_size(total_bytes)} across {file_count} file(s) "
        f"in {len(selected)} {noun}."
    )

    if not selected or file_count == 0:
        console.print("Nothing selected to restore.")
        return 0

    if interactive:
        _review_restore(console, groups)

    if dry_run:
        console.print("\n(dry-run: no files were written to the device.)")
        if manifest.apps:
            grouped = apps.summarize(manifest.apps)
            console.print(
                f"(dry-run: {len(manifest.apps)} app(s) recorded, "
                f"{len(grouped[AppAvailability.BACKUP])} installable from the backup.)"
            )
        return 0

    if not _confirm_restore(
        console, total_bytes, file_count, target, policy, interactive, assume_yes
    ):
        console.print("Restore not confirmed; no files were written.")
        return 0

    def prompt(remote: str, _identical: bool) -> str:
        console.print(f"Conflict: {remote} differs from the backup.")
        if ui.confirm(console, "Overwrite it?", interactive=interactive, default=False):
            return "write"
        return "skip"

    summary = RestoreSummary()
    for group in selected:
        if cancel is not None and cancel.requested:
            break
        console.print(f"\n{group.name}: restoring {group.count()} file(s) ...")
        only = {entry.local_relative_path for entry in group.entries}

        def progress(done: int, total: int, _name: str = group.name) -> None:
            if done == total or done % _PROGRESS_EVERY == 0:
                console.print(f"    {_name}: {done}/{total} files")

        part = run_restore_files(
            device, manifest, backup_root,
            policy=policy, interactive=interactive, prompt=prompt,
            cancel=cancel, only=only, on_file=progress,
        )
        _merge_restore(summary, part)

    console.print(
        f"\nRestore done: {len(summary.restored)} restored, {len(summary.skipped)} skipped, "
        f"{len(summary.failed)} failed, {len(summary.conflicts)} renamed."
    )

    _restore_apps(
        device, manifest, backup_root, console,
        interactive=interactive, assume_yes=assume_yes,
    )
    return 1 if summary.failed else 0
