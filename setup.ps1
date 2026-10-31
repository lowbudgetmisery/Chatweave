# One-time setup: private Python environment in .venv, dependencies, self-tests, config check.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Find-Python {
    foreach ($candidate in @(@("py", "-3"), @("python"))) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $extra = @($candidate | Select-Object -Skip 1)
        $ver = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -and [version]$ver -ge [version]"3.11") { return , $candidate }
    }
    throw "Python 3.11 or newer not found. Install it from python.org (tick 'Add python.exe to PATH'), then run setup again."
}

if (-not (Test-Path -LiteralPath $Py)) {
    $found = Find-Python
    $exe = $found[0]
    $extra = @($found | Select-Object -Skip 1)
    Write-Host "Creating private Python environment: `"$Venv`""
    & $exe @extra -m venv "$Venv"
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment (exit $LASTEXITCODE)." }
}

& "$Py" -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed (exit $LASTEXITCODE)" }

& "$Py" -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Installing dependencies failed (exit $LASTEXITCODE)" }

Push-Location -LiteralPath $Root
try {
    & "$Py" -m unittest discover -s "tests" -t "."
    if ($LASTEXITCODE -ne 0) { throw "Self-tests failed (exit $LASTEXITCODE)" }
    & "$Py" -m chatmerge check
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -eq 2) { throw "config.toml needs editing (see CONFIG ERROR above)." }
if ($code -eq 1) { Write-Warning "Setup worked. YouTube files are still missing (listed above): follow README step 3, then double-click 'YouTube Login.bat'." }
if ($code -eq 0) { Write-Host "Setup OK. Double-click 'Start Chat.bat'." -ForegroundColor Green }
