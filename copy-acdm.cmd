@echo off
cd /d "%~dp0"
"D:\home\airport-images-20260901\airport-server\.venv\Scripts\python.exe" -X utf8 scripts\db\acdm_snapshot.py --days 7
pause
