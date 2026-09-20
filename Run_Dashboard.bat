@echo off
title Content Verification Tool
echo ========================================================
echo   Launching Content Verification Tool
echo ========================================================
echo.

if not exist "env\Scripts\activate.bat" (
    echo [ERROR] Environment not found. Please run 1_Install_Dependencies.bat first.
    pause
    exit /b
)

call env\Scripts\activate.bat
echo [*] Starting Streamlit Application...
streamlit run app_ui.py --server.headless=false

pause