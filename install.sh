#!/bin/sh
# adbk one-command installer (Linux / macOS)
#
#   curl -fsSL https://raw.githubusercontent.com/outerelocarlos/adbk/main/install.sh | sh
#
# Installs uv (which also provides Python 3.12) if needed, then installs the
# `adbk` CLI as an isolated, reusable tool. Nothing is installed
# system-wide. ADB itself is fetched later by `adbk setup-adb` into a private
# per-user directory.

set -eu

info() { printf '==> %s\n' "$1"; }

# Full path to uv if it can be found (do not trust the session PATH alone).
resolve_uv() {
    if command -v uv >/dev/null 2>&1; then command -v uv; return 0; fi
    for d in "$HOME/.local/bin" "$HOME/.cargo/bin" "${XDG_BIN_HOME:-}"; do
        if [ -n "$d" ] && [ -x "$d/uv" ]; then printf '%s\n' "$d/uv"; return 0; fi
    done
    return 1
}

info "Installing adbk"

# 1. Ensure uv is available (it manages an isolated Python and tool installs).
UV="$(resolve_uv || true)"
if [ -z "$UV" ]; then
    info "uv not found; installing uv (Astral)"
    curl -fsSL https://astral.sh/uv/install.sh | sh
    # The installer drops an env file that adds uv to PATH; use it, plus fallbacks.
    [ -f "$HOME/.local/bin/env" ] && . "$HOME/.local/bin/env"
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    UV="$(resolve_uv || true)"
fi
if [ -z "$UV" ]; then
    info "Could not locate uv after installing it. Open a NEW terminal and re-run,"
    info "or install uv from https://astral.sh/uv first."
    exit 1
fi

# 2. Install the CLI with uv's full path -- do not depend on the session PATH.
#    Prefer PyPI; fall back to GitHub so this works before the first PyPI release.
info "Installing the adbk CLI with uv"
if ! "$UV" tool install --python 3.12 --force adbk; then
    info "PyPI install unavailable; installing from GitHub instead"
    "$UV" tool install --python 3.12 --force \
        "git+https://github.com/outerelocarlos/adbk"
fi

# 3. Put uv's tool bin on PATH -- persistently, and in this shell so `adbk`
#    works right away instead of needing `uv run adbk`.
"$UV" tool update-shell || true
export PATH="$HOME/.local/bin:$PATH"

printf '\n'
if command -v adbk >/dev/null 2>&1; then
    printf 'Done. adbk is installed and ready in this shell:\n'
else
    printf 'Done. Open a NEW terminal (so adbk is on PATH), then run:\n'
fi
printf '    adbk doctor      # check the environment and set up ADB\n'
printf '    adbk backup      # back up the connected phone\n'
