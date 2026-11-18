# 启动 ops_ask 航班数据源模式
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
Set-Location $projectDir
Write-Host "[INFO] 正在以 ops_ask 数据源启动航班图谱问答服务 (8088端口)..." -ForegroundColor Cyan
$env:HBASK_DATA_SOURCE = "ops_ask"
$env:HBASK_HOST = "0.0.0.0"
& (Join-Path $projectDir '.venv\Scripts\python.exe') -X utf8 (Join-Path $projectDir 'ask_web.py')
