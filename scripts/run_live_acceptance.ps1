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
$baseTemp = Join-Path $evidenceRoot "pytest-$timestamp"
$junit = Join-Path $evidenceRoot "junit-$timestamp.xml"
New-Item -ItemType Directory -Path $evidenceRoot -Force | Out-Null

Write-Host "CDT-AutoCAD live acceptance"
Write-Host "  Expected release: $ExpectedRelease"
Write-Host "  ProgID:           $ProgId"
Write-Host "  Evidence:         $evidenceRoot"

& $Python -m pytest -q `
    tests/test_com_backend.py `
    tests/test_com_3d.py `
    --basetemp $baseTemp `
    --junitxml $junit

$exitCode = $LASTEXITCODE
if ($exitCode -eq 0) {
    Write-Host "Live acceptance PASS. Preserve the evidence directory for review."
} else {
    Write-Host "Live acceptance FAILED with exit code $exitCode. Do not promote A2/A3 capability state."
}
exit $exitCode
