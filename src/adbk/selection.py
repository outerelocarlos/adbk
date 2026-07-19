"""Category selection with size estimates and sub-folder exclusions.

Each selectable category carries an estimated size (from a fast ``du`` per
readable path) and may have sub-folders excluded (drilled into during
selection). Totals are de-duplicated: a path nested inside another selected path
is only counted once, and excluded sub-folders are subtracted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adbk import paths
from adbk.device import DeviceInterface
from adbk.models import AccessState, Category, DiscoveredPath


@dataclass
class CategoryEstimate:
    """One selectable category with its estimated on-device size."""

    name: str
    size_bytes: int
    readable_paths: list[str]
    included: bool
    path_sizes: dict[str, int] = field(default_factory=dict)
    exclusions: dict[str, int] = field(default_factory=dict)  # android path -> du size

    def excluded_bytes(self) -> int:
        return _dedup_sum(self.exclusions)

    def effective_size(self) -> int:
        return max(0, self.size_bytes - self.excluded_bytes())


def _dedup_sum(sizes: dict[str, int]) -> int:
    """Sum sizes over top-level paths only (a path inside another is counted once)."""

    kept = paths.drop_nested_paths(sizes.keys())
    return sum(sizes.get(path, 0) for path in kept)


def estimate_categories(
    device: DeviceInterface,
    discovered: list[DiscoveredPath],
    categories: list[Category],
) -> list[CategoryEstimate]:
    """Group readable discovered paths by category and estimate each one's size."""

    defaults = {c.name: c.default_selected for c in categories}
    order = [c.name for c in categories]

    by_category: dict[str, list[str]] = {}
    for path in discovered:
        if path.access_state is AccessState.READABLE:
            by_category.setdefault(path.category, []).append(path.android_path)

    estimates: list[CategoryEstimate] = []
    for name in order:
        readable_paths = by_category.get(name)
        if not readable_paths:
            continue
        path_sizes = {path: (device.disk_usage(path) or 0) for path in readable_paths}
        estimates.append(
            CategoryEstimate(
                name=name,
                size_bytes=sum(path_sizes.values()),
                readable_paths=readable_paths,
                included=defaults.get(name, True),
                path_sizes=path_sizes,
            )
        )
    return estimates


def selected_total_bytes(estimates: list[CategoryEstimate]) -> int:
    """De-duplicated total of selected categories, minus excluded sub-folders."""

    path_sizes: dict[str, int] = {}
    exclusions: dict[str, int] = {}
    for estimate in estimates:
        if not estimate.included:
            continue
        path_sizes.update(estimate.path_sizes)
        exclusions.update(estimate.exclusions)
    return max(0, _dedup_sum(path_sizes) - _dedup_sum(exclusions))


def selected_paths(estimates: list[CategoryEstimate]) -> set[str]:
    return {path for e in estimates if e.included for path in e.readable_paths}


def all_exclusions(estimates: list[CategoryEstimate]) -> set[str]:
    """Every excluded sub-folder path across the selected categories."""

    return {path for e in estimates if e.included for path in e.exclusions}
