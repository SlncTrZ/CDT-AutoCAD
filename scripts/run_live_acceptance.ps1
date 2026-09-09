[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$ExpectedRelease = "2027",
    [string]$ProgId = "AutoCAD.Application.26.0"
)

$ErrorActionPreference = "Stop"

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "CDT-AutoCAD live acceptance must run on Windows."
}

if (-not $Python) {
    $localVenvPython = Join-Path $env:USERPROFILE ".venvs\CDT-AutoCAD\Scripts\python.exe"
    $Python = if (Test-Path $localVenvPython) { $localVenvPython } else { "python" }
}

if (-not (Get-Process -Name "acad" -ErrorAction SilentlyContinue)) {
    throw "AutoCAD is not running. Start the full AutoCAD application before the attach_only gate."
}

& $Python -c "import ezdxf, win32com.client, PIL, fastmcp, pytest; print('Python COM/test dependencies: OK')"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$env:CDT_AUTOCAD_LIVE_TEST = "1"
$env:CDT_AUTOCAD_EXPECT_RELEASE = $ExpectedRelease
$env:CDT_AUTOCAD_COM_PROGID = $ProgId

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$evidenceRoot = Join-Path (Get-Location) "artifacts/live-acceptance"
$coreBaseTemp = Join-Path $evidenceRoot "pytest-core-$timestamp"
$advancedBaseTemp = Join-Path $evidenceRoot "pytest-advanced-dimensions-$timestamp"
$coreJunit = Join-Path $evidenceRoot "junit-core-$timestamp.xml"
$advancedJunit = Join-Path $evidenceRoot "junit-advanced-dimensions-$timestamp.xml"
New-Item -ItemType Directory -Path $evidenceRoot -Force | Out-Null

Write-Host "CDT-AutoCAD live acceptance"
Write-Host "  Expected release: $ExpectedRelease"
Write-Host "  ProgID:           $ProgId"
Write-Host "  Evidence:         $evidenceRoot"

& $Python -m pytest -q `
    tests/test_com_backend.py `
    tests/test_com_3d.py `
    --basetemp $coreBaseTemp `
    --junitxml $coreJunit
$coreExitCode = $LASTEXITCODE

& $Python -m pytest -q `
    tests/test_advanced_dimensions.py `
    --basetemp $advancedBaseTemp `
    --junitxml $advancedJunit
$advancedExitCode = $LASTEXITCODE

if ($coreExitCode -eq 0) {
    Write-Host "A2/A3.1 native gate: PASS"
} else {
    Write-Host "A2/A3.1 native gate: FAIL ($coreExitCode)"
}
if ($advancedExitCode -eq 0) {
    Write-Host "A3.2 advanced-dimension native gate: PASS"
} else {
    Write-Host "A3.2 advanced-dimension native gate: FAIL ($advancedExitCode)"
}

if (($coreExitCode -eq 0) -and ($advancedExitCode -eq 0)) {
    Write-Host "All current native gates PASS. Preserve the evidence directory for review."
    exit 0
}

Write-Host "One or more native gates FAILED. Review the separate JUnit reports before changing capability state."
exit 1
