# Build picasso.exe via PyInstaller --onedir.
# Run from anywhere: .\build\build.ps1
#
# Output: dist\Picasso\picasso.exe + dependencies, ready to be zipped and
# shipped (or pointed at by install.bat).

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "==> Building Picasso (PyInstaller --onedir)" -ForegroundColor Cyan
Write-Host "    project root: $projectRoot"

# Use the venv's Python so the build picks up the project's pinned deps.
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "venv not found at $python.`nRun 'python -m venv .venv' and '.venv\Scripts\python.exe -m pip install -e .' from the project root first."
}

# Ensure pyinstaller is available in the venv (idempotent).
& $python -m pip install --quiet --disable-pip-version-check pyinstaller

# Clean prior dist + build cache so we don't ship stale binaries.
$distDir = Join-Path $projectRoot "dist\Picasso"
$workDir = Join-Path $projectRoot "build\.pyinstaller"
if (Test-Path $distDir) { Remove-Item -Recurse -Force $distDir }
if (Test-Path $workDir) { Remove-Item -Recurse -Force $workDir }

$specFile = Join-Path $projectRoot "build\picasso.spec"

# Pillow plugin collection lives inside the spec (collect_all("PIL")) —
# can't pass --collect-all alongside a spec file.
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath (Join-Path $projectRoot "dist") `
    --workpath $workDir `
    "$specFile"

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed (exit code $LASTEXITCODE)."
}

# Copy the install + manual-update scripts into the dist folder so the
# shipped zip is one self-contained unit.
foreach ($script in @("install.bat", "update-alida.bat")) {
    $src = Join-Path $projectRoot $script
    $dst = Join-Path $distDir $script
    if (Test-Path $src) {
        Copy-Item $src $dst -Force
    } else {
        Write-Warning "expected $script at project root; skipping copy"
    }
}

Write-Host ""
Write-Host "==> Build complete: $distDir" -ForegroundColor Green

# Quick size + smoke-test summary.
$exe = Join-Path $distDir "picasso.exe"
if (Test-Path $exe) {
    $sizeMB = [math]::Round((Get-ChildItem -Recurse $distDir | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
    Write-Host "    picasso.exe + bundle: $sizeMB MB"
    Write-Host "    Smoke-test: $exe"
    Write-Host "    Ship the whole $distDir folder zipped."
} else {
    Write-Error "picasso.exe not found in $distDir after build."
}
