@echo off
chcp 65001 >nul
echo [INFO] 正在以 ops_ask 数据源启动航班图谱问答服务...
set HBASK_DATA_SOURCE=ops_ask
set HBASK_HOST=0.0.0.0
call .venv\Scripts\python.exe -X utf8 ask_web.py
pause
