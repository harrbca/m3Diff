# Build the m3diff-engine PyInstaller sidecar for the Tauri bundle (ADR-021).
#
# Produces: desktop/src-tauri/binaries/m3diff-engine-<target-triple>.exe
# (Tauri's externalBin resolves the triple-suffixed name at bundle time.)
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build-sidecar.ps1
# Requires network for pip (pyinstaller + httpx into a local build venv).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv-build"
$binaries = Join-Path $root "desktop\src-tauri\binaries"
$triple = "x86_64-pc-windows-msvc"

if (-not (Test-Path $venv)) {
    Write-Host "creating build venv..."
    python -m venv $venv
}
$py = Join-Path $venv "Scripts\python.exe"

& $py -m pip install --quiet --upgrade pyinstaller httpx
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "freezing engine..."
& $py -m PyInstaller `
    --onefile `
    --console `
    --name m3diff-engine `
    --paths (Join-Path $root "engine\src") `
    --distpath $binaries `
    --workpath (Join-Path $root ".pyinstaller-work") `
    --specpath (Join-Path $root ".pyinstaller-work") `
    --noconfirm `
    (Join-Path $root "engine\packaging\entry.py")
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

# Tauri expects the target triple in the filename.
$plain = Join-Path $binaries "m3diff-engine.exe"
$suffixed = Join-Path $binaries "m3diff-engine-$triple.exe"
Move-Item -Force $plain $suffixed
Write-Host "sidecar: $suffixed ($([math]::Round((Get-Item $suffixed).Length / 1MB, 1)) MB)"
