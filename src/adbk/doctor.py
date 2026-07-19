"""The ``doctor`` command: a read-only health check of the environment.

It never changes anything and never prints secrets. Network checks (source
reachability, update availability) are opt-in via ``check_network`` so an
ordinary ``doctor`` run stays fast and offline-friendly.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from adbk import adb, adb_installer, adb_sources
from adbk import platform_support as ps
from adbk.adb_setup import AdbOptions
from adbk.config import AppConfig
from adbk.errors import AndroidBackupError

DEFAULT_ADB_SERVER_HOST = "127.0.0.1"
DEFAULT_ADB_SERVER_PORT = 5037


@dataclass
class Diagnostic:
    label: str
    value: str
    ok: bool | None = None  # True=good, False=problem, None=informational


@dataclass
class DoctorReport:
    sections: list[tuple[str, list[Diagnostic]]] = field(default_factory=list)
    guidance: list[str] = field(default_factory=list)


def _nearest_existing(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            break
        current = parent
    return current


def _free_space_text(path: Path) -> str:
    try:
        usage = shutil.disk_usage(_nearest_existing(path))
    except OSError:
        return "unknown"
    gib = usage.free / (1024**3)
    return f"{gib:.1f} GiB free"


def _is_writable(path: Path) -> bool:
    target = _nearest_existing(path)
    if not target.is_dir():
        return False
    try:
        with tempfile.TemporaryFile(dir=target):
            return True
    except OSError:
        return False


def gather(
    options: AdbOptions,
    config: AppConfig,
    config_path: Path | None,
    *,
    check_network: bool = False,
) -> DoctorReport:
    """Collect all diagnostics into a structured, printable report."""

    report = DoctorReport()

    # --- Environment ----------------------------------------------------------
    platform_info = ps.describe_platform()
    report.sections.append(
        (
            "Environment",
            [
                Diagnostic("Operating system", platform_info.operating_system),
                Diagnostic("Architecture", platform_info.architecture),
                Diagnostic(
                    "Execution",
                    "container" if platform_info.in_container else "native",
                ),
                Diagnostic("Python", platform_info.python_version),
            ],
        )
    )

    # --- ADB ------------------------------------------------------------------
    adb_items: list[Diagnostic] = []
    resolution: adb.AdbResolution | None
    try:
        resolution = adb.resolve_adb(options.explicit_path)
    except AndroidBackupError as exc:
        resolution = None
        adb_items.append(Diagnostic("ADB resolution", str(exc), ok=False))

    server_host = options.server_host or DEFAULT_ADB_SERVER_HOST
    server_port = options.server_port or DEFAULT_ADB_SERVER_PORT

    if resolution is not None:
        adb_items.append(Diagnostic("Resolved via", resolution.label, ok=True))
        adb_items.append(Diagnostic("Executable", ps.display_path(resolution.path)))
        client = adb.AdbClient(resolution.path, options.server_host, options.server_port)
        try:
            adb_items.append(Diagnostic("Version", client.version(), ok=True))
        except AndroidBackupError as exc:
            adb_items.append(Diagnostic("Version", str(exc), ok=False))
        adb_items.append(Diagnostic("Server host", server_host))
        adb_items.append(Diagnostic("Server port", str(server_port)))
        try:
            devices = client.devices()
            adb_items.append(Diagnostic("ADB server", "reachable", ok=True))
            if devices:
                for device in devices:
                    adb_items.append(
                        Diagnostic(
                            f"Device {device.serial}",
                            device.state,
                            ok=device.is_ready,
                        )
                    )
            else:
                adb_items.append(Diagnostic("Devices", "none connected", ok=None))
        except AndroidBackupError as exc:
            adb_items.append(Diagnostic("ADB server", f"unreachable: {exc}", ok=False))
    else:
        adb_items.append(Diagnostic("ADB", "not found", ok=False))
        adb_items.append(Diagnostic("Server host", server_host))
        adb_items.append(Diagnostic("Server port", str(server_port)))

    adb_items.append(Diagnostic("Installation needed", "no" if resolution else "yes"))
    report.sections.append(("ADB", adb_items))

    # --- Managed installation -------------------------------------------------
    tools_dir = ps.managed_tools_dir()
    managed_items = [
        Diagnostic("Managed directory", ps.display_path(tools_dir)),
        Diagnostic("Managed installed", "yes" if adb_installer.is_managed_installed() else "no"),
        Diagnostic("Directory writable", "yes" if _is_writable(tools_dir) else "no",
                   ok=_is_writable(tools_dir)),
    ]
    metadata = adb_installer.read_metadata()
    if metadata is not None:
        managed_items.append(Diagnostic("Managed version", metadata.version))
    if adb_installer.has_incomplete_install():
        managed_items.append(
            Diagnostic("Incomplete install", "yes - run setup-adb to repair", ok=False)
        )
    if check_network:
        managed_items.extend(_network_diagnostics())
    else:
        managed_items.append(
            Diagnostic("Download source", "not checked (pass --check-network)")
        )
    report.sections.append(("Managed ADB", managed_items))

    # --- Storage --------------------------------------------------------------
    backup_dir = config.backup_dir or ps.default_backup_dir()
    report.sections.append(
        (
            "Storage",
            [
                Diagnostic("Backup directory", ps.display_path(backup_dir)),
                Diagnostic("Writable", "yes" if _is_writable(backup_dir) else "no",
                           ok=_is_writable(backup_dir)),
                Diagnostic("Free space", _free_space_text(backup_dir)),
            ],
        )
    )

    # --- Configuration --------------------------------------------------------
    config_items: list[Diagnostic] = []
    if config_path is None:
        config_items.append(Diagnostic("Config file", "none (using defaults)"))
    else:
        readable = config_path.is_file()
        config_items.append(
            Diagnostic("Config file", ps.display_path(config_path), ok=readable if readable else None)
        )
        config_items.append(Diagnostic("Readable", "yes" if readable else "no (will use defaults)"))
    report.sections.append(("Configuration", config_items))

    # --- Docker guidance ------------------------------------------------------
    report.guidance.extend(_docker_guidance(platform_info.in_container, options))

    return report


def _network_diagnostics() -> list[Diagnostic]:
    items: list[Diagnostic] = []
    try:
        os_name = ps.current_os()
        arch = ps.current_arch()
        source = adb_sources.resolve_source(os_name, arch, adb_installer.default_fetch_bytes)
        items.append(Diagnostic("Download source", "reachable", ok=True))
        items.append(Diagnostic("Latest version", source.version))
        if adb_installer.is_managed_installed():
            metadata = adb_installer.read_metadata()
            if metadata is not None and metadata.version != source.version:
                items.append(
                    Diagnostic(
                        "Update available",
                        f"{metadata.version} -> {source.version}",
                        ok=None,
                    )
                )
            else:
                items.append(Diagnostic("Update available", "no", ok=True))
    except AndroidBackupError as exc:
        items.append(Diagnostic("Download source", f"unreachable: {exc}", ok=False))
    return items


def _docker_guidance(in_container: bool, options: AdbOptions) -> list[str]:
    if in_container:
        if not options.server_host:
            return [
                "Running in a container without --adb-server-host.",
                "On Docker Desktop (Windows/macOS), connect to the host's ADB server:",
                "  --adb-server-host host.docker.internal --adb-server-port 5037",
                "and start the host server with:  adb -a -P 5037 nodaemon server",
            ]
        return [
            f"Container will use the ADB server at {options.server_host}:"
            f"{options.server_port or DEFAULT_ADB_SERVER_PORT}.",
        ]
    return []


def render(console: Console, report: DoctorReport) -> None:
    """Print the report as aligned, plain-friendly text."""

    for title, items in report.sections:
        console.print(f"\n{title}")
        console.print("-" * len(title))
        width = max((len(item.label) for item in items), default=0)
        for item in items:
            marker = {True: "ok  ", False: "!!  ", None: "    "}[item.ok]
            console.print(f"  {marker}{item.label.ljust(width)}  {item.value}")

    if report.guidance:
        console.print("\nDocker guidance")
        console.print("---------------")
        for line in report.guidance:
            console.print(f"  {line}")
