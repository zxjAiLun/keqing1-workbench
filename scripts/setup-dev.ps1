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

# 1. Project environment.  uv project mode manages the env via pyproject.toml
#    + uv.lock, but we keep a single non-default environment (.venv-win) so it
#    never collides with uv's default .venv.  UV_PROJECT_ENVIRONMENT makes both
#    `uv sync` and `uv run` target .venv-win.
$env:UV_PROJECT_ENVIRONMENT = $Venv
if (-not (Test-Path $VenvPython)) {
    uv venv --python 3.12 $Venv
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
}

# 2. All ordinary Python dependencies from pyproject.toml (+ dev group for
#    pytest/ruff).  This is an exact sync, so it prunes keqing_core -- the
#    native runtime wheel is intentionally not a registry dependency and is
#    reinstalled in step 4 after the sync.
uv sync --group dev
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

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

# 4. keqing_core runtime wheel.  Must come AFTER uv sync, because the exact
#    sync above prunes it as extraneous.  Resolution order:
#    a. explicit -KeqingCoreWheel <path>
#    b. $RuntimeWheelDir (published by the keqing1_experiment setup)
#    c. clear error
if ($KeqingCoreWheel) {
    if (-not (Test-Path $KeqingCoreWheel)) { throw "keqing_core wheel not found: $KeqingCoreWheel" }
    $Wheel = Get-Item -LiteralPath $KeqingCoreWheel
} else {
    $Wheel = Get-ChildItem -Path $RuntimeWheelDir -Filter "keqing_core-*.whl" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $Wheel) {
        throw "keqing_core wheel not found: publish one into $RuntimeWheelDir (run the keqing1_experiment setup) or pass -KeqingCoreWheel <path>"
    }
}
& uv pip install --python $VenvPython $Wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "keqing_core wheel install failed" }
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

# 5b. Bootstrap smoke: verify the dependency combination actually imports and
#     the Workbench server can boot.  A green setup-dev.ps1 used to hide a
#     broken uvicorn/websockets pairing (uvicorn >= 0.52 needs websockets >= 11);
#     this makes that class of failure fail fast here instead of at first launch.
$SmokePort = 8123
try {
    & $VenvPython -c "import uvicorn, websockets, keqing_core, libriichi; print('smoke imports OK: uvicorn', uvicorn.__version__, '| websockets', websockets.__version__)"
    if ($LASTEXITCODE -ne 0) { throw "bootstrap import smoke failed" }

    $SmokeOut = Join-Path $Repo "logs\setup-smoke.out"
    $SmokeErr = Join-Path $Repo "logs\setup-smoke.err"
    $Proc = Start-Process -FilePath $VenvPython -ArgumentList @("workbench/main.py", "--port", "$SmokePort", "--no-ui-build", "local") `
        -WorkingDirectory $Repo -NoNewWindow -RedirectStandardOutput $SmokeOut -RedirectStandardError $SmokeErr -PassThru
    try {
        $SmokeUp = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            if ($Proc.HasExited) { break }
            try {
                $Resp = Invoke-WebRequest -Uri "http://127.0.0.1:$SmokePort/" -TimeoutSec 2 -UseBasicParsing
                if ($Resp.StatusCode -eq 200 -and $Resp.Content -match "<title>麻将回放分析</title>") {
                    $SmokeUp = $true
                    break
                }
            } catch {
                # still starting; retry
            }
        }
        if (-not $SmokeUp) {
            $Tail = Get-Content $SmokeErr -Tail 20 -ErrorAction SilentlyContinue
            throw "workbench launch smoke failed - server did not come up on port $SmokePort`n$($Tail -join [Environment]::NewLine)"
        }
        Write-Output "smoke launch OK: workbench/main.py local served on 127.0.0.1:$SmokePort"
    } finally {
        if (-not $Proc.HasExited) { Stop-Process -Id $Proc.Id -Force -ErrorAction SilentlyContinue }
    }
} finally {
    Remove-Item $SmokeOut, $SmokeErr -ErrorAction SilentlyContinue
}

"setup complete: $Venv"
