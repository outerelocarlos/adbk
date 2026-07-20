"""Orchestrates 'resolve ADB, and install it if that is allowed'.

This module contains the *policy* (when may we download?) and keeps it away from
the *mechanism* (:mod:`adbk.adb_installer`) and plain execution
(:mod:`adbk.adb`). The rules encoded here are:

* interactive: explain what will happen, then ask for confirmation;
* non-interactive: only install with ``--install-adb`` / ``--yes``;
* dry-run: report that installation would be needed, change nothing;
* containers: prefer the bundled adb and do not install unless asked explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from adbk import adb, adb_installer, ui
from adbk import platform_support as ps
from adbk.adb import AdbClient, AdbResolution
from adbk.errors import AdbNotFoundError


@dataclass
class AdbOptions:
    """Command-line/config inputs that affect ADB resolution and installation."""

    explicit_path: Path | None = None
    server_host: str | None = None
    server_port: int | None = None
    install: bool = False  # --install-adb
    no_install: bool = False  # --no-install-adb
    assume_yes: bool = False  # --yes / -y
    dry_run: bool = False
    verbose: bool = False


def _installer_events(console: Console, verbose: bool) -> adb_installer.EventCallback:
    def on_event(stage: str, detail: str) -> None:
        if verbose:
            console.print(f"  [{stage}] {detail}")
        else:
            console.print(detail)

    return on_event


def _explain_install(console: Console, tools_dir: Path) -> None:
    console.print("ADB (Android Debug Bridge) is required but was not found.")
    console.print("The official Android SDK platform-tools will be downloaded from Google:")
    console.print("  source:  https://dl.google.com/android/repository/")
    console.print(f"  install: {ps.display_path(tools_dir)}")
    console.print("No administrator rights are needed and your PATH is not modified.")


def resolve_or_install_adb(
    options: AdbOptions,
    console: Console,
    *,
    interactive: bool,
    force_install: bool = False,
) -> AdbResolution | None:
    """Return a resolved ADB, installing it when policy allows.

    Returns ``None`` only in dry-run mode when ADB is missing (so the caller can
    report without failing). Raises :class:`AdbNotFoundError` when ADB is needed
    but may not be installed.
    """

    resolved = adb.resolve_adb(options.explicit_path)
    if resolved is not None:
        return resolved

    tools_dir = ps.managed_tools_dir()

    if options.dry_run and not force_install:
        console.print(
            f"ADB was not found. Installing it into {ps.display_path(tools_dir)} "
            "would be required, and a dry run changes nothing. "
            "Run 'adbk setup-adb' first, then try this again."
        )
        return None

    if options.no_install and not force_install:
        raise AdbNotFoundError(
            "No usable adb was found and automatic installation is disabled "
            "(--no-install-adb). Install Android platform-tools manually, pass "
            "--adb-path, or set ADBK_ADB_PATH."
        )

    if ps.in_container() and not (options.install or force_install):
        raise AdbNotFoundError(
            "No adb found inside the container. Use the image's bundled adb, "
            "connect to a host ADB server with --adb-server-host, or pass "
            "--install-adb to install into the container explicitly."
        )

    permitted = force_install or options.install or options.assume_yes
    if not permitted:
        if interactive:
            _explain_install(console, tools_dir)
            permitted = ui.confirm(
                console,
                "Download and install ADB now?",
                interactive=True,
                default=False,
            )
        else:
            raise AdbNotFoundError(
                "No usable adb was found. Re-run with --install-adb (or --yes) to "
                "install the official platform-tools, or run 'setup-adb'."
            )

    if not permitted:
        raise AdbNotFoundError("ADB is required to continue, but installation was declined.")

    result = adb_installer.install_adb(on_event=_installer_events(console, options.verbose))
    console.print(f"Installed adb {result.version} at {ps.display_path(result.adb_path)}")
    return AdbResolution(result.adb_path, "managed")


def ensure_adb_client(
    options: AdbOptions,
    console: Console,
    *,
    interactive: bool,
) -> AdbClient:
    """Resolve (installing if allowed) and return a ready-to-use client."""

    resolved = resolve_or_install_adb(options, console, interactive=interactive)
    if resolved is None:
        raise AdbNotFoundError(
            "No usable adb is available. Run 'adbk setup-adb' to install the "
            "official platform-tools, or pass --adb-path to point at your own."
        )
    return AdbClient(resolved.path, options.server_host, options.server_port)


# --- Explicit commands --------------------------------------------------------


def run_setup_adb(options: AdbOptions, console: Console, *, interactive: bool) -> int:
    """Implement the ``setup-adb`` command (explicit install/reinstall)."""

    tools_dir = ps.managed_tools_dir()

    if options.dry_run:
        console.print(
            f"[dry-run] Would download the official platform-tools and install into "
            f"{ps.display_path(tools_dir)}. Nothing was changed."
        )
        return 0

    existing = adb.resolve_adb(options.explicit_path)
    if existing is not None and existing.kind not in {"managed"}:
        console.print(
            f"Note: an existing adb was found ({existing.label}) at "
            f"{ps.display_path(existing.path)}. It will not be modified; a private "
            "managed copy will be installed alongside it."
        )
    if adb_installer.is_managed_installed():
        metadata = adb_installer.read_metadata()
        version = metadata.version if metadata else "unknown"
        console.print(f"A managed adb (version {version}) is already installed; reinstalling.")

    result = adb_installer.install_adb(on_event=_installer_events(console, options.verbose))
    console.print(f"Installed adb {result.version} at {ps.display_path(result.adb_path)}")
    return 0


def run_update_adb(options: AdbOptions, console: Console, *, interactive: bool) -> int:
    """Implement the ``update-adb`` command (never updates silently)."""

    status = adb_installer.check_update()

    if not status.has_managed:
        console.print(
            "No application-managed adb is installed, so there is nothing to update. "
            "Run 'setup-adb' to install one."
        )
        return 0

    if not status.update_available:
        console.print(f"Managed adb is up to date (version {status.installed_version}).")
        return 0

    console.print(
        f"A newer adb is available: {status.installed_version} -> {status.latest_version}."
    )
    if options.dry_run:
        console.print("[dry-run] Not updating.")
        return 0

    permitted = options.install or options.assume_yes
    if not permitted and interactive:
        permitted = ui.confirm(console, "Update the managed adb now?", interactive=True)
    if not permitted:
        console.print("Update not confirmed; leaving the current version in place.")
        return 0

    result = adb_installer.install_adb(on_event=_installer_events(console, options.verbose))
    console.print(f"Updated managed adb to version {result.version}.")
    return 0
