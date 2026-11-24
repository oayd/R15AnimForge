#Requires -Version 5.1
<#
.SYNOPSIS
    Offline build script for R15 AnimForge (PyInstaller ONEDIR portable + optional Inno Setup installer).
.PARAMETER BlenderDir
    Path to portable Blender 5.3.0 directory to bundle under AnimForge\blender\.
.PARAMETER SkipTests
    Skip pytest unit suite before building.
.PARAMETER KeepDebugSymbols
    Keep blender.pdb and Cycles AOT kernels in bundled Blender (larger by ~130 MB).
#>
param(
    [string]$BlenderDir = $env:ANIMFORGE_BLENDER_DIR,
    [switch]$SkipTests,
    [switch]$KeepDebugSymbols
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $RepoRoot

try {
    Write-Host "=== R15 AnimForge Build ==="
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Write-Host "[1/6] Creating virtual environment..."
        python -m venv .venv
        .\.venv\Scripts\python.exe -m pip install --upgrade pip
        .\.venv\Scripts\python.exe -m pip install numpy pytest pyinstaller -e .
    }

    if (-not $SkipTests) {
        Write-Host "[2/6] Running unit tests..."
        .\.venv\Scripts\pytest.exe -v
        if ($LASTEXITCODE -ne 0) { throw "Unit tests failed." }
    }

    Write-Host "[3/6] Generating icon..."
    .\.venv\Scripts\python.exe .\packaging\make_icon.py

    Write-Host "[4/6] Running PyInstaller ONEDIR (no UPX)..."
    .\.venv\Scripts\pyinstaller.exe --noconfirm --clean .\packaging\animforge.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    $DistApp = Join-Path $RepoRoot "dist\AnimForge"
    Write-Host "[5/6] Assembling portable distribution at $DistApp..."

    # Mirror data, profiles, vendor, licenses cleanly beside AnimForge.exe
    foreach ($sub in @("data", "profiles")) {
        $dst = Join-Path $DistApp $sub
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item (Join-Path $RepoRoot $sub) $dst -Recurse -Force
    }

    $VendorSrc = Join-Path $RepoRoot "vendor"
    $VendorDst = Join-Path $DistApp "vendor"
    if (Test-Path $VendorSrc) {
        if (Test-Path $VendorDst) { Remove-Item $VendorDst -Recurse -Force }
        Copy-Item $VendorSrc $VendorDst -Recurse -Force
    }

    $LicDst = Join-Path $DistApp "licenses"
    New-Item -ItemType Directory -Force -Path $LicDst | Out-Null
    Copy-Item (Join-Path $RepoRoot "THIRD_PARTY_LICENSES.md") (Join-Path $LicDst "THIRD_PARTY_LICENSES.md") -Force
    Copy-Item (Join-Path $RepoRoot "README.md") (Join-Path $DistApp "README.md") -Force

    if ($BlenderDir -and (Test-Path $BlenderDir)) {
        $BlDst = Join-Path $DistApp "blender"
        Write-Host "  Copying portable Blender 5.3.0 from $BlenderDir..."
        if (-not (Test-Path $BlDst)) {
            $excludeFiles = if ($KeepDebugSymbols) { @() } else { @("blender.pdb") }
            robocopy $BlenderDir $BlDst /E /NFL /NDL /NJH /NJS /XF $excludeFiles | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "robocopy failed copying Blender ($LASTEXITCODE)" }
        }
        $BlLicSrc = Join-Path $BlenderDir "license"
        if (Test-Path $BlLicSrc) {
            Copy-Item $BlLicSrc (Join-Path $LicDst "blender") -Recurse -Force
        }
    } else {
        Write-Warning "BlenderDir '$BlenderDir' not found. Portable folder omits bundled blender\; user can pass --blender or ANIMFORGE_BLENDER."
    }

    Write-Host "[6/6] Computing SHA-256 checksums..."
    $SumFile = Join-Path $RepoRoot "dist\SHA256SUMS.txt"
    $TargetExes = @(Get-ChildItem $DistApp -File -Filter "*.exe") + @(Get-ChildItem $DistApp -File -Filter "*.md")
    $lines = foreach ($f in $TargetExes) {
        $h = (Get-FileHash $f.FullName -Algorithm SHA256).Hash.ToLower()
        "$h  $($f.Name)"
    }
    Set-Content -Path $SumFile -Value $lines -Encoding ASCII

    # Optional Inno Setup compile if ISCC.exe exists
    $iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if (-not $iscc) {
        $candIscc = Get-ChildItem "C:\Program Files*\Inno Setup*\ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($candIscc) { $iscc = $candIscc.FullName }
    } else {
        $iscc = $iscc.Source
    }

    if ($iscc) {
        Write-Host "[INSTALLER] Compiling Inno Setup installer via $iscc..."
        & $iscc (Join-Path $RepoRoot "packaging\installer.iss")
        if ($LASTEXITCODE -ne 0) { throw "ISCC failed." }
        $SetupExe = Join-Path $RepoRoot "dist\AnimForge_Setup.exe"
        if (Test-Path $SetupExe) {
            $sh = (Get-FileHash $SetupExe -Algorithm SHA256).Hash.ToLower()
            Add-Content -Path $SumFile -Value "$sh  AnimForge_Setup.exe" -Encoding ASCII
        }
    } else {
        Write-Warning "Inno Setup (ISCC.exe) is not installed; skipped compiling installer.iss. Portable folder is ready at $DistApp."
    }

    Write-Host "[BUILD COMPLETE] Outputs in $(Join-Path $RepoRoot 'dist')"
} finally {
    Pop-Location
}
