@echo off
setlocal

cd /d "%~dp0"

echo.
echo Building DASRecorder.exe
echo.

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo PyInstaller is not installed.
    echo Run this command first:
    echo.
    echo     python -m pip install pyinstaller
    echo.
    exit /b 1
)

python -m PyInstaller --clean --noconfirm DASRecorder.spec
if errorlevel 1 (
    echo.
    echo Build failed.
    exit /b 1
)

if not exist "dist\DASRecorder\config" mkdir "dist\DASRecorder\config"
copy /Y "config\default_config.json" "dist\DASRecorder\config\default_config.json" >nul

echo.
echo Build complete:
echo     dist\DASRecorder\DASRecorder.exe
echo.
echo Copy the whole dist\DASRecorder folder to another PC.
echo.
