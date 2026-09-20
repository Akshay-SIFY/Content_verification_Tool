@echo off
title Install Dependencies - Content Verification Tool
echo ========================================================
echo   Setting up Content Verification Tool Environment
echo ========================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b
)

echo [*] Creating virtual environment (env)...
python -m venv env

echo [*] Activating environment and installing requirements...
call env\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo [SUCCESS] Installation complete! You can now run "Run_Dashboard.bat".
pause