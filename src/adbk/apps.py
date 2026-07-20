"""Inventory of the installed apps, so a restore can bring the apps back too.

Backing up files preserves your *data*; this module preserves the *list of
apps*, and classifies how each one could be reinstalled on a new device:

* ``backup``      - its APK is stored in this backup, so we can install it directly;
* ``store``       - it came from the app store and is expected to still be there;
* ``unavailable`` - neither, so it has to be tracked down by hand;
* ``unknown``     - could not be determined.

Only third-party apps are inventoried. APKs are pulled for the apps that did
*not* come from the store, because those are the irreplaceable ones; store apps
are re-downloadable and would only bloat the backup.

Note that "installed from the store" is not the same as "still on the store": a
delisted app was installed from it but can no longer be downloaded. The optional
store check (a network request, off by default) is what tells those apart;
without it, a package can also be forced into the APK set through configuration.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

from adbk.device import DeviceInterface
from adbk.errors import TransferError
from adbk.manifest import AppRecord
from adbk.models import AppAvailability
from adbk.paths import apk_relative_path, logical_to_local

PLAY_INSTALLER = "com.android.vending"
_NO_INSTALLER = {"", "null", "none"}
_STORE_URL = "https://play.google.com/store/apps/details?id={package}&hl=en"
_STORE_TIMEOUT = 10


def from_store(installer: str) -> bool:
    """Whether the app was installed by the app store."""

    return installer == PLAY_INSTALLER


def store_available(package: str) -> bool | None:
    """Ask the store whether a package still has a listing.

    Returns ``True``/``False``, or ``None`` when the question could not be
    answered (offline, blocked, rate-limited). Never raises: an unreachable
    store must not fail a backup.
    """

    request = urllib.request.Request(
        _STORE_URL.format(package=package),
        headers={"User-Agent": "Mozilla/5.0"},
        method="HEAD",
    )
    try:
        with urllib.request.urlopen(request, timeout=_STORE_TIMEOUT) as response:
            status = int(response.status)
        return 200 <= status < 300
    except urllib.error.HTTPError as exc:
        return False if exc.code == 404 else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def classify(record: AppRecord, store_ok: bool | None) -> AppAvailability:
    """Decide how an app could be reinstalled, best option first."""

    if record.apk_files:
        return AppAvailability.BACKUP  # we hold the APK: always installable
    if store_ok is True:
        return AppAvailability.STORE
    if store_ok is False:
        return AppAvailability.UNAVAILABLE
    if from_store(record.installer):
        return AppAvailability.STORE  # heuristic: it came from the store
    if record.installer.lower() in _NO_INSTALLER:
        return AppAvailability.UNAVAILABLE  # sideloaded and no APK kept
    return AppAvailability.UNKNOWN


def wants_apk(installer: str, package: str, store_ok: bool | None, forced: set[str]) -> bool:
    """Whether this app's APK should be pulled into the backup."""

    if package in forced:
        return True
    if store_ok is False:
        return True  # confirmed gone from the store: keep it while we still can
    return not from_store(installer)


def pull_apks(
    device: DeviceInterface, package: str, backup_root: Path, serial: str
) -> list[str]:
    """Copy a package's APK(s) into the backup, returning their logical paths."""

    stored: list[str] = []
    for remote in device.package_apks(package):
        relative = apk_relative_path(serial, package, remote)
        try:
            device.pull(remote, logical_to_local(backup_root, relative))
        except TransferError:
            continue  # an unreadable APK must not fail the whole backup
        stored.append(relative)
    return stored


def collect(
    device: DeviceInterface,
    *,
    serial: str,
    backup_root: Path | None = None,
    force_packages: Iterable[str] = (),
    check_store: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[AppRecord]:
    """Inventory the installed apps, pulling APKs when ``backup_root`` is given.

    ``backup_root`` of ``None`` means "describe only" (used by dry runs), so
    nothing is written.
    """

    forced = set(force_packages)
    packages = device.list_packages()
    records: list[AppRecord] = []

    for index, package in enumerate(packages, 1):
        details = device.package_details(package)
        record = AppRecord(
            package=package,
            version_name=details.get("versionName", ""),
            version_code=details.get("versionCode", ""),
            installer=details.get("installerPackageName", ""),
        )

        store_ok: bool | None = None
        if check_store:
            store_ok = store_available(package)
            record.store_checked = store_ok is not None

        if backup_root is not None and wants_apk(record.installer, package, store_ok, forced):
            record.apk_files = pull_apks(device, package, backup_root, serial)

        record.availability = classify(record, store_ok)
        records.append(record)
        if on_progress is not None:
            on_progress(index, len(packages))

    return records


def summarize(records: Iterable[AppRecord]) -> dict[AppAvailability, list[AppRecord]]:
    """Group app records by how they can be reinstalled."""

    grouped: dict[AppAvailability, list[AppRecord]] = {state: [] for state in AppAvailability}
    for record in records:
        grouped[record.availability].append(record)
    return grouped
