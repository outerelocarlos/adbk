"""The backup manifest: versioned, UTF-8 JSON, written atomically.

The manifest is the source of truth for verification, resume and restore. It
stores **logical** relative local paths (never absolute host/container paths),
records copy/verification/deletion results per entry, and is flushed atomically
after each change so an interruption always leaves a valid, resumable file.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from adbk import __version__
from adbk.errors import ConfigError
from adbk.models import (
    AppAvailability,
    CopyResult,
    DeletionResult,
    DeviceIdentity,
    EntryType,
    ManifestState,
    Operation,
    TransferMode,
    VerificationResult,
)

MANIFEST_VERSION = 2
MANIFEST_FILENAME = "manifest.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class ManifestEntry:
    android_path: str
    local_relative_path: str
    entry_type: EntryType = EntryType.FILE
    category: str = ""
    size: int | None = None
    mtime: int | None = None
    sha256: str | None = None
    copy_result: CopyResult = CopyResult.NOT_ATTEMPTED
    verification_result: VerificationResult = VerificationResult.NOT_ATTEMPTED
    deletion_result: DeletionResult = DeletionResult.NOT_ATTEMPTED
    source_removed: bool = False
    conflict_decision: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ManifestEntry:
        return cls(
            android_path=str(data.get("android_path", "")),
            local_relative_path=str(data.get("local_relative_path", "")),
            entry_type=EntryType(str(data.get("entry_type", EntryType.FILE))),
            category=str(data.get("category", "")),
            size=_opt_int(data.get("size")),
            mtime=_opt_int(data.get("mtime")),
            sha256=_opt_str(data.get("sha256")),
            copy_result=CopyResult(str(data.get("copy_result", CopyResult.NOT_ATTEMPTED))),
            verification_result=VerificationResult(
                str(data.get("verification_result", VerificationResult.NOT_ATTEMPTED))
            ),
            deletion_result=DeletionResult(
                str(data.get("deletion_result", DeletionResult.NOT_ATTEMPTED))
            ),
            source_removed=bool(data.get("source_removed", False)),
            conflict_decision=str(data.get("conflict_decision", "")),
            error=str(data.get("error", "")),
        )


@dataclass
class AppRecord:
    """One installed app, and how it could be reinstalled on a new device."""

    package: str
    label: str = ""
    version_name: str = ""
    version_code: str = ""
    installer: str = ""
    apk_files: list[str] = field(default_factory=list)  # logical relative paths
    availability: AppAvailability = AppAvailability.UNKNOWN
    store_checked: bool = False  # True only when the store was actually queried
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AppRecord:
        raw_apks = data.get("apk_files", [])
        apk_files = [str(item) for item in raw_apks] if isinstance(raw_apks, list) else []
        return cls(
            package=str(data.get("package", "")),
            label=str(data.get("label", "")),
            version_name=str(data.get("version_name", "")),
            version_code=str(data.get("version_code", "")),
            installer=str(data.get("installer", "")),
            apk_files=apk_files,
            availability=AppAvailability(
                str(data.get("availability", AppAvailability.UNKNOWN))
            ),
            store_checked=bool(data.get("store_checked", False)),
            note=str(data.get("note", "")),
        )


def _opt_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


def _opt_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


@dataclass
class Manifest:
    operation: Operation
    mode: TransferMode
    device: DeviceIdentity
    manifest_version: int = MANIFEST_VERSION
    tool_version: str = __version__
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    state: ManifestState = ManifestState.IN_PROGRESS
    selected_categories: list[str] = field(default_factory=list)
    entries: dict[str, ManifestEntry] = field(default_factory=dict)
    apps: list[AppRecord] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    config_snapshot: dict[str, str] = field(default_factory=dict)

    # -- mutation -------------------------------------------------------------

    def upsert(self, entry: ManifestEntry) -> None:
        self.entries[entry.local_relative_path] = entry
        self.updated_at = _now()

    def get(self, local_relative_path: str) -> ManifestEntry | None:
        return self.entries.get(local_relative_path)

    def mark_skipped(self, android_path: str, reason: str) -> None:
        self.skipped.append({"android_path": android_path, "reason": reason})
        self.updated_at = _now()

    def finalize(self, state: ManifestState) -> None:
        self.state = state
        self.updated_at = _now()

    # -- serialisation --------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "tool_version": self.tool_version,
            "operation": str(self.operation),
            "mode": str(self.mode),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "state": str(self.state),
            "device": asdict(self.device),
            "root_available": self.device.root_available,
            "selected_categories": list(self.selected_categories),
            "entries": [entry.to_dict() for entry in self.entries.values()],
            "apps": [app.to_dict() for app in self.apps],
            "skipped": list(self.skipped),
            "config_snapshot": dict(self.config_snapshot),
        }

    def save(self, path: Path) -> None:
        """Write the manifest atomically (temp file in the same dir, then replace)."""

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".manifest-", suffix=".json", dir=path.parent)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            tmp_path.replace(path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Manifest:
        device_raw = data.get("device", {})
        device_dict = device_raw if isinstance(device_raw, dict) else {}
        device = DeviceIdentity(
            serial=str(device_dict.get("serial", "unknown")),
            model=str(device_dict.get("model", "")),
            android_version=str(device_dict.get("android_version", "")),
            root_available=bool(device_dict.get("root_available", False)),
        )
        entries_raw = data.get("entries", [])
        entries: dict[str, ManifestEntry] = {}
        if isinstance(entries_raw, list):
            for item in entries_raw:
                if isinstance(item, dict):
                    entry = ManifestEntry.from_dict(item)
                    entries[entry.local_relative_path] = entry
        apps_raw = data.get("apps", [])
        apps = [
            AppRecord.from_dict(item) for item in apps_raw if isinstance(item, dict)
        ] if isinstance(apps_raw, list) else []
        skipped_raw = data.get("skipped", [])
        skipped = [
            {str(k): str(v) for k, v in item.items()}
            for item in skipped_raw
            if isinstance(item, dict)
        ] if isinstance(skipped_raw, list) else []
        snapshot_raw = data.get("config_snapshot", {})
        snapshot = (
            {str(k): str(v) for k, v in snapshot_raw.items()}
            if isinstance(snapshot_raw, dict)
            else {}
        )
        categories_raw = data.get("selected_categories", [])
        categories = [str(c) for c in categories_raw] if isinstance(categories_raw, list) else []

        raw_version = data.get("manifest_version")
        manifest_version = raw_version if isinstance(raw_version, int) else MANIFEST_VERSION

        return cls(
            operation=Operation(str(data.get("operation", Operation.BACKUP))),
            mode=TransferMode(str(data.get("mode", TransferMode.SAFE_MOVE))),
            device=device,
            manifest_version=manifest_version,
            tool_version=str(data.get("tool_version", "")),
            created_at=str(data.get("created_at", _now())),
            updated_at=str(data.get("updated_at", _now())),
            state=ManifestState(str(data.get("state", ManifestState.IN_PROGRESS))),
            selected_categories=categories,
            entries=entries,
            apps=apps,
            skipped=skipped,
            config_snapshot=snapshot,
        )


def load_manifest(path: Path) -> Manifest:
    """Load and parse a manifest file."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Could not read manifest {path}: {exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"Invalid manifest JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Manifest {path} is not a JSON object.")
    return Manifest.from_dict(data)
