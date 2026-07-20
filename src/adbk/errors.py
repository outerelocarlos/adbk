"""Exception hierarchy for adbk.

Every error the application raises on purpose inherits from
:class:`AndroidBackupError`. The command-line layer catches that base class and
prints a concise message, so lower-level code can raise a specific subclass
without worrying about how it will be displayed.
"""

from __future__ import annotations


class AndroidBackupError(Exception):
    """Base class for all expected, user-facing errors.

    ``hint`` carries optional multi-line guidance printed under the message, so
    an error can spell out what to do next instead of cramming every detail
    into one long sentence.
    """

    def __init__(self, *args: object, hint: str = "") -> None:
        super().__init__(*args)
        self.hint = hint


# --- ADB discovery / execution ------------------------------------------------


class AdbError(AndroidBackupError):
    """A problem locating or running the ``adb`` executable."""


class AdbNotFoundError(AdbError):
    """No usable ``adb`` executable could be resolved."""


# --- ADB installation ---------------------------------------------------------


class AdbInstallError(AndroidBackupError):
    """A problem while downloading or installing a managed ``adb``."""


class DownloadError(AdbInstallError):
    """The platform-tools archive could not be downloaded."""


class ChecksumError(AdbInstallError):
    """A downloaded archive did not match its expected checksum."""


class UnsafeArchiveError(AdbInstallError):
    """An archive tried to write outside its extraction directory."""


# --- Platform / configuration -------------------------------------------------


class UnsupportedPlatformError(AndroidBackupError):
    """The current operating system or CPU architecture is not supported."""


class ConfigError(AndroidBackupError):
    """The configuration file could not be read or is invalid."""


# --- Device / transfer --------------------------------------------------------


class DeviceError(AndroidBackupError):
    """A problem talking to the connected Android device."""


class DeviceAccessError(DeviceError):
    """A path exists but could not be listed/read (distinct from 'missing')."""


class TransferError(AndroidBackupError):
    """A file could not be pulled, pushed, verified or deleted."""


class OperationCancelled(AndroidBackupError):
    """The user requested cancellation (Ctrl+C)."""
