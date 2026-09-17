[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$SourceDirectory,
    [string]$InstallRoot,
    [string]$DesktopDirectory,
    # Only exercised by automated rollback tests; regular installation always smokes the EXE.
    [switch]$SkipSmokeCheck,
    [ValidateSet('none', 'after_backup', 'after_install', 'after_shortcut')]
    [string]$TestFailureAt = 'none'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-AbsolutePath([string]$Path) {
    return [IO.Path]::GetFullPath($Path)
}

function Test-ReparsePoint([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    return [bool]((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Assert-SafeChildPath([string]$Candidate, [string]$Parent, [string]$Description) {
    $resolvedCandidate = Get-AbsolutePath $Candidate
    $resolvedParent = (Get-AbsolutePath $Parent).TrimEnd('\') + '\'
    if (-not $resolvedCandidate.StartsWith($resolvedParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Description 不在预期目录内：$resolvedCandidate"
    }
    if (Test-ReparsePoint $Parent) { throw "$Description 的父目录不能是重解析点。" }
    if (Test-ReparsePoint $resolvedCandidate) { throw "$Description 不能是重解析点。" }
    return $resolvedCandidate
}

function Remove-ScopedDirectory([string]$Directory, [string]$ApplicationRoot) {
    $checked = Assert-SafeChildPath $Directory $ApplicationRoot '待删除目录'
    if (Test-Path -LiteralPath $checked -PathType Container) {
        Remove-Item -LiteralPath $checked -Recurse -Force
    }
}

function Remove-ScopedShortcut([string]$Shortcut, [string]$DesktopPath) {
    $checked = Assert-SafeChildPath $Shortcut $DesktopPath '待删除快捷方式'
    if (Test-Path -LiteralPath $checked -PathType Leaf) {
        Remove-Item -LiteralPath $checked -Force
    }
}

function Test-DeliveryPackage([string]$Directory) {
    $package = (Resolve-Path -LiteralPath $Directory -ErrorAction Stop).Path
    if (Test-ReparsePoint $package) { throw "构建目录不能是重解析点：$package" }
    $executable = Join-Path $package '晴子学习助手.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf) -or (Test-ReparsePoint $executable)) {
        throw "未找到可执行文件：$executable"
    }
    if ((Get-Item -LiteralPath $executable).Length -le 0) { throw "可执行文件为空：$executable" }
    if ([string]::IsNullOrWhiteSpace((Get-Item -LiteralPath $executable).VersionInfo.FileVersion)) {
        throw "可执行文件没有版本信息：$executable"
    }
    $internal = Join-Path $package '_internal'
    foreach ($resource in @(
        'qingzi_learning\schema\analysis-result.schema.json',
        'qingzi_learning\schema\analysis-transport.schema.json',
        'qingzi_learning\storage\schema.sql'
    )) {
        $path = Join-Path $internal $resource
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Test-ReparsePoint $path)) {
            throw "构建资源缺失或不安全：$resource"
        }
    }
    foreach ($dependency in @('PIL', 'cv2', 'tkinter', 'multiprocessing')) {
        $path = Join-Path $internal $dependency
        if (-not (Test-Path -LiteralPath $path -PathType Container) -or (Test-ReparsePoint $path)) {
            throw "构建依赖缺失或不安全：$dependency"
        }
    }
    return $package
}

function Invoke-PackageSmoke([string]$Executable) {
    $process = Start-Process -FilePath $Executable -ArgumentList '--smoke-check' -PassThru
    if (-not $process.WaitForExit(20000)) {
        Stop-Process -Id $process.Id -Force
        throw "程序安全检查超时。"
    }
    if ($process.ExitCode -ne 0) { throw "程序安全检查失败，退出码：$($process.ExitCode)" }
}

function Set-DesktopShortcut([string]$ShortcutPath, [string]$Executable, [string]$WorkingDirectory) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = Get-AbsolutePath $Executable
    $shortcut.WorkingDirectory = Get-AbsolutePath $WorkingDirectory
    $shortcut.IconLocation = "$(Get-AbsolutePath $Executable),0"
    $shortcut.Description = '连续拍摄作业并更新晴子知识库'
    $shortcut.Save()
    if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) {
        throw "无法创建桌面快捷方式：$ShortcutPath"
    }
}

function Throw-InjectedFailure([string]$Phase) {
    if ($TestFailureAt -eq $Phase) { throw "INJECTED_INSTALL_FAILURE:$Phase" }
}

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($SourceDirectory)) {
    $SourceDirectory = Join-Path $projectRoot 'dist\晴子学习助手'
}
if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $InstallRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'QingziLearningAssistant'
}
if ([string]::IsNullOrWhiteSpace($DesktopDirectory)) {
    $DesktopDirectory = [Environment]::GetFolderPath('Desktop')
}

$applicationRoot = Get-AbsolutePath $InstallRoot
if ($applicationRoot.TrimEnd('\') -eq [IO.Path]::GetPathRoot($applicationRoot).TrimEnd('\')) {
    throw "安装根目录不能是磁盘根目录。"
}
New-Item -ItemType Directory -Path $applicationRoot -Force | Out-Null
if (Test-ReparsePoint $applicationRoot) { throw "安装根目录不能是重解析点。" }
New-Item -ItemType Directory -Path $DesktopDirectory -Force | Out-Null
if (Test-ReparsePoint $DesktopDirectory) { throw "桌面目录不能是重解析点。" }

$installDirectory = Assert-SafeChildPath (Join-Path $applicationRoot 'app') $applicationRoot '安装目录'
$desktop = Get-AbsolutePath $DesktopDirectory
$shortcutPath = Assert-SafeChildPath (Join-Path $desktop '晴子学习助手.lnk') $desktop '桌面快捷方式'

if ($Uninstall) {
    Remove-ScopedShortcut $shortcutPath $desktop
    Remove-ScopedDirectory $installDirectory $applicationRoot
    Write-Host "已移除程序和桌面快捷方式；知识库与暂存资料未触碰。"
    exit 0
}

$sourceDirectory = Test-DeliveryPackage $SourceDirectory
$nonce = [Guid]::NewGuid().ToString('N')
$staging = Assert-SafeChildPath (Join-Path $applicationRoot ('.app-staging-' + $nonce)) $applicationRoot '安装暂存目录'
$backup = Assert-SafeChildPath (Join-Path $applicationRoot ('.app-backup-' + $nonce)) $applicationRoot '安装备份目录'
$shortcutBackup = Assert-SafeChildPath (Join-Path $applicationRoot ('.shortcut-backup-' + $nonce + '.lnk')) $applicationRoot '快捷方式备份'
$oldInstallMoved = $false
$newInstallMoved = $false
$shortcutAttempted = $false
$oldShortcut = Test-Path -LiteralPath $shortcutPath -PathType Leaf
$success = $false

try {
    if ($oldShortcut) {
        if (Test-ReparsePoint $shortcutPath) { throw "现有快捷方式不能是重解析点。" }
        Copy-Item -LiteralPath $shortcutPath -Destination $shortcutBackup -Force
    }
    Copy-Item -LiteralPath $sourceDirectory -Destination $staging -Recurse -Force
    Test-DeliveryPackage $staging | Out-Null
    if (Test-Path -LiteralPath $installDirectory -PathType Container) {
        Move-Item -LiteralPath $installDirectory -Destination $backup
        $oldInstallMoved = $true
    }
    Throw-InjectedFailure 'after_backup'
    Move-Item -LiteralPath $staging -Destination $installDirectory
    $newInstallMoved = $true
    Test-DeliveryPackage $installDirectory | Out-Null
    if (-not $SkipSmokeCheck) { Invoke-PackageSmoke (Join-Path $installDirectory '晴子学习助手.exe') }
    Throw-InjectedFailure 'after_install'
    $shortcutAttempted = $true
    Set-DesktopShortcut $shortcutPath (Join-Path $installDirectory '晴子学习助手.exe') $installDirectory
    Throw-InjectedFailure 'after_shortcut'
    $success = $true
}
catch {
    $originalFailure = $_
    try {
        if ($newInstallMoved) { Remove-ScopedDirectory $installDirectory $applicationRoot }
        if ($oldInstallMoved -and (Test-Path -LiteralPath $backup -PathType Container)) {
            Move-Item -LiteralPath $backup -Destination $installDirectory
        }
        if ($shortcutAttempted) { Remove-ScopedShortcut $shortcutPath $desktop }
        if ($oldShortcut -and (Test-Path -LiteralPath $shortcutBackup -PathType Leaf)) {
            Copy-Item -LiteralPath $shortcutBackup -Destination $shortcutPath -Force
        }
    }
    catch {
        throw "安装失败且回滚未完成：$($_.Exception.Message)；原始错误：$($originalFailure.Exception.Message)"
    }
    throw $originalFailure
}
finally {
    if (Test-Path -LiteralPath $staging -PathType Container) { Remove-ScopedDirectory $staging $applicationRoot }
    if ($success) {
        if (Test-Path -LiteralPath $backup -PathType Container) { Remove-ScopedDirectory $backup $applicationRoot }
        if (Test-Path -LiteralPath $shortcutBackup -PathType Leaf) { Remove-Item -LiteralPath $shortcutBackup -Force }
    }
}

Write-Host "安装完成：$(Join-Path $installDirectory '晴子学习助手.exe')"
