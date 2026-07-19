# adbk

An interactive, cross-platform command-line tool that backs up a connected
Android phone over **ADB** so you can migrate to a new device without losing
files, game saves, emulator data and app media.

It runs natively on **Windows, Linux and macOS**, and inside **Docker**. It can
**install ADB for you** (the official Google platform-tools) into a private
per-user directory - no administrator rights, no changes to your `PATH`.

See [`MIGRATION.md`](MIGRATION.md) for practical migration notes: which game
saves are captured, how to handle delisted games, and which apps (WhatsApp,
Signal, launchers, authenticators, ...) need their own export/transfer step
instead of a plain file copy.

## Requirements

- Python **3.12+**
- A USB cable and an Android phone with **USB debugging** enabled
  (Settings -> Developer options -> USB debugging)

## Install

### One command

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/outerelocarlos/adbk/main/install.ps1 | iex
```

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/outerelocarlos/adbk/main/install.sh | sh
```

This installs [`uv`](https://docs.astral.sh/uv/) if needed (which also provides
Python 3.12), then installs the CLI as an isolated tool. No admin rights, no
changes to system Python. Open a new terminal afterwards and run `adbk doctor`.

### From PyPI

If you already have [`uv`](https://docs.astral.sh/uv/) or
[`pipx`](https://pipx.pypa.io/):

```bash
uv tool install adbk      # or: pipx install adbk
```

### From source (for development)

```bash
git clone https://github.com/outerelocarlos/adbk
cd adbk
uv sync
uv run adbk doctor
```

The same commands work in PowerShell on Windows and in a POSIX shell on Linux
and macOS.

## Commands

```bash
python -m adbk backup              # back up the connected device (verified safe move)
python -m adbk backup --copy       # back up, but never delete anything from the phone
python -m adbk backup --safe-move  # the explicit default: delete each source after verify
python -m adbk backup --resume     # resume an interrupted backup
python -m adbk restore             # restore files from a backup manifest
python -m adbk doctor              # environment + ADB health check
python -m adbk setup-adb           # download & install a managed ADB
python -m adbk update-adb          # check for / install a newer managed ADB
```

The two backup modes: **`--safe-move`** (the default) deletes each source file
from the phone only after its copy is verified by SHA-256 on both sides;
**`--copy`** transfers everything and leaves the phone untouched.

Run with no subcommand to back up (or, if a manifest is present in the backup
directory, be offered a restore). See [`COMMANDS.md`](COMMANDS.md) for a full
cheatsheet (the CLI is also available as the short alias `adbk`).

Useful options (work before or after the command):

| Option | Purpose |
| --- | --- |
| `--copy` | Back up by copying only; never delete anything from the phone. |
| `--safe-move` | Verified safe move (default): delete each source only after its copy is verified. |
| `--resume` | Resume an interrupted backup, re-verifying prior entries. |
| `--adb-path PATH` | Use a specific `adb` executable. |
| `--adb-server-host HOST` | Connect to an ADB server (e.g. `host.docker.internal`). |
| `--adb-server-port PORT` | ADB server port (default `5037`). |
| `--install-adb` | Permit automatic installation when ADB is missing. |
| `--no-install-adb` | Never install ADB automatically. |
| `--yes` / `-y` | Assume "yes" to prompts (non-interactive). |
| `--dry-run` | Report actions without downloading or changing anything. |
| `--check-network` | Let `doctor` probe the official download source. |
| `--verbose` / `-v` | Show detailed progress. |

## Backing up and restoring

```bash
# Verified safe move is the DEFAULT: each file is copied, its SHA-256 is checked
# on both sides, and only then is the source deleted from the phone.
python -m adbk backup --backup-dir /path/to/backups

# Copy only - never delete anything from the device:
python -m adbk backup --copy

# See exactly what would happen without touching anything:
python -m adbk backup --dry-run

# Resume an interrupted backup (re-verifies prior entries, retries the rest):
python -m adbk backup --resume

# Restore, choosing how conflicts are handled (default: skip identical, ask about different):
python -m adbk restore --backup-dir /path/to/backups
python -m adbk restore --conflict overwrite-different
```

How it works:

- **Backups are dated**: each run is stored in `<root>/YYYY-MM-DD/` (default
  root `~/backup`, or a container's `/data/backup`; override the root with
  `--backup-dir`). A second backup on the same day gets a `-2`, `-3`, ... suffix.
  `restore` and `--resume` act on the most recent dated backup under the root
  unless you point them at a specific one with `--manifest`.
- **Categories** and their candidate Android paths come from configuration
  (`[[category]]` tables); discovery reports each as readable, inaccessible,
  missing, root-only or empty - a failed listing is never treated as empty.
- An **adaptive tree** shows what was found: folders list up to three files or a
  summary for four or more, and single-child folder chains collapse onto one
  line (`Android > media > com.whatsapp > WhatsApp > Media`).
- A **versioned JSON manifest** (`manifest.json`) is written atomically after
  every file, using portable relative paths, and drives verification, resume and
  restore.
- **Ctrl+C** cancels gracefully: it starts no new deletion, keeps every
  unverified source, flushes the manifest and leaves the backup resumable.

App chat databases that live in private storage (WhatsApp chats, Signal, Nova
preferences) cannot be file-copied without root; use each app's own
migration/export and back up the resulting file from shared storage.

### ADB resolution order

1. `--adb-path`
2. `ADBK_ADB_PATH`
3. An installation previously managed by this application
4. ADB on the system `PATH`
5. Common Android SDK locations
6. Offer to download and install the official platform-tools

A managed ADB installs under a per-user data directory (overridable with
`ADBK_TOOLS_DIRECTORY`). An existing, usable ADB is never modified.

## Configuration

Copy [`backup-config.example.toml`](backup-config.example.toml) to
`backup-config.toml` and edit as needed, or pass `--config PATH`.

## Docker and physical devices

Containerisation makes the *application environment* portable, but physical USB
access is controlled by the host and is **not** equally portable across Docker
implementations. The recommended cross-platform container mode runs the ADB
server on the host and connects to it over TCP. Full Docker documentation is
added alongside the container files.

## Development

```bash
uv run ruff check .
uv run mypy src/adbk
uv run pytest
```

The package lives under `src/adbk/` (src layout). The CLI is available
as both `adbk` and the short alias `adbk`.

## License

Copyright (C) 2026 outerelocarlos.

This program is free software: you can redistribute it and/or modify it under
the terms of the **GNU General Public License** as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version. It is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY. See the [`LICENSE`](LICENSE) file for the full text.
