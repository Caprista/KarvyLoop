# KarvyLoop uninstaller (Windows) — removes exactly what install.ps1 created
# ($env:LOCALAPPDATA\karvyloop: venv + bin shim; user-PATH entry).
# Instance data ($env:USERPROFILE\.karvyloop) is KEPT by default; -PurgeData removes it too
# (consider `karvyloop export` first).
param([switch]$PurgeData)
$ErrorActionPreference = 'Continue'

$Base = Join-Path $env:LOCALAPPDATA 'karvyloop'
$BinDir = Join-Path $Base 'bin'
$Data = Join-Path $env:USERPROFILE '.karvyloop'

Write-Host 'KarvyLoop uninstall:'
if (Test-Path $Base) {
    Remove-Item -Recurse -Force $Base -Confirm:$false
    Write-Host "  removed $Base"
} else {
    Write-Host "  (nothing at $Base)"
}

# take the bin dir out of the *user* PATH (that's the scope install.ps1 wrote)
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath) {
    $parts = $userPath -split ';' | Where-Object { $_ -and ($_ -ne $BinDir) }
    $newPath = ($parts -join ';')
    if ($newPath -ne $userPath) {
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        Write-Host "  removed $BinDir from user PATH"
    }
}

if ($PurgeData) {
    if (Test-Path $Data) {
        Remove-Item -Recurse -Force $Data -Confirm:$false
        Write-Host "  removed $Data (instance data - gone)"
    }
} elseif (Test-Path $Data) {
    Write-Host "  KEPT $Data (your instance data) - rerun with -PurgeData to remove it"
}
Write-Host 'Done. Open a new terminal so PATH changes take effect.'
