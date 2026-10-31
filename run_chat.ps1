# .\run_chat.ps1               merged chat (Twitch + YouTube)
# .\run_chat.ps1 -Demo         fake messages, to place the overlay in OBS
# .\run_chat.ps1 -AuthYouTube  YouTube login in the browser
# .\run_chat.ps1 -Check        check config and login files
# .\run_chat.ps1 -TestYouTube  read any public YouTube live stream's chat (asks for the link)
# .\run_chat.ps1 -Record       merged chat, also saving raw chat to the recordings folder
# .\run_chat.ps1 -Replay "recordings\chat_....jsonl" [-Speed 2]   play a recording into the overlay
param([switch]$Demo, [switch]$AuthYouTube, [switch]$Check, [switch]$TestYouTube, [string]$YouTubeUrl = "",
      [switch]$Record, [string]$Replay = "", [double]$Speed = 1)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) { throw "Not set up yet. Double-click 'Setup.bat' first." }

$cmd = "run"
if ($Demo) { $cmd = "demo" }
if ($AuthYouTube) { $cmd = "auth-youtube" }
if ($Check) { $cmd = "check" }

$extra = @()
if ($Record) { $extra += "--record" }
if ($Replay) {
    if (-not (Test-Path -LiteralPath $Replay)) { throw "Recording not found: '$Replay'" }
    $cmd = "replay"
    $extra += @((Resolve-Path -LiteralPath $Replay).Path, "--speed", "$Speed")
}
if ($TestYouTube -and -not $YouTubeUrl) {
    Write-Host "YouTube TEST MODE: reads the chat of any public live stream instead of yours."
    Write-Host "Open the live stream on YouTube, click Share, copy the link, paste it here."
    $YouTubeUrl = (Read-Host "YouTube live link").Trim()
    if (-not $YouTubeUrl) { throw "No link entered." }
}
if ($YouTubeUrl) { $extra += @("--youtube-url", $YouTubeUrl) }

Push-Location -LiteralPath $Root
try {
    & "$Py" -m chatmerge "$cmd" @extra
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0) { throw "chatmerge $cmd stopped with code $code (see the message above)." }
