@echo off
cd /d "%~dp0"
REM Same as Start Chat, but also saves raw chat to the "recordings" folder for testing.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_chat.ps1" -Record
echo.
pause
