# One-time setup: private Python environment in .venv, dependencies, self-tests, config check.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Find-Python {
    # Tested versions first. "py -3" alone picks the NEWEST Python installed, which can be one
    # the pinned packages don't support yet (found on GitHub's build machine: Python 3.15).
    # Local to this function: a Python stub that writes to stderr (like the Microsoft Store
    # placeholder) must mean "try the next one", not stop the whole script.
    $ErrorActionPreference = "Continue"
    $candidates = @(@("py", "-3.12"), @("py", "-3.13"), @("py", "-3.11"), @("py", "-3.14"), @("python"))
    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $extra = @($candidate | Select-Object -Skip 1)
        $ver = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -and [version]$ver -ge [version]"3.11" -and [version]$ver -le [version]"3.14") { return , $candidate }
    }
    throw "No supported Python found (3.11 to 3.14; 3.12 recommended). Install Python 3.12 from python.org (tick 'Add python.exe to PATH'), then run this again."
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
