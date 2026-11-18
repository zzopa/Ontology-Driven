@echo off
setlocal
for %%I in ("%~dp0..\..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
"%PROJECT_ROOT%\.venv\Scripts\python.exe" -X utf8 scripts\db\acdm_snapshot.py --days 7
pause
