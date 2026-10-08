param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference='Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$reloadScript = Join-Path $repoRoot 'artifacts\native-bridge-acceptance\reload_autocad_bridge.ps1'
$reloadResult = Join-Path $repoRoot 'artifacts\native-bridge-acceptance\reload-bridge-result.json'

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $reloadScript
$exitCode = $LASTEXITCODE
if (Test-Path -LiteralPath $reloadResult -PathType Leaf) {
    $parent = Split-Path -Parent $OutputPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Copy-Item -LiteralPath $reloadResult -Destination $OutputPath -Force
}
exit $exitCode
