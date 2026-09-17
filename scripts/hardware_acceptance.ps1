[CmdletBinding()]
param(
    [string]$SessionDirectory,
    [string]$ArchiveDirectory
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$knowledgeRoot = 'C:\晴子知识库\5th grade'
$installed = Join-Path $env:LOCALAPPDATA 'QingziLearningAssistant\app\晴子学习助手.exe'
$shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) '晴子学习助手.lnk'
$checks = [System.Collections.Generic.List[object]]::new()
$device = @(Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_BC15&PID_2C1B' -and $_.Class -eq 'Camera' })
$checks.Add([pscustomobject]@{check='target_camera'; passed=($device.Count -eq 1 -and $device[0].Status -eq 'OK'); detail=@($device | Select-Object Status,FriendlyName,InstanceId)})
$auth = & $python -c 'from qingzi_learning.analysis.codex_cli import resolve_codex_cli; print(resolve_codex_cli())'
$checks.Add([pscustomobject]@{check='codex_login'; passed=($LASTEXITCODE -eq 0); detail=($auth -join ' ')})
foreach ($subject in @('语文', '数学', '英语')) {
    $directory = Join-Path $knowledgeRoot $subject
    $exists = Test-Path -LiteralPath $directory -PathType Container
    $writable = $false
    if ($exists) {
        $probe = Join-Path $directory ('.acceptance-' + [guid]::NewGuid().ToString('N') + '.tmp')
        try {
            $stream = [IO.File]::Open($probe, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $stream.Dispose()
            $writable = $true
        } finally {
            if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe }
        }
    }
    $checks.Add([pscustomobject]@{check=('subject_' + $subject); passed=($exists -and $writable); detail=$directory})
}
if (Test-Path -LiteralPath $installed -PathType Leaf) {
    $process = Start-Process -FilePath $installed -ArgumentList '--smoke-check' -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(20000)) {
        Stop-Process -Id $process.Id -Force
        throw 'Installed smoke timed out'
    }
    $checks.Add([pscustomobject]@{check='installed_smoke'; passed=($process.ExitCode -eq 0); detail=(Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash})
} else {
    $checks.Add([pscustomobject]@{check='installed_smoke'; passed=$false; detail='not_installed'})
}
if (Test-Path -LiteralPath $shortcut) {
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    $checks.Add([pscustomobject]@{check='desktop_shortcut'; passed=($link.TargetPath -eq $installed); detail=$link.TargetPath})
} else {
    $checks.Add([pscustomobject]@{check='desktop_shortcut'; passed=$false; detail='missing'})
}
# Inspect evidence only. Never fabricate clicks, capture counts, retakes or analysis results.
if ($SessionDirectory -and $ArchiveDirectory) {
    & $python (Join-Path $PSScriptRoot 'verify_capture_evidence.py') $SessionDirectory $ArchiveDirectory
    $checks.Add([pscustomobject]@{check='ten_pages_and_retake'; passed=($LASTEXITCODE -eq 0); detail=$ArchiveDirectory})
} else {
    $checks.Add([pscustomobject]@{check='ten_pages_and_retake'; passed=$null; detail='Requires a safe test sheet, 10 actual captures, an earlier-page retake, and completed archive'})
}
$checks | ConvertTo-Json -Depth 5
if (@($checks | Where-Object { $_.passed -eq $false }).Count) { exit 1 }
