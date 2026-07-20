"""Command-line interface for adbk.

Global options are shared between the top level and every subcommand (via a
common parent parser) so ``adbk --dry-run doctor`` and
``adbk doctor --dry-run`` both work.

This build wires up the ADB-focused commands (``doctor``, ``setup-adb``,
``update-adb``); the interactive backup/restore workflow is added on top of this
foundation in later commits.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from rich.console import Console

from adbk import __version__, filters, ui
from adbk import platform_support as ps
from adbk.adb_setup import AdbOptions, ensure_adb_client, run_setup_adb, run_update_adb
from adbk.config import AppConfig, effective_categories, load_config
from adbk.device import AdbDevice, DeviceInterface
from adbk.doctor import gather, render
from adbk.errors import AndroidBackupError, DeviceError
from adbk.models import ConflictPolicy, TransferMode
from adbk.transfer import CancellationToken
from adbk.workflow import find_latest_backup, next_backup_dir, run_backup, run_restore

_CONFLICT_CHOICES: dict[str, ConflictPolicy] = {
    "skip-identical": ConflictPolicy.SKIP_IDENTICAL,
    "overwrite": ConflictPolicy.ALWAYS_OVERWRITE,
    "overwrite-different": ConflictPolicy.OVERWRITE_IF_DIFFERENT,
    "rename": ConflictPolicy.RENAME,
    "ask": ConflictPolicy.ASK,
    "abort": ConflictPolicy.ABORT,
}


def _common_options() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--adb-path", type=Path, metavar="PATH",
                        help="Path to a specific adb executable to use.")
    common.add_argument("--adb-server-host", metavar="HOST",
                        help="Hostname of an ADB server to connect to (e.g. host.docker.internal).")
    common.add_argument("--adb-server-port", type=int, metavar="PORT",
                        help="Port of the ADB server to connect to (default 5037).")
    common.add_argument("--install-adb", action="store_true",
                        help="Permit downloading and installing the official ADB if missing.")
    common.add_argument("--no-install-adb", action="store_true",
                        help="Never install ADB automatically.")
    common.add_argument("-y", "--yes", action="store_true",
                        help="Assume 'yes' to confirmation prompts (non-interactive).")
    common.add_argument("--dry-run", action="store_true",
                        help="Report what would happen without downloading or changing anything.")
    common.add_argument("--config", type=Path, metavar="PATH",
                        help="Path to a backup-config.toml file.")
    common.add_argument("--backup-dir", type=Path, metavar="PATH",
                        help="Destination directory for backups (and source for restore).")
    common.add_argument("--check-network", action="store_true",
                        help="Allow the doctor command to probe the download source and updates.")
    common.add_argument("--plain", action="store_true",
                        help="Force plain output with no colour.")
    common.add_argument("-v", "--verbose", action="store_true",
                        help="Show detailed progress and stages.")
    common.add_argument("--doctor", action="store_true",
                        help="Run diagnostics (same as the 'doctor' command).")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_options()
    parser = argparse.ArgumentParser(
        prog="adbk",
        description="Back up an Android phone over ADB before migrating devices.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"adbk {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", parents=[common], help="Show an environment health check.")
    subparsers.add_parser("setup-adb", parents=[common], help="Download and install a managed ADB.")
    subparsers.add_parser("update-adb", parents=[common], help="Check for and install a newer managed ADB.")

    backup = subparsers.add_parser("backup", parents=[common], help="Back up the connected device.")
    mode_group = backup.add_mutually_exclusive_group()
    mode_group.add_argument("--safe-move", action="store_true",
                            help="Verified safe move (default): delete each source only after verification.")
    mode_group.add_argument("--copy", action="store_true",
                            help="Copy only; never delete anything from the device.")
    backup.add_argument("--resume", action="store_true",
                        help="Resume an interrupted backup, re-verifying prior entries.")
    store_group = backup.add_mutually_exclusive_group()
    store_group.add_argument("--check-store", action="store_true",
                             help="Ask the app store which installed apps are still listed, "
                                  "so delisted apps keep their APK (asked interactively).")
    store_group.add_argument("--no-check-store", action="store_true",
                             help="Never query the app store while inventorying apps.")

    restore = subparsers.add_parser("restore", parents=[common], help="Restore files onto the device.")
    restore.add_argument("--manifest", type=Path, metavar="PATH",
                         help="Manifest to restore from (default: <backup-dir>/manifest.json).")
    restore.add_argument("--conflict", choices=sorted(_CONFLICT_CHOICES), metavar="POLICY",
                         help="Conflict policy: " + ", ".join(sorted(_CONFLICT_CHOICES)) +
                              " (default: skip identical, ask about different).")
    return parser


def _first(*values: object) -> object | None:
    for value in values:
        if value:
            return value
    return None


def _build_options(args: argparse.Namespace, config: AppConfig) -> AdbOptions:
    explicit_path = args.adb_path or config.adb_path

    server_host = _first(
        args.adb_server_host,
        os.environ.get(ps.ENV_ADB_SERVER_HOST),
        config.adb_server_host,
    )
    server_port_raw = _first(
        args.adb_server_port,
        os.environ.get(ps.ENV_ADB_SERVER_PORT),
        config.adb_server_port,
    )
    server_port = int(str(server_port_raw)) if server_port_raw is not None else None

    install = bool(args.install_adb) or config.auto_install_adb is True
    no_install = bool(args.no_install_adb) or config.auto_install_adb is False

    return AdbOptions(
        explicit_path=Path(explicit_path) if explicit_path else None,
        server_host=str(server_host) if server_host else None,
        server_port=server_port,
        install=install,
        no_install=no_install,
        assume_yes=bool(args.yes),
        dry_run=bool(args.dry_run),
        verbose=bool(args.verbose),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.install_adb and args.no_install_adb:
        parser.error("--install-adb and --no-install-adb cannot be used together.")

    console = ui.make_console(plain=bool(args.plain))
    interactive = ps.stdin_is_interactive()

    config_path = args.config or ps.default_config_file()
    if not config_path.is_file():
        config_path = None if args.config is None else args.config

    try:
        config = load_config(config_path)
        filters.configure(config.ignore_dirs, config.ignore_files)
        options = _build_options(args, config)

        command = args.command
        if args.doctor and command is None:
            command = "doctor"

        if command == "doctor":
            report = gather(options, config, config_path, check_network=bool(args.check_network))
            render(console, report)
            return 0
        if command == "setup-adb":
            return run_setup_adb(options, console, interactive=interactive)
        if command == "update-adb":
            return run_update_adb(options, console, interactive=interactive)

        # No subcommand: --install-adb is a shortcut for setup-adb; otherwise the
        # default operation is backup (or restore if a manifest suggests it).
        if command is None and args.install_adb:
            return run_setup_adb(options, console, interactive=interactive)
        if command is None:
            command = _default_operation(args, config, console, interactive)

        if command == "backup":
            return _run_backup(args, options, config, console, interactive)
        if command == "restore":
            return _run_restore(args, options, config, console, interactive)

        parser.print_help()
        return 0
    except KeyboardInterrupt:
        console.print(f"\nCancelled ({ps.cancel_key_hint()}).")
        return 130
    except AndroidBackupError as exc:
        console.print(f"Error: {exc}")
        return 1


def _resolve_backup_dir(args: argparse.Namespace, config: AppConfig) -> Path:
    if args.backup_dir is not None:
        return Path(args.backup_dir)
    if config.backup_dir is not None:
        return config.backup_dir
    return ps.default_backup_dir()


def _default_operation(
    args: argparse.Namespace, config: AppConfig, console: Console, interactive: bool
) -> str:
    """Backup by default; suggest restore when a backup is already present."""

    latest = find_latest_backup(_resolve_backup_dir(args, config))
    # The inferred default is always shown and changeable.
    if latest is not None and interactive and ui.confirm(
        console, f"A backup exists at {ps.display_path(latest)}. "
        "Restore instead of backing up?", interactive=True, default=False,
    ):
        return "restore"
    return "backup"


def _open_device(options: AdbOptions, console: Console, interactive: bool) -> DeviceInterface:
    client = ensure_adb_client(options, console, interactive=interactive)
    ready = [d for d in client.devices() if d.is_ready]
    if not ready:
        raise DeviceError(
            "No ready Android device found. Connect the phone, enable USB debugging, "
            "and accept the 'Allow USB debugging' prompt (run 'doctor' to check)."
        )
    if len(ready) > 1:
        console.print(f"Multiple devices connected; using {ready[0].serial}.")
    return AdbDevice(client, ready[0].serial)


def _resolve_check_store(args: argparse.Namespace) -> bool | None:
    """True/False when forced by a flag, None to ask (defaulting to yes)."""

    if getattr(args, "check_store", False):
        return True
    if getattr(args, "no_check_store", False):
        return False
    return None


def _resolve_mode(args: argparse.Namespace) -> TransferMode:
    if getattr(args, "copy", False):
        return TransferMode.COPY
    return TransferMode.SAFE_MOVE


def _run_backup(
    args: argparse.Namespace,
    options: AdbOptions,
    config: AppConfig,
    console: Console,
    interactive: bool,
) -> int:
    root = _resolve_backup_dir(args, config)
    mode = _resolve_mode(args)
    resume = bool(getattr(args, "resume", False))
    device = _open_device(options, console, interactive)

    if resume:
        backup_dir = find_latest_backup(root) or next_backup_dir(root)
    else:
        backup_dir = next_backup_dir(root)
    console.print(f"Backup directory: {ps.display_path(backup_dir)}")

    cancel = CancellationToken()
    uninstall = ps.install_interrupt_handler(cancel.request)
    try:
        outcome = run_backup(
            device,
            backup_root=backup_dir,
            categories=list(effective_categories(config)),
            mode=mode,
            console=console,
            dry_run=options.dry_run,
            interactive=interactive,
            assume_yes=options.assume_yes,
            cancel=cancel,
            resume=resume,
            config_snapshot={"mode": str(mode)},
            force_apk=config.force_apk,
            check_store=_resolve_check_store(args),
        )
    finally:
        uninstall()
    return 1 if outcome.failed else 0


def _run_restore(
    args: argparse.Namespace,
    options: AdbOptions,
    config: AppConfig,
    console: Console,
    interactive: bool,
) -> int:
    root = _resolve_backup_dir(args, config)
    policy = _CONFLICT_CHOICES.get(getattr(args, "conflict", None) or "")
    manifest_arg = getattr(args, "manifest", None)
    manifest_path: Path | None
    if manifest_arg is not None:
        manifest_path = Path(manifest_arg)
        backup_dir = manifest_path.parent
    else:
        found = find_latest_backup(root)
        if found is None:
            raise AndroidBackupError(
                f"No backup found under {ps.display_path(root)}. "
                "Pass --backup-dir or --manifest to point at one."
            )
        backup_dir = found
        manifest_path = None
    device = _open_device(options, console, interactive)

    cancel = CancellationToken()
    uninstall = ps.install_interrupt_handler(cancel.request)
    try:
        return run_restore(
            device,
            backup_root=backup_dir,
            manifest_path=manifest_path,
            policy=policy,
            console=console,
            dry_run=options.dry_run,
            interactive=interactive,
            assume_yes=options.assume_yes,
            cancel=cancel,
        )
    finally:
        uninstall()
