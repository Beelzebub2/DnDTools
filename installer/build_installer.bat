@echo off
setlocal ENABLEDELAYEDEXPANSION

REM Ensure we are running from the repository root
cd /d "%~dp0.."

REM Step 1: Build the standalone executable
call build_app.bat
if errorlevel 1 goto :error

REM Step 2: Sync installer metadata with the application version
python installer\generate_version_include.py
if errorlevel 1 goto :error

REM Step 3: Locate the Inno Setup Compiler (ISCC.exe)
set "ISCC_EXE=%ISCC_PATH%"
if defined ISCC_EXE goto :build_installer

set "ISCC_CANDIDATE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "!ISCC_CANDIDATE!" set "ISCC_EXE=!ISCC_CANDIDATE!"

if not defined ISCC_EXE (
    set "ISCC_CANDIDATE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
    if exist "!ISCC_CANDIDATE!" set "ISCC_EXE=!ISCC_CANDIDATE!"
)

if not defined ISCC_EXE (
    echo Could not find ISCC.exe. Please install Inno Setup 6 and/or set the ISCC_PATH environment variable.
    goto :error
)

:build_installer
"%ISCC_EXE%" installer\DnDTools.iss
if errorlevel 1 goto :error

echo Inno Setup installer created successfully.
goto :eof

:error
echo Failed to create Inno Setup installer.
exit /b 1
