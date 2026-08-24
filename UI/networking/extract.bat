@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "PROTO_DIR=%SCRIPT_DIR%protos"
set "BACKUP_DIR=%SCRIPT_DIR%.protos-rollback"
set "STAGE_DIR=%SCRIPT_DIR%.protos-stage-%RANDOM%-%RANDOM%"
set "WORK_PROTO_DIR=%STAGE_DIR%"
set "GAME_EXE=%~1"

call :recover_previous
if errorlevel 1 exit /b 1

if not defined GAME_EXE (
    for %%G in (
        "C:\Program Files\IRONMACE\Dark and Darker\DungeonCrawler\Binaries\Win64\DungeonCrawler.exe"
        "C:\Program Files (x86)\Steam\steamapps\common\Dark and Darker\DungeonCrawler\Binaries\Win64\DungeonCrawler.exe"
        "D:\SteamLibrary\steamapps\common\Dark and Darker\DungeonCrawler\Binaries\Win64\DungeonCrawler.exe"
        "E:\SteamLibrary\steamapps\common\Dark and Darker\DungeonCrawler\Binaries\Win64\DungeonCrawler.exe"
        "F:\SteamLibrary\steamapps\common\Dark and Darker\DungeonCrawler\Binaries\Win64\DungeonCrawler.exe"
    ) do if not defined GAME_EXE if exist "%%~G" set "GAME_EXE=%%~G"
)

if not defined GAME_EXE (
    echo Dark and Darker was not found in a common install location.
    echo Usage: extract.bat "X:\path\to\DungeonCrawler.exe"
    exit /b 2
)

if not exist "%GAME_EXE%" (
    echo Game executable not found: "%GAME_EXE%"
    exit /b 2
)

where protodump.exe >nul 2>nul
if errorlevel 1 (
    echo protodump.exe was not found on PATH.
    exit /b 2
)

if not exist "%SCRIPT_DIR%protoc.exe" (
    echo Bundled compiler not found: "%SCRIPT_DIR%protoc.exe"
    exit /b 2
)

rem Extraction never overlays the live tree. A clean, uniquely named sibling
rem prevents schemas removed upstream from surviving into a new generation.
powershell.exe -NoProfile -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$root = [IO.Path]::GetFullPath($env:SCRIPT_DIR).TrimEnd([IO.Path]::DirectorySeparatorChar);" ^
    "$stage = [IO.Path]::GetFullPath($env:STAGE_DIR);" ^
    "if ([IO.Path]::GetDirectoryName($stage) -ne $root -or -not [IO.Path]::GetFileName($stage).StartsWith('.protos-stage-')) { throw 'Unsafe protobuf staging path' };" ^
    "if (Test-Path -LiteralPath $stage) { throw ('Protobuf staging path already exists: ' + $stage) };" ^
    "New-Item -ItemType Directory -Path $stage -ErrorAction Stop | Out-Null"
if errorlevel 1 exit /b 1

echo Extracting protobuf schemas from:
echo   %GAME_EXE%
protodump.exe -file "%GAME_EXE%" -output "%WORK_PROTO_DIR%"
if errorlevel 1 goto :fail

echo Regenerating Python protobuf modules with the bundled compiler...
set "FOUND_PROTO="
for %%F in ("%WORK_PROTO_DIR%\*.proto") do if exist "%%~fF" (
    set "FOUND_PROTO=1"
    call :compile_proto "%%~fF"
    if errorlevel 1 goto :fail
)
if not defined FOUND_PROTO (
    echo No protobuf schemas were extracted.
    goto :fail
)

echo Recording schema provenance...
powershell.exe -NoProfile -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$source = Get-Item -LiteralPath $env:GAME_EXE;" ^
    "$sha256 = [System.Security.Cryptography.SHA256]::Create();" ^
    "try {" ^
    "  $stream = [System.IO.File]::OpenRead($source.FullName);" ^
    "  try { $hash = [BitConverter]::ToString($sha256.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() } finally { $stream.Dispose() }" ^
    "} finally { $sha256.Dispose() };" ^
    "$manifest = [ordered]@{" ^
    "  game_executable = $source.Name;" ^
    "  game_version = $source.VersionInfo.FileVersion;" ^
    "  sha256 = $hash;" ^
    "  size = $source.Length;" ^
    "  last_write_utc = $source.LastWriteTimeUtc.ToString('o');" ^
    "  extracted_at_utc = [DateTime]::UtcNow.ToString('o');" ^
    "  proto_count = @(Get-ChildItem -LiteralPath $env:WORK_PROTO_DIR -Filter '*.proto' -File).Count" ^
    "};" ^
    "$json = $manifest | ConvertTo-Json;" ^
    "$utf8NoBom = New-Object System.Text.UTF8Encoding($false);" ^
    "[System.IO.File]::WriteAllText((Join-Path $env:WORK_PROTO_DIR '_source.json'), $json + [Environment]::NewLine, $utf8NoBom);" ^
    "[System.IO.File]::WriteAllText((Join-Path $env:WORK_PROTO_DIR '__init__.py'), '# Generated package marker for networking.protos.' + [Environment]::NewLine, $utf8NoBom)"
if errorlevel 1 goto :fail

echo Validating staged protobuf generation...
powershell.exe -NoProfile -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$root = [IO.Path]::GetFullPath($env:WORK_PROTO_DIR);" ^
    "$required = @('_PacketCommand.proto', 'Merchant.proto', '_PacketCommand_pb2.py', 'Merchant_pb2.py', '_source.json', '__init__.py');" ^
    "foreach ($name in $required) { $path = Join-Path $root $name; if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -le 0) { throw ('Required protobuf output is missing or empty: ' + $name) } };" ^
    "$protoFiles = @(Get-ChildItem -LiteralPath $root -Filter '*.proto' -File);" ^
    "if ($protoFiles.Count -lt 4) { throw ('Suspiciously small protobuf extraction: ' + $protoFiles.Count + ' schemas') };" ^
    "foreach ($proto in $protoFiles) { $generated = Join-Path $root ($proto.BaseName + '_pb2.py'); if (-not (Test-Path -LiteralPath $generated -PathType Leaf) -or (Get-Item -LiteralPath $generated).Length -le 0) { throw ('Generated module is missing or empty for ' + $proto.Name) } };" ^
    "$provenance = Get-Content -LiteralPath (Join-Path $root '_source.json') -Raw | ConvertFrom-Json;" ^
    "if ([int]$provenance.proto_count -ne $protoFiles.Count) { throw 'Provenance schema count does not match staged output' }"
if errorlevel 1 goto :fail

echo Promoting validated protobuf generation...
powershell.exe -NoProfile -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$root = [IO.Path]::GetFullPath($env:SCRIPT_DIR).TrimEnd([IO.Path]::DirectorySeparatorChar);" ^
    "$target = [IO.Path]::GetFullPath($env:PROTO_DIR);" ^
    "$backup = [IO.Path]::GetFullPath($env:BACKUP_DIR);" ^
    "$stage = [IO.Path]::GetFullPath($env:STAGE_DIR);" ^
    "foreach ($path in @($target, $backup, $stage)) { if ([IO.Path]::GetDirectoryName($path) -ne $root) { throw ('Unsafe protobuf promotion path: ' + $path) } };" ^
    "if ([IO.Path]::GetFileName($target) -ne 'protos' -or [IO.Path]::GetFileName($backup) -ne '.protos-rollback' -or -not [IO.Path]::GetFileName($stage).StartsWith('.protos-stage-')) { throw 'Unexpected protobuf promotion path name' };" ^
    "if (-not (Test-Path -LiteralPath $stage -PathType Container)) { throw 'Validated protobuf staging directory disappeared' };" ^
    "if (Test-Path -LiteralPath $backup) { throw 'A protobuf rollback directory already exists' };" ^
    "$preserved = $false;" ^
    "if (Test-Path -LiteralPath $target) { if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw 'Canonical protobuf path is not a directory' }; Move-Item -LiteralPath $target -Destination $backup -ErrorAction Stop; $preserved = $true };" ^
    "try { Move-Item -LiteralPath $stage -Destination $target -ErrorAction Stop } catch { if ($preserved -and -not (Test-Path -LiteralPath $target) -and (Test-Path -LiteralPath $backup)) { Move-Item -LiteralPath $backup -Destination $target -ErrorAction Stop }; throw };" ^
    "if ($preserved) { try { Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction Stop } catch { Write-Warning ('New protobuf generation is active; stale rollback cleanup will retry next run: ' + $_.Exception.Message) } }"
if errorlevel 1 goto :fail

echo Protobuf refresh complete.
exit /b 0

:compile_proto
for /l %%R in (1,1,3) do (
    "%SCRIPT_DIR%protoc.exe" --proto_path="%WORK_PROTO_DIR%" --python_out="%WORK_PROTO_DIR%" "%~1"
    if not errorlevel 1 exit /b 0
    if %%R LSS 3 (
        echo protoc write failed for "%~nx1"; retrying ^(%%R/3^)...
        powershell.exe -NoProfile -Command "Start-Sleep -Milliseconds 500" >nul 2>nul
    )
)
echo Failed to compile "%~1" after 3 attempts.
exit /b 1

:recover_previous
rem Recover the only unsafe interruption point in directory promotion: after
rem the old tree was preserved but before the staged tree reached `protos`.
powershell.exe -NoProfile -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$root = [IO.Path]::GetFullPath($env:SCRIPT_DIR).TrimEnd([IO.Path]::DirectorySeparatorChar);" ^
    "$target = [IO.Path]::GetFullPath($env:PROTO_DIR);" ^
    "$backup = [IO.Path]::GetFullPath($env:BACKUP_DIR);" ^
    "foreach ($path in @($target, $backup)) { if ([IO.Path]::GetDirectoryName($path) -ne $root) { throw ('Unsafe protobuf recovery path: ' + $path) } };" ^
    "if ([IO.Path]::GetFileName($target) -ne 'protos' -or [IO.Path]::GetFileName($backup) -ne '.protos-rollback') { throw 'Unexpected protobuf recovery path name' };" ^
    "if (Test-Path -LiteralPath $backup) { if (-not (Test-Path -LiteralPath $backup -PathType Container)) { throw 'Protobuf rollback path is not a directory' }; if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction Stop } else { Move-Item -LiteralPath $backup -Destination $target -ErrorAction Stop; Write-Host 'Recovered interrupted protobuf promotion.' } }"
exit /b %ERRORLEVEL%

:cleanup_stage
powershell.exe -NoProfile -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$root = [IO.Path]::GetFullPath($env:SCRIPT_DIR).TrimEnd([IO.Path]::DirectorySeparatorChar);" ^
    "$stage = [IO.Path]::GetFullPath($env:STAGE_DIR);" ^
    "if ([IO.Path]::GetDirectoryName($stage) -ne $root -or -not [IO.Path]::GetFileName($stage).StartsWith('.protos-stage-')) { throw 'Unsafe protobuf staging cleanup path' };" ^
    "if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction Stop }"
exit /b %ERRORLEVEL%

:fail
echo Protobuf refresh failed; the prior generated tree remains active.
call :cleanup_stage
call :recover_previous
exit /b 1
