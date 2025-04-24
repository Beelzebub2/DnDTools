@echo off
REM Navigate to script directory
cd /d %~dp0

REM Define paths
set MAIN_SCRIPT=UI\app.py
set OUTPUT_DIR=dist

REM Compile using Nuitka
python -m nuitka ^
    --onefile ^
    --output-dir=%OUTPUT_DIR% ^
    %MAIN_SCRIPT%

echo.
echo Compilation complete. Output located in %OUTPUT_DIR%.
pause
