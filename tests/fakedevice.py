"""An in-memory :class:`DeviceInterface` for testing without a real phone."""

from __future__ import annotations

import hashlib
from pathlib import Path

from adbk.device import DeviceListing, FlatEntry, RawEntry
from adbk.errors import DeviceAccessError, TransferError
from adbk.models import DeviceIdentity, EntryType
from adbk.paths import normalize_device_path


class FakeDevice:
    """A fake device backed by dictionaries of files and directories."""

    def __init__(
        self,
        *,
        serial: str = "FAKE123",
        model: str = "TestPhone",
        android_version: str = "14",
        root: bool = False,
        sha_available: bool = True,
    ) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.package_apk_paths: dict[str, list[str]] = {}
        self.package_meta: dict[str, dict[str, str]] = {}
        self.installed_apks: list[list[Path]] = []
        self.unreadable: set[str] = set()
        self.list_errors: set[str] = set()  # readable, but listing fails
        self.mtimes: dict[str, int] = {}
        self._serial = serial
        self._model = model
        self._android = android_version
        self._root = root
        self.sha_available = sha_available

    # -- construction helpers -------------------------------------------------

    def _ensure_parents(self, path: str) -> None:
        parts = path.strip("/").split("/")
        for i in range(1, len(parts)):
            self.dirs.add("/" + "/".join(parts[:i]))

    def add_file(self, path: str, content: bytes = b"data", *, mtime: int = 0, readable: bool = True) -> None:
        norm = normalize_device_path(path)
        self._ensure_parents(norm)
        self.files[norm] = content
        self.mtimes[norm] = mtime
        if not readable:
            self.unreadable.add(norm)

    def add_package(
        self,
        package: str,
        *,
        apks: tuple[str, ...] = (),
        installer: str = "com.android.vending",
        version_name: str = "1.0",
        version_code: str = "1",
    ) -> None:
        """Register an installed app, creating its APK files so they can be pulled."""

        paths = list(apks) or [f"/data/app/{package}/base.apk"]
        for path in paths:
            self.add_file(path, b"APK-" + package.encode())
        self.package_apk_paths[package] = paths
        self.package_meta[package] = {
            "installerPackageName": installer,
            "versionName": version_name,
            "versionCode": version_code,
        }

    def add_dir(self, path: str, *, readable: bool = True) -> None:
        norm = normalize_device_path(path)
        self._ensure_parents(norm)
        self.dirs.add(norm)
        if not readable:
            self.unreadable.add(norm)

    # -- DeviceInterface ------------------------------------------------------

    def identity(self) -> DeviceIdentity:
        return DeviceIdentity(self._serial, self._model, self._android, self._root)

    def has_root(self) -> bool:
        return self._root

    def exists(self, path: str) -> bool:
        norm = normalize_device_path(path)
        return norm in self.files or norm in self.dirs

    def is_dir(self, path: str) -> bool:
        return normalize_device_path(path) in self.dirs

    def is_readable(self, path: str) -> bool:
        return normalize_device_path(path) not in self.unreadable

    def _children(self, path: str) -> list[str]:
        prefix = path.rstrip("/") + "/"
        names: set[str] = set()
        for entry in list(self.files) + list(self.dirs):
            if entry.startswith(prefix):
                remainder = entry[len(prefix):]
                names.add(remainder.split("/", 1)[0])
        return sorted(names)

    def list_dir(self, path: str) -> list[RawEntry]:
        norm = normalize_device_path(path)
        if norm in self.unreadable or norm in self.list_errors:
            raise DeviceAccessError(f"permission denied: {norm}")
        entries: list[RawEntry] = []
        for name in self._children(norm):
            child = f"{norm}/{name}"
            if child in self.dirs:
                entries.append(RawEntry(name, EntryType.DIRECTORY))
            else:
                entries.append(RawEntry(name, EntryType.FILE, len(self.files[child]), self.mtimes.get(child, 0)))
        return entries

    def list_recursive(self, root: str) -> DeviceListing:
        norm = normalize_device_path(root)
        prefix = norm.rstrip("/") + "/"
        entries: list[FlatEntry] = []
        inaccessible: set[str] = set()
        for directory in sorted(self.dirs):
            if directory == norm or directory.startswith(prefix):
                if directory in self.unreadable or directory in self.list_errors:
                    inaccessible.add(directory)
                else:
                    entries.append(FlatEntry(directory, EntryType.DIRECTORY))
        for stored, content in self.files.items():
            if stored == norm or stored.startswith(prefix):
                entries.append(FlatEntry(stored, EntryType.FILE, len(content)))
        return DeviceListing(entries, inaccessible)

    def stat(self, path: str) -> RawEntry | None:
        norm = normalize_device_path(path)
        name = norm.rsplit("/", 1)[-1]
        if norm in self.dirs:
            return RawEntry(name, EntryType.DIRECTORY, None, self.mtimes.get(norm, 0))
        if norm in self.files:
            return RawEntry(name, EntryType.FILE, len(self.files[norm]), self.mtimes.get(norm, 0))
        return None

    def disk_usage(self, path: str) -> int | None:
        norm = normalize_device_path(path)
        prefix = norm.rstrip("/") + "/"
        found = norm in self.dirs or norm in self.files
        total = 0
        for stored, content in self.files.items():
            if stored == norm or stored.startswith(prefix):
                total += len(content)
                found = True
        return total if found else None

    def disk_usage_tree(self, path: str, depth: int = 1) -> dict[str, int]:
        norm = normalize_device_path(path)
        sizes: dict[str, int] = {norm: self.disk_usage(norm) or 0}

        def walk(current: str, level: int) -> None:
            if level > depth:
                return
            for name in self._children(current):
                child = f"{current}/{name}"
                if child in self.dirs:
                    sizes[child] = self.disk_usage(child) or 0
                    walk(child, level + 1)

        walk(norm, 1)
        return sizes

    def sha256(self, path: str) -> str | None:
        if not self.sha_available:
            return None
        norm = normalize_device_path(path)
        if norm not in self.files:
            return None
        return hashlib.sha256(self.files[norm]).hexdigest()

    def pull(self, remote: str, local: Path) -> None:
        norm = normalize_device_path(remote)
        if norm not in self.files:
            raise TransferError(f"no such file: {norm}")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(self.files[norm])

    def push(self, local: Path, remote: str) -> None:
        norm = normalize_device_path(remote)
        self._ensure_parents(norm)
        self.files[norm] = local.read_bytes()

    def delete_file(self, remote: str) -> bool:
        norm = normalize_device_path(remote)
        self.files.pop(norm, None)
        self.mtimes.pop(norm, None)
        return norm not in self.files

    def delete_dir(self, remote: str) -> bool:
        norm = normalize_device_path(remote)
        if self._children(norm):
            return False  # rmdir refuses non-empty directories
        self.dirs.discard(norm)
        return norm not in self.dirs

    # -- installed applications -----------------------------------------------

    def list_packages(self) -> list[str]:
        return sorted(self.package_apk_paths)

    def package_apks(self, package: str) -> list[str]:
        return list(self.package_apk_paths.get(package, []))

    def package_details(self, package: str) -> dict[str, str]:
        return dict(self.package_meta.get(package, {}))

    def install_apks(self, local_paths: list[Path]) -> bool:
        if not local_paths:
            return False
        self.installed_apks.append(list(local_paths))
        return True
