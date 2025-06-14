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
@REM --standalone ^
python -m nuitka ^
--onefile ^
--windows-icon-from-ico=UI\assets\logo.ico ^
--include-data-dir=UI\networking\protos=networking/protos ^
--include-data-dir=UI\templates=templates ^
--include-data-dir=UI\static=static ^
--include-data-dir=UI\assets=assets ^
--include-data-file=UI\assets\npcap-1.82.exe=assets/npcap-1.82.exe ^
--include-data-file=UI\assets\equipment_slots.json=assets/equipment_slots.json ^
--output-dir=dist ^
--include-module=clr ^
--remove-output ^
--assume-yes-for-downloads ^
--enable-plugin=no-qt ^
--nofollow-import-to=tkinter ^
--output-filename=DnDTools.exe ^
--windows-console-mode=disable ^
--lto=no ^
--jobs=4 ^
UI\app.py

echo Build complete. Executable is in the dist folder.
pause