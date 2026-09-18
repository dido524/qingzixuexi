[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pyinstaller = Join-Path $projectRoot '.venv\Scripts\pyinstaller.exe'
$main = Join-Path $projectRoot 'src\qingzi_learning\main.py'
$iconSource = Join-Path $projectRoot 'scripts\create_icon.py'
$icon = Join-Path $projectRoot 'assets\qingzi-learning-assistant.ico'
$versionFile = Join-Path $projectRoot 'scripts\windows-version-info.txt'
$strictSchema = Join-Path $projectRoot 'src\qingzi_learning\schema\analysis-result.schema.json'
$transportSchema = Join-Path $projectRoot 'src\qingzi_learning\schema\analysis-transport.schema.json'
$reportNarrativeSchema = Join-Path $projectRoot 'src\qingzi_learning\schema\report-narrative.schema.json'
$examGenerationSchema = Join-Path $projectRoot 'src\qingzi_learning\schema\exam-generation.schema.json'
$examVerificationSchema = Join-Path $projectRoot 'src\qingzi_learning\schema\exam-verification.schema.json'
$storageSchema = Join-Path $projectRoot 'src\qingzi_learning\storage\schema.sql'
$schemaDestination = 'qingzi_learning\schema'
$storageDestination = 'qingzi_learning\storage'

foreach ($required in @($python, $main, $iconSource, $versionFile, $strictSchema, $transportSchema, $reportNarrativeSchema, $examGenerationSchema, $examVerificationSchema, $storageSchema)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "构建输入缺失：$required"
    }
}

Push-Location $projectRoot
try {
    if (-not $SkipTests) {
        & $python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "测试未通过，已停止打包。" }
    }

    & $python -m pip install -r (Join-Path $projectRoot 'requirements-build.txt')
    if ($LASTEXITCODE -ne 0) { throw "无法安装固定版本的打包依赖。" }

    & $python $iconSource
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $icon -PathType Leaf)) {
        throw "无法生成程序图标。"
    }

    & $pyinstaller --noconfirm --clean --windowed `
        --name '晴子学习助手' `
        --icon $icon `
        --version-file $versionFile `
        --collect-all cv2_enumerate_cameras `
        --collect-all PIL `
        --collect-all tkinter `
        --collect-all multiprocessing `
        --hidden-import tkinter `
        --hidden-import multiprocessing `
        --add-data "$strictSchema;$schemaDestination" `
        --add-data "$transportSchema;$schemaDestination" `
        --add-data "$reportNarrativeSchema;$schemaDestination" `
        --add-data "$examGenerationSchema;$schemaDestination" `
        --add-data "$examVerificationSchema;$schemaDestination" `
        --add-data "$storageSchema;$storageDestination" `
        $main
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败。" }

    $executable = Join-Path $projectRoot 'dist\晴子学习助手\晴子学习助手.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "未生成预期的可执行文件：$executable"
    }
    if ([string]::IsNullOrWhiteSpace((Get-Item -LiteralPath $executable).VersionInfo.FileVersion)) {
        throw "打包检查失败：可执行文件没有版本信息。"
    }
    $internalRoot = Join-Path $projectRoot 'dist\晴子学习助手\_internal'
    $schemaRoot = Join-Path $internalRoot 'qingzi_learning\schema'
    foreach ($schema in @('analysis-result.schema.json', 'analysis-transport.schema.json', 'report-narrative.schema.json', 'exam-generation.schema.json', 'exam-verification.schema.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $schemaRoot $schema) -PathType Leaf)) {
            throw "打包检查失败：未包含 Schema $schema"
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $internalRoot 'qingzi_learning\storage\schema.sql') -PathType Leaf)) {
        throw "打包检查失败：未包含资料库 Schema。"
    }
    foreach ($dependency in @('PIL', 'cv2', 'tkinter', 'multiprocessing')) {
        if (-not (Test-Path -LiteralPath (Join-Path $internalRoot $dependency) -PathType Container)) {
            throw "打包检查失败：未包含依赖 $dependency"
        }
    }
    Write-Host "打包完成：$executable"
}
finally {
    Pop-Location
}
