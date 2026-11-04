@echo off
setlocal EnableExtensions
title Asset Downloader
rem Standalone launcher: save this one file anywhere and double-click it.
rem It downloads Asset Downloader from GitHub (and updates it when there is a new version),
rem keeps your settings (.env) and installed packages, then starts the app.
rem Public URL for your phone or other devices: run it with --tunnel (see README).

set "REPO=harshsainisuper/Asset-Downloader"
set "BRANCH=main"
set "APP=%LOCALAPPDATA%\AssetDownloader"
set "WORK=%TEMP%\AssetDownloader-update"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -Command"

if exist "%APP%\" if not exist "%APP%\.version" (
  dir /b /a "%APP%" 2>nul | findstr "." >nul && (
    echo [setup] ERROR: "%APP%" already exists and was not created by this launcher.
    echo [setup] Rename or delete that folder, then run this file again.
    pause
    exit /b 1
  )
)

echo [setup] Checking GitHub for the latest version ...
set "LATEST="
for /f "usebackq delims=" %%S in (`%PS% "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; (Invoke-RestMethod -UseBasicParsing -Headers @{'User-Agent'='asset-downloader-launcher'} -Uri ('https://api.github.com/repos/' + $env:REPO + '/commits/' + $env:BRANCH)).sha" 2^>nul`) do set "LATEST=%%S"

set "CURRENT="
if exist "%APP%\.version" set /p CURRENT=<"%APP%\.version"

if not defined LATEST (
  if exist "%APP%\start-windows.bat" (
    echo [setup] GitHub could not be reached; starting the version already on this computer.
    goto run
  )
  echo [setup] ERROR: Could not reach GitHub to download the app. Check your internet connection and try again.
  pause
  exit /b 1
)

if /i "%LATEST%"=="%CURRENT%" if exist "%APP%\start-windows.bat" (
  echo [setup] Already up to date.
  goto run
)

echo [setup] Downloading version %LATEST:~0,7% ...
if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%" || goto download_failed
set "SHA=%LATEST%"
%PS% "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; $ProgressPreference='SilentlyContinue'; $zip = Join-Path $env:WORK 'app.zip'; Invoke-WebRequest -UseBasicParsing -Uri ('https://github.com/' + $env:REPO + '/archive/' + $env:SHA + '.zip') -OutFile $zip; Expand-Archive -LiteralPath $zip -DestinationPath $env:WORK -Force"
if errorlevel 1 goto download_failed

set "SRC="
for /d %%D in ("%WORK%\Asset-Downloader-*") do set "SRC=%%D"
if not defined SRC goto download_failed
if not exist "%SRC%\start-windows.bat" goto download_failed

echo [setup] Installing into %APP% ...
if not exist "%APP%\" mkdir "%APP%" || goto download_failed
rem Marks the folder as ours before copying, so an interrupted install is simply redone.
>"%APP%\.version" echo installing
rem /MIR makes the folder match the download; excluded items (your settings, installed
rem packages, temp files, this launcher if it runs from there) are neither copied nor deleted.
robocopy "%SRC%" "%APP%" /MIR /XD .venv node_modules tmp /XF .env .version Asset-Downloader.bat /R:2 /W:2 /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto download_failed
>"%APP%\.version" echo %LATEST%
rmdir /s /q "%WORK%" 2>nul

:run
echo [setup] App folder: %APP%
echo [setup] Settings file: %APP%\.env (created on first start)
call "%APP%\start-windows.bat" %*
exit /b %errorlevel%

:download_failed
echo [setup] ERROR: Downloading or unpacking the app failed.
if exist "%APP%\start-windows.bat" (
  echo [setup] Starting the version already on this computer instead.
  goto run
)
pause
exit /b 1
