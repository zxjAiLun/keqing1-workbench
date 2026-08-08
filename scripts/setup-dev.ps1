param(
    [string]$Venv = ".venv-win",
    [string]$KeqingCoreWheel = "",
    [switch]$SkipUiBuild
)

$ErrorActionPreference = "Stop"
$Repo = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Repo

$DataRoot = if ($env:KEQING_DATA_ROOT) { $env:KEQING_DATA_ROOT } else { Join-Path (Resolve-Path (Join-Path $Repo "..\..")) "keqing-data" }
$RuntimeWheelDir = Join-Path $DataRoot "runtime\keqing_core"
$VenvPython = Join-Path $Repo "$Venv\Scripts\python.exe"
$Site = Join-Path $Repo "$Venv\Lib\site-packages"

function Invoke-UvPip {
    param([string[]]$Packages)
    & uv pip install --python $VenvPython @Packages
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed" }
}

# 1. venv
if (-not (Test-Path $VenvPython)) {
    uv venv --python 3.12 $Venv
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
}

# 2. Python dependencies
Invoke-UvPip @(
    "fastapi>=0.135.2", "mahjong>=1.4.0", "numpy>=1.24", "python-dotenv>=1.2.2",
    "python-multipart>=0.0.20", "pyyaml>=6.0", "riichienv==0.4.8", "torch>=2.11.0",
    "uvicorn>=0.42.0", "websockets==10.2", "pytest>=9.0.2", "ruff>=0.15.10"
)

# 2b. Editable install so src/ packages (inference, static_tables, mahjong_env,
#     project_data) resolve from source.
Invoke-UvPip @("-e", ".")

# 3. libriichi runtime, built from the vendored Mortal crate (same as the
#    keqing1_experiment setup; requires cargo on PATH).
$env:PYO3_PYTHON = $VenvPython
cargo build --manifest-path (Join-Path $Repo "third_party\Mortal\Cargo.toml") -p libriichi --lib --release
if ($LASTEXITCODE -ne 0) { throw "libriichi cargo build failed" }
Copy-Item -LiteralPath (Join-Path $Repo "third_party\Mortal\target\release\riichi.dll") -Destination (Join-Path $Site "riichi.pyd") -Force
if (Test-Path (Join-Path $Site "libriichi")) { Remove-Item -Recurse -Force (Join-Path $Site "libriichi") }
Copy-Item -Recurse -LiteralPath (Join-Path $Repo "third_party\libriichi\libriichi") -Destination (Join-Path $Site "libriichi")
& $VenvPython -c "from libriichi.arena import OneVsThree; assert hasattr(OneVsThree, 'py_selfplay'); print('libriichi OK')"
if ($LASTEXITCODE -ne 0) { throw "libriichi install verification failed" }

# 4. keqing_core runtime wheel.  Resolution order:
#    a. explicit -KeqingCoreWheel <path>
#    b. already importable in this venv
#    c. $RuntimeWheelDir (published by the keqing1_experiment setup)
#    d. clear error
$Installed = $false
try {
    & $VenvPython -c "import keqing_core" 2>$null
    if ($LASTEXITCODE -eq 0) { $Installed = $true }
} catch {
    $Installed = $false
}
if ($KeqingCoreWheel) {
    if (-not (Test-Path $KeqingCoreWheel)) { throw "keqing_core wheel not found: $KeqingCoreWheel" }
    Invoke-UvPip @($KeqingCoreWheel)
} elseif ($Installed) {
    Write-Output "keqing_core already installed; reusing it"
} else {
    $Wheel = Get-ChildItem -Path $RuntimeWheelDir -Filter "keqing_core-*.whl" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $Wheel) {
        throw "keqing_core wheel not found: publish one into $RuntimeWheelDir (run the keqing1_experiment setup) or pass -KeqingCoreWheel <path>"
    }
    Invoke-UvPip @($Wheel.FullName)
}
& $VenvPython -c "import keqing_core; print('keqing_core OK (rust available:', keqing_core.is_available(), ')')"
if ($LASTEXITCODE -ne 0) { throw "keqing_core verification failed" }

# 5. Replay UI
if (-not $SkipUiBuild) {
    Push-Location (Join-Path $Repo "workbench\replay_ui")
    try {
        if (-not (Test-Path "node_modules")) { npm install }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "replay UI build failed" }
    } finally {
        Pop-Location
    }
}

"setup complete: $Venv"
