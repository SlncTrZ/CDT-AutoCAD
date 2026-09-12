param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('session-probe', 'visual-style', 'acis-soak', 'mp2-current-identity')]
    [string]$Profile,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [string]$RepoRoot = 'H:\Develop\CDT-AutoCAD',
    [string]$PythonExe = "$env:USERPROFILE\.venvs\CDT-AutoCAD\Scripts\python.exe",
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not (Test-Path -LiteralPath $RepoRoot -PathType Container)) {
    throw "RepoRoot does not exist: $RepoRoot"
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "PythonExe does not exist: $PythonExe"
}
if ($TimeoutSeconds -le 0) {
    throw 'TimeoutSeconds must be positive'
}

$runId = [guid]::NewGuid().ToString('N')
$taskName = 'CDT-AutoCAD-' + $Profile + '-' + $runId.Substring(0, 8)
$taskEvidencePath = $OutputPath + '.' + $runId + '.task.json'
$runtimeRoot = Join-Path $RepoRoot 'artifacts\task-lifecycle'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$runnerPath = Join-Path $runtimeRoot ($runId + '.ps1')
$stdoutPath = Join-Path $runtimeRoot ($runId + '.stdout.txt')
$stderrPath = Join-Path $runtimeRoot ($runId + '.stderr.txt')
$runtimeEvidencePath = Join-Path $runtimeRoot ($runId + '.runtime.json')

$scriptPath = $null
$scriptArgs = @()
switch ($Profile) {
    'session-probe' {
        $scriptPath = Join-Path $RepoRoot 'scripts\probe_autocad_session.py'
        $scriptArgs = @('--output', $OutputPath, '--com-progid', 'AutoCAD.Application.26')
    }
    'visual-style' {
        $scriptPath = Join-Path $RepoRoot 'scripts\run_visual_style_acceptance.py'
        $scriptArgs = @('--output', $OutputPath, '--com-progid', 'AutoCAD.Application.26')
    }
    'acis-soak' {
        $scriptPath = Join-Path $RepoRoot 'scripts\run_acis_acceptance.py'
        $scriptArgs = @('--repo-root', $RepoRoot, '--output', $OutputPath, '--com-progid', 'AutoCAD.Application.26')
    }
    'mp2-current-identity' {
        $scriptPath = Join-Path $RepoRoot 'scripts\run_hot_reload_acceptance.py'
        $scriptArgs = @(
            '--repo-root', $RepoRoot,
            '--output', $OutputPath,
            '--success-cycles', '20',
            '--failure-cycles', '10',
            '--backend', 'com',
            '--require-bridge',
            '--com-progid', 'AutoCAD.Application.26'
        )
    }
}
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "Profile script does not exist: $scriptPath"
}

function Quote-PowerShellLiteral([string]$Value) {
    return "'" + $Value.Replace("'", "''") + "'"
}

$argumentLiteral = ($scriptArgs | ForEach-Object { Quote-PowerShellLiteral $_ }) -join ', '
$runner = @"
`$ErrorActionPreference = 'Continue'
`$env:PYTHONPATH = $(Quote-PowerShellLiteral (Join-Path $RepoRoot 'src'))
`$started = (Get-Date).ToString('o')
`$exitCode = 255
try {
    `$arguments = @($argumentLiteral)
    & $(Quote-PowerShellLiteral $PythonExe) $(Quote-PowerShellLiteral $scriptPath) @arguments 1> $(Quote-PowerShellLiteral $stdoutPath) 2> $(Quote-PowerShellLiteral $stderrPath)
    `$exitCode = `$LASTEXITCODE
} catch {
    (`$_ | Out-String) | Add-Content -LiteralPath $(Quote-PowerShellLiteral $stderrPath)
    `$exitCode = 254
} finally {
    `$runtime = [ordered]@{
        schema_version = 1
        profile = $(Quote-PowerShellLiteral $Profile)
        task_name = $(Quote-PowerShellLiteral $taskName)
        runner_process_id = `$PID
        session_id = (Get-Process -Id `$PID).SessionId
        started_at = `$started
        finished_at = (Get-Date).ToString('o')
        exit_code = `$exitCode
        output_path = $(Quote-PowerShellLiteral $OutputPath)
        stdout_path = $(Quote-PowerShellLiteral $stdoutPath)
        stderr_path = $(Quote-PowerShellLiteral $stderrPath)
    }
    `$runtime | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $(Quote-PowerShellLiteral $runtimeEvidencePath) -Encoding UTF8
}
exit `$exitCode
"@
Set-Content -LiteralPath $runnerPath -Value $runner -Encoding UTF8

$created = $false
$deleted = $false
$timedOut = $false
$runtime = $null
$startedAt = (Get-Date).ToString('o')
try {
    $taskCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $runnerPath + '"'
    & schtasks.exe /Create /TN $taskName /TR $taskCommand /SC ONCE /ST 23:59 /IT /RU $env:USERNAME /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks /Create failed with exit code $LASTEXITCODE"
    }
    $created = $true

    & schtasks.exe /Run /TN $taskName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks /Run failed with exit code $LASTEXITCODE"
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $runtimeEvidencePath -PathType Leaf)) {
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path -LiteralPath $runtimeEvidencePath -PathType Leaf)) {
        $timedOut = $true
        throw "Interactive task timed out after $TimeoutSeconds seconds"
    }
    $runtime = Get-Content -LiteralPath $runtimeEvidencePath -Raw | ConvertFrom-Json
} finally {
    if ($created) {
        & schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null
        $deleted = ($LASTEXITCODE -eq 0)
    }
    $stillPresent = $false
    try {
        $null = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        $stillPresent = $true
    } catch {
        $stillPresent = $false
    }

    $taskEvidence = [ordered]@{
        schema_version = 1
        finding_id = 'BS-G14'
        run_id = $runId
        profile = $Profile
        task_name = $taskName
        task_created = $created
        task_deleted = $deleted
        cleanup_verified = (-not $stillPresent)
        timed_out = $timedOut
        caller_session_id = (Get-Process -Id $PID).SessionId
        runner_session_id = if ($null -ne $runtime) { $runtime.session_id } else { $null }
        exit_code = if ($null -ne $runtime) { $runtime.exit_code } else { $null }
        output_path = $OutputPath
        stdout_path = $stdoutPath
        stderr_path = $stderrPath
        task_evidence_path = $taskEvidencePath
        started_at = $startedAt
        finished_at = (Get-Date).ToString('o')
        persistent_task_exception = 'CDT-AutoCAD-Marketing-Demo'
    }
    $parent = Split-Path -Parent $taskEvidencePath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $taskEvidence | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $taskEvidencePath -Encoding UTF8

    Remove-Item -LiteralPath $runnerPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $runtimeEvidencePath -Force -ErrorAction SilentlyContinue
}

if ($null -eq $runtime) {
    throw 'Interactive task produced no runtime evidence'
}
if ([int]$runtime.exit_code -ne 0) {
    throw "Interactive task failed with exit code $($runtime.exit_code); stderr: $stderrPath"
}
if (-not $deleted) {
    throw "Interactive task completed but Scheduled Task cleanup failed: $taskName"
}

Get-Content -LiteralPath $taskEvidencePath -Raw
