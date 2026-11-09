@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 scripts\service\data_source.py ops_ask
if errorlevel 1 goto failed
set "HBASK_DATA_SOURCE=ops_ask"
call restart.cmd
exit /b
:failed
pause
