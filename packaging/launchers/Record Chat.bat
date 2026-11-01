@echo off
cd /d "%~dp0"
REM Same as Start Chat, but also saves raw chat to the recordings folder for testing.
"%~dp0chatweave.exe" run --record
echo.
pause
