# KarvyLoop installer (Windows) -- installs the `karvyloop` command onto your PATH, isolated from system Python.
#
#   irm https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.ps1 | iex
#
# Mirrors scripts/install.sh: KarvyLoop goes into its own dedicated venv (never system Python) and a
# `karvyloop.cmd` shim lands on your user PATH -- nothing for you to configure. Re-running upgrades in place.
#
# Windows runs KarvyLoop in DEGRADED mode: the runtime, console and your own crystallized skills all work;
# only third-party skill scripts are disabled (no sandbox on Windows yet -- Linux/macOS get the full sandbox).
#
# Env overrides:  KARVYLOOP_REF=<branch|tag>   KARVYLOOP_EXTRAS=mcp,web   KARVYLOOP_REPO=<git url>
#
# PowerShell 5.1 compatible. ASCII-only output.

$ErrorActionPreference = 'Stop'

$Repo   = if ($env:KARVYLOOP_REPO)   { $env:KARVYLOOP_REPO }   else { 'https://github.com/Caprista/KarvyLoop.git' }
$Ref    = if ($env:KARVYLOOP_REF)    { $env:KARVYLOOP_REF }    else { 'main' }
$Extras = if ($env:KARVYLOOP_EXTRAS) { $env:KARVYLOOP_EXTRAS } else { '' }

$Base   = Join-Path $env:LOCALAPPDATA 'karvyloop'
$Venv   = Join-Path $Base 'venv'
$BinDir = Join-Path $Base 'bin'

if ($Extras) { $Spec = "karvyloop[$Extras] @ git+$Repo@$Ref" } else { $Spec = "git+$Repo@$Ref" }

# 1) find a Python 3.11+ (python / python3 on PATH, then the `py -3.11` launcher fallback)
$VerCheck = 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)'
$Py = $null
$PyArgs = @()
foreach ($cand in @('python', 'python3')) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) {
        & $cand -c $VerCheck
        if ($LASTEXITCODE -eq 0) { $Py = $cand; $PyArgs = @(); break }
    }
}
if (-not $Py) {
    if (Get-Command 'py' -ErrorAction SilentlyContinue) {
        & py -3.11 -c $VerCheck
        if ($LASTEXITCODE -eq 0) { $Py = 'py'; $PyArgs = @('-3.11') }
    }
}
if (-not $Py) {
    throw "Python 3.11+ is required but was not found on PATH (tried: python, python3, py -3.11). Install it from https://www.python.org/downloads/ (check 'Add python.exe to PATH') and re-run."
}
$PyVersion = & $Py @PyArgs -V
Write-Host "-> Using $PyVersion  ($((Get-Command $Py).Source))"

# pip installs from a git+ URL, which needs the git CLI -- fail early with a clear message
# instead of a mid-install stack trace (mojibake on non-UTF8 consoles).
if (-not (Get-Command 'git' -ErrorAction SilentlyContinue)) {
    throw "git is required (pip installs KarvyLoop from a git URL) but was not found on PATH. Install it from https://git-scm.com/download/win and re-run."
}

# 2) Self-contained, one path, zero config: a dedicated venv + a .cmd shim on the user PATH. Installing
#    INTO a venv never touches system Python. Re-running upgrades in place.
Write-Host "-> Creating an isolated environment at $Venv ..."
& $Py @PyArgs -m venv $Venv
if ($LASTEXITCODE -ne 0) { throw "couldn't create a venv at $Venv" }
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
& $VenvPython -m pip install -q --upgrade pip | Out-Null

Write-Host "-> Installing KarvyLoop from $Repo@$Ref ..."
& $VenvPython -m pip install -q --upgrade $Spec
if ($LASTEXITCODE -ne 0) { throw 'install failed.' }

# 3) `karvyloop.cmd` shim in a stable bin dir (venv layout stays an implementation detail)
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$KarvyloopExe = Join-Path $Venv 'Scripts\karvyloop.exe'
$ShimPath = Join-Path $BinDir 'karvyloop.cmd'
# PYTHONUTF8=1: on Chinese/other non-UTF8 Windows locales, piping output that contains
# unicode glyphs (doctor's check marks etc.) crashes with UnicodeEncodeError under GBK.
Set-Content -Path $ShimPath -Value "@echo off`r`nset PYTHONUTF8=1`r`n`"$KarvyloopExe`" %*" -Encoding Ascii
Write-Host "-> Created shim $ShimPath"

# 4) ensure the bin dir is on the *user* PATH (persistent; no admin needed)
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($null -eq $UserPath) { $UserPath = '' }
if (($UserPath -split ';') -notcontains $BinDir) {
    $NewPath = if ($UserPath.TrimEnd(';')) { $UserPath.TrimEnd(';') + ';' + $BinDir } else { $BinDir }
    [Environment]::SetEnvironmentVariable('Path', $NewPath, 'User')
    Write-Host "-> Added $BinDir to your user PATH"
}
if (($env:Path -split ';') -notcontains $BinDir) { $env:Path = "$env:Path;$BinDir" }

Write-Host ''
Write-Host 'OK - KarvyLoop installed.'
Write-Host ''
Write-Host '  Open a NEW terminal (so PATH refreshes), then:'
Write-Host '     karvyloop console      # start the local console (opens the web UI)'
Write-Host '     karvyloop url          # print the access link (needed to reach it from another device)'
Write-Host ''
Write-Host '  Note: Windows runs in degraded mode -- third-party skill scripts are disabled (no sandbox yet);'
Write-Host '  everything else works. Linux/macOS get the full sandbox.'
