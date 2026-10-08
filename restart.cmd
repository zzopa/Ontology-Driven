@echo off
setlocal
chcp 65001 >nul
title hbairport - Restart
set "HBASK_RESTART_ROOT=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create([IO.File]::ReadAllText((Join-Path $env:HBASK_RESTART_ROOT 'scripts\service\restart.ps1'), [Text.Encoding]::UTF8))) -ProjectRoot $env:HBASK_RESTART_ROOT"
set "restart_result=%errorlevel%"
echo.
pause
exit /b %restart_result%
