@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 scripts\service\data_source.py acdm
if errorlevel 1 goto failed
set "HBASK_DATA_SOURCE=acdm"
call restart.cmd
exit /b
:failed
pause
