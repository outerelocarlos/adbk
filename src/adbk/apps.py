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

# Installer package names that mean "re-downloadable from an app store", so the
# APK is not worth keeping. More than just Play: phones ship other real stores.
_KNOWN_STORES = frozenset({
    PLAY_INSTALLER,                          # Google Play
    "com.sec.android.app.samsungapps",       # Samsung Galaxy Store
    "com.samsung.android.app.updatecenter",  # Samsung's own app updater
    "com.amazon.venezia",                    # Amazon Appstore
    "com.huawei.appmarket",                  # Huawei AppGallery
    "com.xiaomi.market", "com.xiaomi.mipicks",  # Xiaomi GetApps
    "com.heytap.market",                     # Oppo / realme
    "com.bbk.appstore",                      # Vivo
    "com.qihoo.appstore",
    "com.facebook.system",                   # Meta app manager (Instagram, ...)
})

# The system component used when you tap an APK file: a genuine sideload, so the
# APK is the only way back and is worth keeping.
_SYSTEM_INSTALLERS = frozenset({
    "com.android.packageinstaller",
    "com.google.android.packageinstaller",
})

_NO_INSTALLER = frozenset({"", "null", "none"})
_STORE_URL = "https://play.google.com/store/apps/details?id={package}&hl=en"
_STORE_TIMEOUT = 5
# Give up on the store check after this many unanswered lookups in a row, so a
# blocked or blackholed network cannot stretch a backup by one timeout per app.
_STORE_FAILURE_LIMIT = 5


def from_store(installer: str) -> bool:
    """Whether the app was installed by a recognized app store."""

    return installer in _KNOWN_STORES


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


def wants_apk(
    installer: str,
    package: str,
    store_ok: bool | None,
    forced: set[str],
    *,
    installed: frozenset[str] = frozenset(),
) -> bool:
    """Whether this app's APK should be pulled into the backup.

    Kept for apps we could not otherwise get back: sideloaded from a loose APK,
    or with no recorded origin. Skipped for apps a store -- or another installed
    app, such as an extension manager -- can reinstall.
    """

    if package in forced:
        return True
    if store_ok is False:
        return True  # the store confirms it is gone: keep it while we can
    if store_ok is True:
        return False
    if installer in _KNOWN_STORES:
        return False
    if installer in _NO_INSTALLER or installer in _SYSTEM_INSTALLERS:
        return True  # sideloaded from an APK file, or origin unknown
    # Installed by another app here (which can reinstall it) -> skip; an
    # unrecognized installer we keep, to be safe.
    return installer not in installed


def apks_to_back_up(installers: dict[str, str], forced: Iterable[str]) -> list[str]:
    """Packages whose APK a backup would keep, from installer info alone.

    This is the store-independent set (sideloaded apps and forced packages); the
    optional store check adds delisted store apps on top during the backup.
    """

    forced_set = set(forced)
    installed = frozenset(installers)
    return sorted(
        package
        for package, installer in installers.items()
        if wants_apk(installer, package, None, forced_set, installed=installed)
    )


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
    installed = frozenset(packages)
    records: list[AppRecord] = []
    store_enabled = check_store
    unanswered = 0

    for index, package in enumerate(packages, 1):
        details = device.package_details(package)
        record = AppRecord(
            package=package,
            version_name=details.get("versionName", ""),
            version_code=details.get("versionCode", ""),
            installer=details.get("installerPackageName", ""),
        )

        store_ok: bool | None = None
        if store_enabled:
            store_ok = store_available(package)
            record.store_checked = store_ok is not None
            if store_ok is None:
                unanswered += 1
                if unanswered >= _STORE_FAILURE_LIMIT:
                    store_enabled = False  # network unusable; stop asking
            else:
                unanswered = 0

        if backup_root is not None and wants_apk(
            record.installer, package, store_ok, forced, installed=installed
        ):
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
