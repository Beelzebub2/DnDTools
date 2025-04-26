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
mkdir dist\data
mkdir dist\output

REM Run Nuitka to compile the application into a single-file executable
python -m nuitka --onefile --standalone --follow-imports --enable-plugin=pylint-warnings --include-data-dir="UI/networking/protos=networking/protos" --include-data-dir="UI/templates=templates" --include-data-dir="UI/static=static" --include-data-dir="UI/assets=assets" --include-data-dir="UI/data=data" --windows-icon-from-ico="UI/assets/logo.ico" --output-dir=dist --remove-output --assume-yes-for-downloads --output-filename=DnDTools.exe UI/app.py

echo Build complete. Executable is in the dist folder.
pause