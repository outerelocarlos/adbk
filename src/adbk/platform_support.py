"""All operating-system specific behaviour lives here, and nowhere else.

The rest of the codebase imports helpers from this module instead of testing
``sys.platform`` inline. That keeps the planner, manifest, verification, tree
rendering and transfer logic completely platform independent and easy to test.

Nothing in here shells out to ``bash``, ``sh`` or any Unix utility; every path
is built with :class:`pathlib.Path` and every value is derived from portable
Python APIs.
"""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from adbk.errors import UnsupportedPlatformError

OperatingSystem = Literal["windows", "macos", "linux"]
Architecture = Literal["x86_64", "arm64"]

# Environment variables the tool understands. Kept together so documentation and
# code never drift apart.
ENV_TOOLS_DIRECTORY = "ADBK_TOOLS_DIRECTORY"
ENV_ADB_PATH = "ADBK_ADB_PATH"
ENV_ADB_SERVER_HOST = "ADBK_ADB_SERVER_HOST"
ENV_ADB_SERVER_PORT = "ADBK_ADB_SERVER_PORT"
ENV_CONTAINER_MARKER = "ADBK_CONTAINER"

# The application name used when building per-user directories.
APP_DIRNAME = "adbk"

# The hostname Docker Desktop (Windows/macOS) and a configured Linux bridge use
# to reach the host machine. Application logic never hardcodes this; it only
# reads it through :func:`docker_host_gateway`.
DOCKER_HOST_GATEWAY = "host.docker.internal"


# --- Operating system and architecture ---------------------------------------


def current_os() -> OperatingSystem:
    """Return the running operating system as a normalized value.

    Raises :class:`UnsupportedPlatformError` for anything we do not target.
    """

    # Read into a local so mypy's ``sys.platform`` narrowing (which assumes the
    # single platform it is running on) does not mark the other branches as
    # unreachable. This code genuinely runs on all three platforms.
    name = sys.platform
    if name.startswith("win"):
        return "windows"
    if name == "darwin":
        return "macos"
    if name.startswith("linux"):
        return "linux"
    raise UnsupportedPlatformError(f"Unsupported operating system: {name!r}")


def current_arch() -> Architecture:
    """Return the CPU architecture normalized to ``x86_64`` or ``arm64``.

    Raises :class:`UnsupportedPlatformError` for architectures we cannot map.
    """

    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    raise UnsupportedPlatformError(f"Unsupported CPU architecture: {machine!r}")


def executable_name(base: str) -> str:
    """Return the executable file name for the current OS (adds ``.exe`` on Windows)."""

    return f"{base}.exe" if current_os() == "windows" else base


@dataclass(frozen=True)
class PlatformInfo:
    """A small, printable snapshot of the environment for diagnostics."""

    operating_system: OperatingSystem
    architecture: Architecture
    in_container: bool
    python_version: str


def describe_platform() -> PlatformInfo:
    """Collect platform facts used by the ``doctor`` command."""

    return PlatformInfo(
        operating_system=current_os(),
        architecture=current_arch(),
        in_container=in_container(),
        python_version=platform.python_version(),
    )


# --- Container detection ------------------------------------------------------


def in_container() -> bool:
    """Best-effort detection of running inside a container.

    We check an explicit marker first (set by our own image) and fall back to
    the ``/.dockerenv`` file that Docker creates. This never raises.
    """

    marker = os.environ.get(ENV_CONTAINER_MARKER, "").strip().lower()
    if marker in {"1", "true", "yes", "on"}:
        return True
    try:
        return Path("/.dockerenv").exists()
    except OSError:
        return False


def docker_host_gateway() -> str:
    """Return the hostname a container uses to reach the host's ADB server."""

    return DOCKER_HOST_GATEWAY


# --- Per-user directories -----------------------------------------------------


def _home() -> Path:
    return Path.home()


def user_data_dir() -> Path:
    """Per-user data directory (persists across runs), OS-appropriate.

    * Windows: ``%LOCALAPPDATA%\\adbk``
    * macOS:   ``~/Library/Application Support/adbk``
    * Linux:   ``$XDG_DATA_HOME/adbk`` or ``~/.local/share/adbk``
    """

    system = current_os()
    if system == "windows":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else _home() / "AppData" / "Local"
    elif system == "macos":
        root = _home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME")
        root = Path(base) if base else _home() / ".local" / "share"
    return root / APP_DIRNAME


def user_config_dir() -> Path:
    """Per-user configuration directory, OS-appropriate."""

    system = current_os()
    if system == "windows":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else _home() / "AppData" / "Roaming"
    elif system == "macos":
        root = _home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        root = Path(base) if base else _home() / ".config"
    return root / APP_DIRNAME


def managed_tools_dir() -> Path:
    """Directory where the application installs its own managed ADB.

    Overridable with :data:`ENV_TOOLS_DIRECTORY`. This is deliberately inside a
    per-user location so installation never needs administrator/root rights and
    never touches a system-wide Android SDK.
    """

    override = os.environ.get(ENV_TOOLS_DIRECTORY)
    if override:
        return Path(override).expanduser()
    return user_data_dir() / "platform-tools"


def default_config_file() -> Path:
    """Default path of the TOML configuration file."""

    return user_config_dir() / "backup-config.toml"


def default_backup_dir() -> Path:
    """Default destination for backups when the user does not choose one.

    Inside a container we prefer the conventional ``/data/backups`` mount point;
    natively we use a folder in the user's home directory.
    """

    if in_container():
        return Path("/data/backups")
    return _home() / "android-backups"


def android_sdk_adb_candidates() -> list[Path]:
    """Common locations of a user-installed Android SDK ``adb``.

    Used only as a fallback during discovery; we never modify anything found
    here. Locations that do not exist are still returned - the caller checks.
    """

    exe = executable_name("adb")
    candidates: list[Path] = []

    # Respect the standard Android SDK environment variables first.
    for var in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        root = os.environ.get(var)
        if root:
            candidates.append(Path(root) / "platform-tools" / exe)

    system = current_os()
    home = _home()
    if system == "windows":
        local = os.environ.get("LOCALAPPDATA")
        local_root = Path(local) if local else home / "AppData" / "Local"
        candidates.append(local_root / "Android" / "Sdk" / "platform-tools" / exe)
    elif system == "macos":
        candidates.append(home / "Library" / "Android" / "sdk" / "platform-tools" / exe)
    else:
        candidates.append(home / "Android" / "Sdk" / "platform-tools" / exe)
        candidates.append(home / ".local" / "share" / "Android" / "Sdk" / "platform-tools" / exe)

    return candidates


# --- Terminal capabilities ----------------------------------------------------


def stdin_is_interactive() -> bool:
    """True when we can safely prompt the user for input."""

    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (ValueError, OSError):
        return False


def stdout_is_tty() -> bool:
    """True when standard output is a real terminal (enables rich formatting)."""

    try:
        return bool(sys.stdout) and sys.stdout.isatty()
    except (ValueError, OSError):
        return False


def install_interrupt_handler(callback: Callable[[], None]) -> Callable[[], None]:
    """Register a SIGINT (Ctrl+C) handler; return a function that restores the old one.

    SIGINT is raised by Ctrl+C on Windows, macOS and Linux alike, so the caller
    can request graceful cancellation the same way everywhere.
    """

    import signal

    previous = signal.getsignal(signal.SIGINT)

    def handler(_signum: int, _frame: object) -> None:
        callback()

    signal.signal(signal.SIGINT, handler)

    def restore() -> None:
        signal.signal(signal.SIGINT, previous)

    return restore


def cancel_key_hint() -> str:
    """Human-readable hint for the cancellation key on this platform."""

    # Ctrl+C raises KeyboardInterrupt on every supported OS; the label is the
    # same, but keeping it here means the workflow code never special-cases it.
    return "Ctrl+C"


def display_path(path: Path) -> str:
    """Format a path for display using native separators, contracting ``$HOME``."""

    text = str(path)
    try:
        home = str(_home())
    except (RuntimeError, OSError):
        return text
    if text.startswith(home):
        marker = "~" if current_os() != "windows" else "%USERPROFILE%"
        return marker + text[len(home) :]
    return text
