"""Tests for the installed-app inventory and how each app can be reinstalled."""

from __future__ import annotations

from pathlib import Path

from adbk import apps
from adbk.device import parse_package_details, parse_package_list, parse_package_paths
from adbk.manifest import AppRecord, Manifest, load_manifest
from adbk.models import AppAvailability, DeviceIdentity, Operation, TransferMode
from adbk.paths import logical_to_local
from tests.fakedevice import FakeDevice

# --- Parsing ------------------------------------------------------------------


def test_parse_package_list_sorts_and_dedupes() -> None:
    output = "package:com.b\npackage:com.a\npackage:com.a\n"
    assert parse_package_list(output) == ["com.a", "com.b"]


def test_parse_package_paths_keeps_splits_in_order() -> None:
    output = "package:/data/app/x/base.apk\npackage:/data/app/x/split_config.arm64.apk\n"
    assert parse_package_paths(output) == [
        "/data/app/x/base.apk",
        "/data/app/x/split_config.arm64.apk",
    ]


def test_parse_package_details_takes_the_first_token() -> None:
    output = (
        "    versionName=1.7.0\n"
        "    versionCode=170 minSdk=23 targetSdk=33\n"
        "    installerPackageName=com.android.vending\n"
    )
    details = parse_package_details(output)
    assert details["versionName"] == "1.7.0"
    assert details["versionCode"] == "170"  # trailing fields dropped
    assert details["installerPackageName"] == "com.android.vending"


# --- Classification -----------------------------------------------------------


def _record(installer: str = "", apk_files: list[str] | None = None) -> AppRecord:
    return AppRecord(package="com.x", installer=installer, apk_files=apk_files or [])


def test_classify_prefers_the_backup() -> None:
    record = _record(installer=apps.PLAY_INSTALLER, apk_files=["a"])
    assert apps.classify(record, None) is AppAvailability.BACKUP


def test_classify_uses_the_installer_as_a_heuristic() -> None:
    assert apps.classify(_record(installer=apps.PLAY_INSTALLER), None) is AppAvailability.STORE


def test_classify_store_check_beats_the_installer() -> None:
    # A delisted app: installed from the store, but no longer listed there.
    assert apps.classify(_record(installer=apps.PLAY_INSTALLER), False) is (
        AppAvailability.UNAVAILABLE
    )


def test_classify_sideloaded_without_apk_is_unavailable() -> None:
    assert apps.classify(_record(installer="null"), None) is AppAvailability.UNAVAILABLE


def test_classify_other_installer_is_unknown() -> None:
    assert apps.classify(_record(installer="org.fdroid.fdroid"), None) is AppAvailability.UNKNOWN


def test_wants_apk_rules() -> None:
    assert apps.wants_apk("null", "com.x", None, set())  # sideloaded
    assert not apps.wants_apk(apps.PLAY_INSTALLER, "com.x", None, set())  # re-downloadable
    assert apps.wants_apk(apps.PLAY_INSTALLER, "com.x", False, set())  # delisted
    assert apps.wants_apk(apps.PLAY_INSTALLER, "com.x", None, {"com.x"})  # forced


# --- Collection ---------------------------------------------------------------


def test_collect_pulls_sideloaded_apks_only(tmp_path: Path) -> None:
    device = FakeDevice(serial="S")
    device.add_package("com.store.app", installer=apps.PLAY_INSTALLER)
    device.add_package(
        "com.side.app",
        installer="null",
        apks=("/data/app/side/base.apk", "/data/app/side/split_config.arm64.apk"),
    )

    records = {r.package: r for r in apps.collect(device, serial="S", backup_root=tmp_path)}

    store_app = records["com.store.app"]
    assert store_app.availability is AppAvailability.STORE
    assert store_app.apk_files == []  # re-downloadable, so nothing kept

    side_app = records["com.side.app"]
    assert side_app.availability is AppAvailability.BACKUP
    assert len(side_app.apk_files) == 2  # both splits kept
    for relative in side_app.apk_files:
        assert logical_to_local(tmp_path, relative).is_file()


def test_collect_keeps_a_forced_packages_apk(tmp_path: Path) -> None:
    device = FakeDevice(serial="S")
    device.add_package("com.delisted", installer=apps.PLAY_INSTALLER)

    records = apps.collect(
        device, serial="S", backup_root=tmp_path, force_packages=["com.delisted"]
    )
    assert records[0].availability is AppAvailability.BACKUP
    assert records[0].apk_files


def test_collect_without_a_backup_root_pulls_nothing() -> None:
    device = FakeDevice(serial="S")
    device.add_package("com.side", installer="null")

    records = apps.collect(device, serial="S", backup_root=None)
    assert records[0].apk_files == []
    assert records[0].availability is AppAvailability.UNAVAILABLE


# --- Manifest round trip ------------------------------------------------------


def test_manifest_round_trip_keeps_apps(tmp_path: Path) -> None:
    manifest = Manifest(Operation.BACKUP, TransferMode.COPY, DeviceIdentity("S"))
    manifest.apps = [
        AppRecord(
            package="com.x",
            installer="null",
            apk_files=["devices/S/apks/com.x/base.apk"],
            availability=AppAvailability.BACKUP,
        )
    ]
    path = tmp_path / "manifest.json"
    manifest.save(path)

    loaded = load_manifest(path)
    assert len(loaded.apps) == 1
    assert loaded.apps[0].package == "com.x"
    assert loaded.apps[0].availability is AppAvailability.BACKUP
    assert loaded.apps[0].apk_files == ["devices/S/apks/com.x/base.apk"]
