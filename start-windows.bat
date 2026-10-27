@echo off
rem Double-click to start YT Media Toolkit on this computer (no Docker needed).
rem The first run installs Python, FFmpeg and Node.js if they are missing (via winget).
rem Public URL for other devices: run start-windows-tunnel.bat instead.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start.ps1" %*
echo.
pause
