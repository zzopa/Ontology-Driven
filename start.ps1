$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw '缺少 .venv。请先按 README.md 安装项目依赖。'
}
$frontendExport = Join-Path $projectDir 'frontend\out\index.html'
if (-not (Test-Path -LiteralPath $frontendExport)) {
    throw '缺少前端构建产物。请先进入 frontend 执行 npm ci 和 npm run build。'
}
& $pythonExe (Join-Path $projectDir 'ask_web.py')
