@echo off
REM Build the app into a single-file executable using PyInstaller

REM Ensure we're in the project root
cd /d "%~dp0"

REM Set PYTHONPATH to include UI so networking.protos is recognized
set PYTHONPATH=%~dp0UI

REM Remove previous build/dist folders if they exist
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

REM Create dist directory and copy initial data
mkdir dist
mkdir dist\data
mkdir dist\output

REM to reduce .exe size, you can add the following line install UPX and move the line below
REM --upx-dir "C:\Users\USERNAME\upx-5.0.0-win64" ^

REM Run PyInstaller to compile the application into a directory
pyinstaller ^
--onefile ^
--paths "UI" ^
--hidden-import networking.protos ^
--add-data "UI\networking\protos;networking/protos" ^
--add-data "UI\templates;templates" ^
--add-data "UI\static;static" ^
--add-data "UI\assets;assets" ^
--add-data "UI\data;data" ^
--icon "UI\assets\logo.ico" ^
--name app ^
--distpath dist ^
--workpath build ^
UI\app.py

echo Build complete. Executable is in the dist folder.
pause