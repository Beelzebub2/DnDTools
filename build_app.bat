@echo off
REM Build the application and updater bundles using PyInstaller

REM Ensure we're in the project root
cd /d "%~dp0"

REM Set PYTHONPATH to include UI so networking.protos is recognized
set PYTHONPATH=%~dp0UI

REM Remove previous build/dist folders if they exist
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

REM Create dist directory and copy initial data
mkdir dist

REM Ensure the icon pack is up to date before building
python UI\scripts\build_icons_pak.py
if %ERRORLEVEL% NEQ 0 goto :error

REM Stage assets without the raw icons directory for inclusion in the build
set ASSET_STAGING=build\assets_no_icons
if exist "%ASSET_STAGING%" rmdir /s /q "%ASSET_STAGING%"
robocopy "UI\assets" "%ASSET_STAGING%" /E /XD icons >nul
if %ERRORLEVEL% GEQ 8 goto :error

REM Run pyinstaller to compile the application into an onedir build so resources stay alongside the executable
pyinstaller ^
  --noconfirm ^
  --onedir ^
  --noconsole ^
  --icon=UI\assets\logo.ico ^
  --add-data "UI\networking\protos;networking/protos" ^
  --add-data "UI\templates;templates" ^
  --add-data "UI\static;static" ^
  --add-data "%ASSET_STAGING%;assets" ^
  --name DnDTools ^
  --distpath dist ^
  --hidden-import=clr ^
  --hidden-import=asyncio.events ^
  --hidden-import=asyncio.windows_events ^
  --hidden-import=asyncio.windows_utils ^
  --hidden-import=pyshark.capture.live_capture ^
  --hidden-import=pyshark.capture.capture ^
  --hidden-import=pyshark.tshark.tshark ^
  --exclude-module=tkinter ^
  UI\app.py
if %ERRORLEVEL% NEQ 0 goto :error

pyinstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --icon=UI\assets\logo.ico ^
  --add-data "UI\assets\logo.ico;assets" ^
  --name update ^
  --distpath dist ^
  UI\update.py
if %ERRORLEVEL% NEQ 0 goto :error

echo Build complete. Check the dist directory for the app bundle and updater executable.
goto :eof

:error
echo Build failed. Please review the log above for details.
exit /b 1