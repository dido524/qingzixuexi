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
$curriculumCatalogDirectory = Join-Path $projectRoot 'src\qingzi_learning\curriculum\catalogs'
$curriculumCatalog = Join-Path $curriculumCatalogDirectory 'bnu_math_g5_upper_2024.json'
$curriculumGraphDirectory = Join-Path $projectRoot 'src\qingzi_learning\curriculum\graphs'
$curriculumGraph = Join-Path $curriculumGraphDirectory 'primary_math_v1.json'
$curriculumMappingDirectory = Join-Path $projectRoot 'src\qingzi_learning\curriculum\mappings'
$curriculumMapping = Join-Path $curriculumMappingDirectory 'bnu_math_g5_upper_2024.json'
$schemaDestination = 'qingzi_learning\schema'
$storageDestination = 'qingzi_learning\storage'
$curriculumDestination = 'qingzi_learning\curriculum\catalogs'
$curriculumGraphDestination = 'qingzi_learning\curriculum\graphs'
$curriculumMappingDestination = 'qingzi_learning\curriculum\mappings'

foreach ($required in @($python, $main, $iconSource, $versionFile, $strictSchema, $transportSchema, $reportNarrativeSchema, $examGenerationSchema, $examVerificationSchema, $storageSchema, $curriculumCatalog, $curriculumGraph, $curriculumMapping)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "构建输入缺失：$required"
    }
}

$previousPythonPath = $env:PYTHONPATH
Push-Location $projectRoot
try {
    # The shared virtualenv may be editable-installed against the parent checkout.
    # Always test and package this exact worktree's source tree.
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
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
        --add-data "$curriculumCatalogDirectory;$curriculumDestination" `
        --add-data "$curriculumGraphDirectory;$curriculumGraphDestination" `
        --add-data "$curriculumMappingDirectory;$curriculumMappingDestination" `
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
    if (-not (Test-Path -LiteralPath (Join-Path $internalRoot 'qingzi_learning\curriculum\catalogs\bnu_math_g5_upper_2024.json') -PathType Leaf)) {
        throw "打包检查失败：未包含数学课程目录。"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $internalRoot 'qingzi_learning\curriculum\graphs\primary_math_v1.json') -PathType Leaf)) {
        throw "打包检查失败：未包含数学知识图谱。"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $internalRoot 'qingzi_learning\curriculum\mappings\bnu_math_g5_upper_2024.json') -PathType Leaf)) {
        throw "打包检查失败：未包含数学课程映射。"
    }
    foreach ($dependency in @('PIL', 'cv2', 'tkinter', 'multiprocessing')) {
        if (-not (Test-Path -LiteralPath (Join-Path $internalRoot $dependency) -PathType Container)) {
            throw "打包检查失败：未包含依赖 $dependency"
        }
    }
    Write-Host "打包完成：$executable"
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
