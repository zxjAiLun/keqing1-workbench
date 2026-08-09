# build_windows_launcher.ps1
# 将 windows_launcher.py 打包为单文件 exe
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File tools\build_windows_launcher.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$ScriptPath = Join-Path $ProjectRoot "workbench\tools\windows_launcher.py"
$VenvPython = Join-Path $ProjectRoot ".venv-win\Scripts\python.exe"
$OutputDir = Join-Path $ProjectRoot "dist"
$ExeName = "Keqing1WorkbenchLauncher"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Keqing1 Workbench Launcher 打包脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查 Python 环境
if (-not (Test-Path $VenvPython)) {
    Write-Host "[错误] 找不到 Python 虚拟环境: $VenvPython" -ForegroundColor Red
    Write-Host "请先创建虚拟环境: python -m venv .venv-win" -ForegroundColor Yellow
    exit 1
}

# 检查脚本文件
if (-not (Test-Path $ScriptPath)) {
    Write-Host "[错误] 找不到启动器脚本: $ScriptPath" -ForegroundColor Red
    exit 1
}

# 选择打包 Python
Write-Host "[1/3] 检查 PyInstaller..." -ForegroundColor Yellow
$PackPython = $VenvPython
$HasPyInstaller = (& $PackPython -c "import importlib.util; print(1 if importlib.util.find_spec('PyInstaller') else 0)").Trim() -eq "1"
if (-not $HasPyInstaller) {
    $SystemPython = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($SystemPython) {
        $SystemHasPyInstaller = (& $SystemPython -c "import importlib.util; print(1 if importlib.util.find_spec('PyInstaller') else 0)").Trim() -eq "1"
        if ($SystemHasPyInstaller) {
            $PackPython = $SystemPython
            $HasPyInstaller = $true
            Write-Host "      虚拟环境无 PyInstaller，使用系统 Python: $PackPython" -ForegroundColor Yellow
        }
    }
}

if (-not $HasPyInstaller) {
    Write-Host "[错误] 虚拟环境和系统 Python 均未安装 PyInstaller" -ForegroundColor Red
    Write-Host "可执行: python -m pip install pyinstaller" -ForegroundColor Yellow
    exit 1
}
Write-Host "      PyInstaller 已就绪" -ForegroundColor Green

# 清理旧的构建产物
Write-Host "[2/3] 清理旧构建..." -ForegroundColor Yellow
$BuildRoot = Join-Path $ProjectRoot "build"
$BuildDir = Join-Path $BuildRoot "windows_launcher"
$SpecFile = Join-Path $ProjectRoot "$ExeName.spec"

if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
if (Test-Path $SpecFile) { Remove-Item -Force $SpecFile }
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

# 执行打包
Write-Host "[3/3] 打包中..." -ForegroundColor Yellow
Write-Host ""

$PyinstallerArgs = @(
    "--onefile",
    "--noconsole",
    "--name", $ExeName,
    "--distpath", $OutputDir,
    "--workpath", $BuildDir,
    $ScriptPath
)

Write-Host "  命令: $PackPython -m PyInstaller $($PyinstallerArgs -join ' ')" -ForegroundColor Gray
Write-Host ""

& $PackPython -m PyInstaller @PyinstallerArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[错误] 打包失败" -ForegroundColor Red
    exit 1
}

# 清理构建临时文件
Write-Host ""
Write-Host "清理构建临时文件..." -ForegroundColor Gray
if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
if (Test-Path $SpecFile) { Remove-Item -Force $SpecFile }

# 完成
$ExePath = Join-Path $OutputDir "$ExeName.exe"
if (Test-Path $ExePath) {
    $Size = (Get-Item $ExePath).Length / 1MB
    Write-Host "========================================" -ForegroundColor Green
    Write-Host " 打包成功!" -ForegroundColor Green
    Write-Host " 输出: $ExePath" -ForegroundColor Green
    Write-Host " 大小: $([math]::Round($Size, 2)) MB" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "使用方法:" -ForegroundColor Cyan
    Write-Host "  将 $ExeName.exe 放到项目根目录双击运行" -ForegroundColor White
    Write-Host "  或直接在 dist/ 目录运行（会自动定位项目根目录）" -ForegroundColor White
} else {
    Write-Host "[错误] 未找到输出文件: $ExePath" -ForegroundColor Red
    exit 1
}
