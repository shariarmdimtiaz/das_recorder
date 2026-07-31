@echo off
setlocal

cd /d "%~dp0"

echo.
echo DASRecorder Windows Firewall setup
echo This adds UDP rules for:
echo   inbound  8211 - Dunay command responses
echo   inbound  8227 - Dunay phase data
echo   outbound 8201 - Dunay commands
echo.
echo If this fails, right-click this file and choose "Run as administrator".
echo.

python run_app.py --configure-firewall
if errorlevel 1 (
    echo.
    echo Firewall setup failed.
    pause
    exit /b 1
)

echo.
echo Firewall setup complete.
pause
