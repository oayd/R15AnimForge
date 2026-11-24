#Requires -Version 5.1
<#
.SYNOPSIS
    Parameterized Authenticode code-signing script for R15 AnimForge binaries & installer.
    Signs ONLY our built binaries (AnimForge.exe, AnimForge_Setup.exe) - never re-signs Blender binaries.
.NOTES
    Under CA/B Forum 2023 rules, OV/EV code-signing keys must live on FIPS-validated hardware tokens/HSMs
    or cloud signing services (e.g. Microsoft Azure Trusted Signing / Artifact Signing).
    Never self-sign and claim SmartScreen trust; even valid EV/OV Authenticode reputation builds over time.
.EXAMPLE
    .\packaging\sign.ps1 -Thumbprint "0123456789ABCDEF..." -TimestampUrl "http://timestamp.digicert.com"
.EXAMPLE
    .\packaging\sign.ps1 -CertSubject "My Publisher Name" -DryRun
#>
param(
    [string]$Thumbprint = "",
    [string]$CertSubject = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [string]$DistDir = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "dist"),
    [string]$SigntoolPath = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not $SigntoolPath) {
    $found = Get-ChildItem "C:\Program Files (x86)\Windows Kits" -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
             Where-Object { $_.FullName -match "\\x64\\" } |
             Sort-Object FullName -Descending | Select-Object -First 1
    if ($found) { $SigntoolPath = $found.FullName }
}
if (-not $SigntoolPath -or -not (Test-Path $SigntoolPath)) {
    throw "signtool.exe not found. Install Windows SDK Signing Tools or pass -SigntoolPath."
}
Write-Host "Using signtool: $SigntoolPath"

$targets = @()
$mainExe = Join-Path $DistDir "AnimForge\AnimForge.exe"
if (Test-Path $mainExe) { $targets += $mainExe }
$setupExe = Join-Path $DistDir "AnimForge_Setup.exe"
if (Test-Path $setupExe) { $targets += $setupExe }

if ($targets.Count -eq 0) {
    throw "No AnimForge-owned binaries found under '$DistDir'. Run packaging\build.ps1 first."
}

if (-not $Thumbprint -and -not $CertSubject -and -not $DryRun) {
    throw "Pass -Thumbprint <SHA1>, -CertSubject <CN>, or -DryRun to preview signing commands."
}

foreach ($file in $targets) {
    $signArgs = @("sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256")
    if ($Thumbprint) { $signArgs += @("/sha1", $Thumbprint) }
    elseif ($CertSubject) { $signArgs += @("/n", $CertSubject) }
    else { $signArgs += @("<CERT_SELECTION>") }
    $signArgs += $file

    if ($DryRun) {
        Write-Host "[DRY-RUN] `"$SigntoolPath`" $($signArgs -join ' ')"
        Write-Host "[DRY-RUN] `"$SigntoolPath`" verify /pa /v `"$file`""
        continue
    }

    Write-Host "Signing $file ..."
    & $SigntoolPath @signArgs
    if ($LASTEXITCODE -ne 0) { throw "signtool sign failed for $file ($LASTEXITCODE)" }

    Write-Host "Verifying $file ..."
    & $SigntoolPath verify /pa /v $file
    if ($LASTEXITCODE -ne 0) { throw "signtool verify /pa failed for $file ($LASTEXITCODE)" }
}
