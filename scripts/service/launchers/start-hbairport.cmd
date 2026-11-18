@echo off
setlocal
for %%I in ("%~dp0..\..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
".venv\Scripts\python.exe" -X utf8 scripts\service\data_source.py hbairport
if errorlevel 1 goto failed
set "HBASK_DATA_SOURCE=hbairport"
call "%PROJECT_ROOT%\restart.cmd"
exit /b
:failed
pause
