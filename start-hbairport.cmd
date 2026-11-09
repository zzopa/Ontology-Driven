@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 scripts\service\data_source.py hbairport
if errorlevel 1 goto failed
set "HBASK_DATA_SOURCE=hbairport"
call restart.cmd
exit /b
:failed
pause
