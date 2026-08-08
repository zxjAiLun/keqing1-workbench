param(
    [string]$Venv = ".venv-win",
    [string]$KeqingCoreWheel = "",
    [string]$ReferenceVenv = "..\keqing1\.venv-win",
    [switch]$SkipUiBuild
)

$ErrorActionPreference = "Stop"
$Repo = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Repo

function Invoke-UvPip {
    param([string[]]$Packages)
    & uv pip install --python (Join-Path $Repo "$Venv\Scripts\python.exe") @Packages
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed" }
}

# 1. venv
if (-not (Test-Path (Join-Path $Repo "$Venv\Scripts\python.exe"))) {
    uv venv --python 3.12 $Venv
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
}

# 2. Python dependencies
Invoke-UvPip @(
    "fastapi>=0.135.2", "mahjong>=1.4.0", "numpy>=1.24", "python-dotenv>=1.2.2",
    "python-multipart>=0.0.20", "pyyaml>=6.0", "riichienv==0.4.8", "torch>=2.11.0",
    "uvicorn>=0.42.0", "websockets==10.2", "pytest>=9.0.2", "ruff>=0.15.10"
)

# 3. keqing_core runtime wheel (built by keqing-mortal; pass -KeqingCoreWheel
#    to override the default sibling path).
if (-not $KeqingCoreWheel) {
    $KeqingCoreWheel = "..\keqing-mortal\rust\keqing_core\target\wheels\keqing_core-0.1.0-cp312-cp312-win_amd64.whl"
}
if (-not (Test-Path $KeqingCoreWheel)) {
    throw "keqing_core wheel not found at $KeqingCoreWheel; build it in keqing-mortal or pass -KeqingCoreWheel"
}
Invoke-UvPip @($KeqingCoreWheel)

# 4. libriichi runtime bits (same reference-venv mechanism as keqing-mortal)
$Site = Join-Path $Repo "$Venv\Lib\site-packages"
if (-not (Test-Path (Join-Path $Site "libriichi\__init__.py"))) {
    $RefSite = Join-Path (Resolve-Path $ReferenceVenv -ErrorAction SilentlyContinue) "Lib\site-packages"
    if (-not (Test-Path (Join-Path $RefSite "libriichi\__init__.py"))) {
        throw "libriichi python package not found under $RefSite; pass -ReferenceVenv"
    }
    Copy-Item -Recurse -LiteralPath (Join-Path $RefSite "libriichi") -Destination (Join-Path $Site "libriichi")
    if (-not (Test-Path (Join-Path $Site "riichi.pyd"))) {
        Copy-Item -LiteralPath (Join-Path $RefSite "riichi.pyd") -Destination (Join-Path $Site "riichi.pyd")
    }
}

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
