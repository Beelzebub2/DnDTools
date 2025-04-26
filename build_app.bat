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

REM to reduce .exe size, you can add the following line if you install UPX and move the line below
REM --onefile --windows-icon-from-ico=UI\assets\logo.ico --standalone --enable-plugin=tk-inter --include-data-dir=UI\networking\protos=networking/protos --include-data-dir=UI\templates=templates --include-data-dir=UI\static=static --include-data-dir=UI\assets=assets --include-data-dir=UI\data=data --output-dir=dist

REM Run Nuitka to compile the application into a single-file executable
python -m nuitka ^
--onefile ^
--standalone ^
--windows-icon-from-ico=UI\assets\logo.ico ^
--include-data-dir=UI\networking\protos=networking/protos ^
--include-data-dir=UI\templates=templates ^
--include-data-dir=UI\static=static ^
--include-data-dir=UI\assets=assets ^
--output-dir=dist ^
--enable-plugin=tk-inter ^
--remove-output ^
--assume-yes-for-downloads ^
--nofollow-import-to=tkinter ^
--output-filename=DnDTools.exe ^
UI\app.py

echo Build complete. Executable is in the dist folder.
pause