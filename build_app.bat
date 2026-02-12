@echo off
REM Build the application and updater bundles using PyInstaller

REM Ensure we're in the project root
cd /d "%~dp0"

REM Set PYTHONPATH to include UI so networking.protos is recognized
set PYTHONPATH=%~dp0UI

REM Remove previous build/dist folders if they exist
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

REM Create dist directory
mkdir dist

REM Ensure the icon pack is up to date before building
python UI\scripts\build_icons_pak.py
if %ERRORLEVEL% NEQ 0 goto :error

REM Stage assets without the raw icons directory for inclusion in the build
set ASSET_STAGING=build\assets_no_icons
if exist "%ASSET_STAGING%" rmdir /s /q "%ASSET_STAGING%"
robocopy "UI\assets" "%ASSET_STAGING%" /E /XD icons >nul
if %ERRORLEVEL% GEQ 8 goto :error

REM Build the main application using the spec file (all optimisations live there)
pyinstaller --noconfirm --distpath dist DnDTools.spec
if %ERRORLEVEL% NEQ 0 goto :error

REM Build the standalone updater using the spec file
pyinstaller --noconfirm --distpath dist update.spec
if %ERRORLEVEL% NEQ 0 goto :error

echo Build complete. Check the dist directory for the app bundle and updater executable.
goto :eof

:error
echo Build failed. Please review the log above for details.
exit /b 1