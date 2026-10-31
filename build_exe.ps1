# Builds the no-Python-needed app: dist\chatmerge\chatmerge.exe plus launchers, and
# dist\chatmerge_app_v<version>.zip ready to hand to someone. Run on Windows:
#   powershell -NoProfile -ExecutionPolicy Bypass -File ".\build_exe.ps1"
# Uses its own .build-venv so the normal .venv is never touched. Never packs secrets.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$BuildVenv = Join-Path $Root ".build-venv"
$Py = Join-Path $BuildVenv "Scripts\python.exe"
$Dist = Join-Path $Root "dist"
$App = Join-Path $Dist "chatmerge"

function Invoke-Checked([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

function Find-Python {
    # Local to this function: a Python stub that writes to stderr (like the Microsoft Store
    # placeholder) must mean "try the next one", not stop the whole script.
    $ErrorActionPreference = "Continue"
    foreach ($candidate in @(@("py", "-3"), @("python"))) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $extra = @($candidate | Select-Object -Skip 1)
        $ver = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -and [version]$ver -ge [version]"3.11") { return , $candidate }
    }
    throw "Python 3.11 or newer not found."
}

Push-Location -LiteralPath $Root
try {
    if (-not (Test-Path -LiteralPath $Py)) {
        $found = Find-Python
        $exe = $found[0]
        $extra = @($found | Select-Object -Skip 1)
        Write-Host "Creating build environment: `"$BuildVenv`""
        Invoke-Checked "Creating the build environment" { & $exe @extra -m venv "$BuildVenv" }
    }
    Invoke-Checked "Installing build dependencies" { & "$Py" -m pip install --disable-pip-version-check -q -r "requirements-build.txt" }
    Invoke-Checked "Generating YouTube code" { & "$Py" -c "from chatmerge import protogen; protogen.load()" }
    Invoke-Checked "Self-tests" { & "$Py" -m unittest discover -s "tests" -t "." }
    $version = (& "$Py" -c "import chatmerge; print(chatmerge.__version__)").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $version) { throw "Could not read the app version" }

    Write-Host "Building chatmerge $version ..."
    Invoke-Checked "PyInstaller" { & "$Py" -m PyInstaller --noconfirm --clean --log-level WARN --distpath "$Dist" --workpath (Join-Path $Root "build") "packaging\chatmerge.spec" }

    Copy-Item -LiteralPath (Join-Path $Root "config.toml") -Destination $App -Force
    Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination $App -Force
    Copy-Item -LiteralPath (Join-Path $Root "preview.png") -Destination $App -Force
    New-Item -ItemType Directory -Path (Join-Path $App "emotes") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $Root "emotes\README.txt") -Destination (Join-Path $App "emotes") -Force
    Get-ChildItem -LiteralPath (Join-Path $Root "packaging\launchers") -Filter "*.bat" | Copy-Item -Destination $App -Force

    if (Test-Path -LiteralPath (Join-Path $App "secrets")) { throw "A secrets folder ended up in `"$App`". Not zipping." }
    $reported = (& (Join-Path $App "chatmerge.exe") --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $reported -ne "chatmerge $version") { throw "Built app reports '$reported', expected 'chatmerge $version'" }

    $zip = Join-Path $Dist "chatmerge_app_v$version.zip"
    Compress-Archive -Path $App -DestinationPath $zip -Force
    if (-not (Test-Path -LiteralPath $zip)) { throw "Zip was not created: `"$zip`"" }
    Write-Host ""
    Write-Host "Built: `"$App\chatmerge.exe`" ($reported)"
    Write-Host "Zip:   `"$zip`""
} finally {
    Pop-Location
}
