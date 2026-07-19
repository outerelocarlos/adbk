"""Entry point so the tool can be run as ``python -m adbk``."""

from __future__ import annotations

from adbk.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
