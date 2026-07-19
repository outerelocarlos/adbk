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

info "Installing adbk"

# 1. Ensure uv is available (it manages an isolated Python and tool installs).
if ! command -v uv >/dev/null 2>&1; then
    info "uv not found; installing uv (Astral)"
    curl -fsSL https://astral.sh/uv/install.sh | sh
    # uv installs to ~/.local/bin; use it in this session.
    export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Install the CLI. Prefer PyPI; fall back to installing from GitHub so the
#    one-liner also works before the first PyPI release is published.
info "Installing the adbk CLI with uv"
if ! uv tool install --python 3.12 --force adbk; then
    info "PyPI install unavailable; installing from GitHub instead"
    uv tool install --python 3.12 --force \
        "git+https://github.com/outerelocarlos/adbk"
fi

# 3. Put uv's tool bin on PATH for future shells.
uv tool update-shell || true

printf '\n'
printf 'Done. Open a NEW terminal, then run:\n'
printf '    adbk doctor      # check the environment and set up ADB\n'
printf '    adbk backup      # back up the connected phone\n'
