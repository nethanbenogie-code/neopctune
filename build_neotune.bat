@echo off
REM ============================================================
REM   NEO TUNE - Build to EXE  (run this ON WINDOWS)
REM   Produces: dist\NeoTune.exe
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo   ERROR: Python not found on PATH. Install Python 3.10+ from python.org
    echo   and tick "Add Python to PATH" during setup.
    pause
    exit /b 1
)
python --version

echo.
echo [2/4] Installing / updating build dependencies...
python -m pip install --upgrade pip
python -m pip install --upgrade pyinstaller customtkinter psutil pystray pillow
if errorlevel 1 (
    echo   ERROR: dependency install failed. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo [3/4] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo.
echo [4/4] Building NeoTune.exe ...
pyinstaller --noconfirm --clean NeoTune.spec
if errorlevel 1 (
    echo.
    echo   BUILD FAILED. Scroll up for the first red error line.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   DONE!  Your app is here:  dist\NeoTune.exe
echo   (It will ask for Administrator rights when launched.)
echo ============================================================
echo.
explorer dist
pause
