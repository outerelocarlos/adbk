# adbk one-command installer (Windows / PowerShell)
#
#   irm https://raw.githubusercontent.com/outerelocarlos/adbk/main/install.ps1 | iex
#
# Installs uv (which also provides Python 3.12) if needed, then installs the
# `adbk` CLI as an isolated, reusable tool. No admin rights required;
# nothing is installed system-wide. ADB itself is fetched later by `adbk
# setup-adb` into a private per-user directory.

$ErrorActionPreference = 'Stop'

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "==> $m" -ForegroundColor Yellow }

Info "Installing adbk"

# 1. Ensure uv is available (it manages an isolated Python and tool installs).
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Warn "uv not found; installing uv (Astral)"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # uv installs to %USERPROFILE%\.local\bin; use it in this session.
    $uvBin = Join-Path $env:USERPROFILE '.local\bin'
    if (Test-Path $uvBin) { $env:Path = "$uvBin;$env:Path" }
}

# 2. Install the CLI. Prefer PyPI; fall back to installing from GitHub so the
#    one-liner also works before the first PyPI release is published.
$pkg = 'adbk'
$repo = 'git+https://github.com/outerelocarlos/adbk'
Info "Installing the adbk CLI with uv"
try {
    uv tool install --python 3.12 --force $pkg
    if ($LASTEXITCODE -ne 0) { throw "uv tool install $pkg exited $LASTEXITCODE" }
} catch {
    Warn "PyPI install unavailable; installing from GitHub instead"
    uv tool install --python 3.12 --force $repo
}

# 3. Put uv's tool bin on PATH for future terminals.
uv tool update-shell

Write-Host ''
Write-Host 'Done. Open a NEW terminal, then run:' -ForegroundColor Green
Write-Host '    adbk doctor      # check the environment and set up ADB'
Write-Host '    adbk backup      # back up the connected phone'
