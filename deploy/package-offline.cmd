@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0package-offline.ps1" %*
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
