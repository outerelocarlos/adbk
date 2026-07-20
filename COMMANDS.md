# Command cheatsheet

The CLI is available as **`adbk`** (short alias) or `adbk`. If you
installed via the one-command installer, PyPI (`uv tool install adbk`)
or `pip install -e .`, call `adbk` directly. From a source checkout without
installing, prefix with `uv run` (e.g. `uv run adbk doctor`). All examples below
use the short form.

## First-time setup

```bash
adbk setup-adb       # download & install a private ADB (official Google build)
adbk doctor          # environment + device health check
```

From a source checkout (no install), prefix each command with `uv run` after a
one-time `uv sync`.

On the phone: **Settings -> Developer options -> USB debugging**, then accept the
"Allow USB debugging" prompt.

## Backup

```bash
uv run adbk backup --dry-run          # inspect + plan, change nothing
uv run adbk backup --copy             # copy only, never delete from the phone
uv run adbk backup                    # default: verified safe move (deletes source after verify)
uv run adbk backup --resume           # resume the most recent backup
uv run adbk backup --no-check-store   # skip the store lookup (it is asked, default yes)
uv run adbk backup --backup-dir D:/phone-backup   # set the root (default: ~/android-backup)
```

Each run is saved in a dated sub-directory, e.g. `~/android-backup/2026-07-20/` (a second
run the same day becomes `-2`, `-3`, ...). `restore`/`--resume` use the newest one
under the root unless you pass `--manifest PATH`.

In the interactive picker: `<num>` toggles a category, `d <num>` drills in to pick
sub-folders, `a`/`n` select all/none, `Enter` continues. Inside a drill: `<num>`
toggles, `d <num>` opens a sub-folder, `b` goes back.

## Restore

```bash
uv run adbk restore                                   # default: skip identical, ask on different
uv run adbk restore --conflict overwrite-different
uv run adbk restore --manifest D:/phone-backup/manifest.json
```

## ADB helpers (managed adb)

```bash
ADB=$HOME/.local/share/adbk/platform-tools/adb   # Linux/macOS
# Windows: %USERPROFILE%\AppData\Local\adbk\platform-tools\adb.exe

adb devices -l          # list devices (should say "device", not "unauthorized")
adb kill-server         # re-trigger the authorization prompt, then adb devices
```

## Delisted game (save in private storage)

```bash
# APK (no root):
adb pull "$(adb shell pm path <package> | sed 's/package://')" game.apk
# Private save, if the app allows backup (adb backup is interactive -- tap on phone):
adb backup -f game_save.ab <package>
# On the new phone: install the APK, restore the OBB, then:
adb restore game_save.ab
```

## Docker

```bash
docker compose run --rm adbk                 # run the app (host ADB-server mode)
docker compose run --rm adbk --dry-run
docker compose run --rm adbk-dev pytest      # tests in the dev image
docker compose run --rm adbk-dev ruff check .
docker compose run --rm adbk-dev mypy src/adbk
docker build -t adbk:latest .
```

## Dev tools (native)

```bash
uv run ruff check .            # lint
uv run ruff check --fix .      # lint + autofix
uv run mypy src/adbk # type-check
uv run pytest -q               # tests
```

## Configuration

Copy `backup-config.example.toml` to `backup-config.toml` (gitignored) to set the
backup destination, ADB server, `[[category]]` entries, and `[filters]`
(extra junk patterns). See the example file for all keys.
