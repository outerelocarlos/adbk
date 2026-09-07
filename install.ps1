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

function Resolve-Uv {
    # Full path to uv.exe if it can be found, else '' (do not trust the session PATH).
    $cmd = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($dir in @("$env:USERPROFILE\.local\bin", "$env:LOCALAPPDATA\uv\bin",
                       "$env:USERPROFILE\.cargo\bin")) {
        $exe = Join-Path $dir 'uv.exe'
        if (Test-Path $exe) { return $exe }
    }
    return ''
}

Info "Installing adbk"

# 1. Ensure uv is available (it manages an isolated Python and tool installs).
$uv = Resolve-Uv
if (-not $uv) {
    Warn "uv not found; installing uv (Astral)"
    # Run uv's installer in a CHILD process: it calls `exit`, which would close
    # this window when the one-liner is run through `irm | iex`.
    & powershell -NoProfile -ExecutionPolicy Bypass -Command `
        "Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression"
    $uv = Resolve-Uv
}
if (-not $uv) {
    Warn "Could not locate uv after installing it. Open a NEW terminal and re-run"
    Warn "this command, or install uv from https://astral.sh/uv first."
    return
}
# Make uv (and the tools it installs) reachable in THIS session too.
$uvDir = Split-Path $uv -Parent
if ($uvDir -and (Test-Path $uvDir)) { $env:Path = "$uvDir;$env:Path" }

# 2. Install the CLI with uv's full path -- do not depend on the session PATH.
#    Prefer PyPI; fall back to GitHub so this works before the first PyPI release.
$repo = 'git+https://github.com/outerelocarlos/adbk'
Info "Installing the adbk CLI with uv"
& $uv tool install --python 3.12 --force adbk
if ($LASTEXITCODE -ne 0) {
    Warn "PyPI install unavailable; installing from GitHub instead"
    & $uv tool install --python 3.12 --force $repo
    if ($LASTEXITCODE -ne 0) { Warn "Installing adbk failed; see the output above."; return }
}

# 3. Put uv's tool bin on PATH -- persistently, and in this session so `adbk`
#    works right away instead of needing `uv run adbk`.
& $uv tool update-shell
$toolBin = Join-Path $env:USERPROFILE '.local\bin'
if (Test-Path $toolBin) { $env:Path = "$toolBin;$env:Path" }

Write-Host ''
if (Get-Command adbk -CommandType Application -ErrorAction SilentlyContinue) {
    Write-Host 'Done. adbk is installed and ready in this terminal:' -ForegroundColor Green
} else {
    Write-Host 'Done. Open a NEW terminal (so adbk is on PATH), then run:' -ForegroundColor Green
}
Write-Host '    adbk doctor      # check the environment and set up ADB'
Write-Host '    adbk backup      # back up the connected phone'
