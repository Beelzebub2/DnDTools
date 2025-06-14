@echo off
REM Build the app into a single-file executable using Nuitka

REM Ensure we're in the project root
cd /d "%~dp0"

REM Set PYTHONPATH to include UI so networking.protos is recognized
set PYTHONPATH=%~dp0UI

REM Remove previous build/dist folders if they exist
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

REM Create dist directory and copy initial data
mkdir dist

REM Run Nuitka to compile the application into a single-file executable
pyinstaller ^
  --onefile ^
  --noconsole ^
  --icon=UI\assets\logo.ico ^
  --add-data "UI\networking\protos;networking/protos" ^
  --add-data "UI\templates;templates" ^
  --add-data "UI\static;static" ^
  --add-data "UI\assets;assets" ^
  --add-data "UI\assets\equipment_slots.json;assets" ^
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

echo Build complete. Executable is in the dist folder.