"""adbk - back up an Android phone over ADB before migrating devices.

The package is intentionally split into small, individually testable modules:

* ``platform_support`` isolates every operating-system difference.
* ``adb``, ``adb_sources`` and ``adb_installer`` handle locating, installing and
  running the ``adb`` executable.
* the core backup/restore logic (planner, manifest, verification, tree
  rendering and transfer) stays platform independent.

Nothing here assumes POSIX behaviour; see ``platform_support`` for the few
places where the operating system genuinely matters.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
