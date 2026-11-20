<#
.SYNOPSIS
    本地（有网环境）一键构建 ops-ask 离线发布包，上传至服务器 /data/ops-ask 即可部署。

.EXAMPLE
    # 首次全量打包（构建前端 + 构建 linux/amd64 镜像 + 导出 tar + 生成离线压缩包）
    .\deploy\package-offline.ps1

.EXAMPLE
    # 首次全量打包并包含 Playwright Chromium（启用 PDF 报告后台）
    .\deploy\package-offline.ps1 -WithPlaywright

.EXAMPLE
    # 日常仅更新前后端代码（跳过耗时的 Docker 镜像构建，秒级打包）
    .\deploy\package-offline.ps1 -SkipImageBuild

.EXAMPLE
    # 首次迁移时连同本地 data/ops_ask/admin 账号管理库一起打包
    .\deploy\package-offline.ps1 -IncludeAdminData
#>
param(
    [string]$Tag = "1.0",
    [switch]$NoPlaywright,
    [switch]$SkipFrontendBuild,
    [switch]$SkipImageBuild,
    [switch]$IncludeAdminData,
    [string]$PipIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ImageName = "ops-ask-runtime:$Tag"
$ReleaseDir = Join-Path $Root "output\ops-ask-offline"
$ArchiveName = if ($SkipImageBuild) { "ops-ask-code-update-$Tag.tar.gz" } else { "ops-ask-offline-full-$Tag.tar.gz" }
$ArchivePath = Join-Path $Root "output\$ArchiveName"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " ops-ask 离线部署打包工具" -ForegroundColor Cyan
Write-Host " 项目目录: $Root"
Write-Host " 输出目录: $ReleaseDir"
Write-Host "==========================================" -ForegroundColor Cyan

# 1. 构建前端静态产物 frontend/out
if (-not $SkipFrontendBuild) {
    Write-Host "`n[1/4] 构建前端静态产物 (frontend/out) ..." -ForegroundColor Yellow
    Push-Location (Join-Path $Root "frontend")
    try {
        if (-not (Test-Path "node_modules")) {
            npm ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci 失败" }
        }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build 失败" }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "`n[1/4] 跳过前端构建，复用现有 frontend/out ..." -ForegroundColor DarkGray
}

if (-not (Test-Path (Join-Path $Root "frontend\out\index.html"))) {
    throw "缺少 frontend\out\index.html，请先去掉 -SkipFrontendBuild 执行前端构建。"
}

# 2. 准备临时打包目录
Write-Host "`n[2/4] 整理宿主机挂载文件 ..." -ForegroundColor Yellow
if (Test-Path $ReleaseDir) {
    Remove-Item -LiteralPath $ReleaseDir -Recurse -Force
}
New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null

# 顶层文件
Copy-Item (Join-Path $Root "ask_web.py") $ReleaseDir
Copy-Item (Join-Path $Root "config.json") $ReleaseDir
Copy-Item (Join-Path $Root "docker-compose.yml") $ReleaseDir
Copy-Item (Join-Path $Root "deploy\config.local.server.example.json") $ReleaseDir
if (Test-Path (Join-Path $Root "docs\ops-ask-deployment-manual.md")) {
    Copy-Item (Join-Path $Root "docs\ops-ask-deployment-manual.md") $ReleaseDir
}

# 确保 server-deploy.sh 为 Unix LF 换行符
$deploySh = [IO.File]::ReadAllText((Join-Path $Root "deploy\server-deploy.sh"), [Text.Encoding]::UTF8)
$deploySh = $deploySh -replace "`r`n", "`n"
$deploySh = $deploySh -replace 'ops-ask-runtime:1\.0', $ImageName
$deploySh = $deploySh -replace 'ops-ask-runtime-1\.0\.tar', "ops-ask-runtime-$Tag.tar"
[IO.File]::WriteAllText((Join-Path $ReleaseDir "server-deploy.sh"), $deploySh, (New-Object Text.UTF8Encoding($false)))

# 更新 docker-compose.yml 镜像标签
$composeText = [IO.File]::ReadAllText((Join-Path $ReleaseDir "docker-compose.yml"), [Text.Encoding]::UTF8)
$composeText = $composeText -replace 'ops-ask-runtime:1\.0', $ImageName
$composeText = $composeText -replace "`r`n", "`n"
[IO.File]::WriteAllText((Join-Path $ReleaseDir "docker-compose.yml"), $composeText, (New-Object Text.UTF8Encoding($false)))

# 复制后端代码 app/ 和 scripts/（排除 __pycache__ 和 *.pyc）
function Copy-CleanTree([string]$Src, [string]$Dst) {
    New-Item -ItemType Directory -Path $Dst -Force | Out-Null
    & robocopy $Src $Dst /E /XD __pycache__ .pytest_cache /XF *.pyc *.pyo /NFL /NDL /NJH /NJS /NC /NS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "复制目录失败: $Src -> $Dst (robocopy exit $LASTEXITCODE)" }
}

Copy-CleanTree (Join-Path $Root "app") (Join-Path $ReleaseDir "app")
Copy-CleanTree (Join-Path $Root "scripts") (Join-Path $ReleaseDir "scripts")
Copy-CleanTree (Join-Path $Root "frontend\out") (Join-Path $ReleaseDir "frontend\out")

# 复制运行必需的 data/ 文件（默认不含本地私有 SQLite 账号库，避免误覆盖生产库）
$DataDst = Join-Path $ReleaseDir "data"
$OpsAskDataDst = Join-Path $DataDst "ops_ask"
New-Item -ItemType Directory -Path $OpsAskDataDst -Force | Out-Null
Copy-Item (Join-Path $Root "data\business_permissions.json") $DataDst
Copy-Item (Join-Path $Root "data\ops_ask\profile.json") $OpsAskDataDst
Copy-Item (Join-Path $Root "data\ops_ask\ontology.json") $OpsAskDataDst
if (Test-Path (Join-Path $Root "data\ops_ask\memory.json")) {
    Copy-Item (Join-Path $Root "data\ops_ask\memory.json") $OpsAskDataDst
}
if ($IncludeAdminData -and (Test-Path (Join-Path $Root "data\ops_ask\admin"))) {
    Write-Host "      已开启 -IncludeAdminData：打包本地 data/ops_ask/admin" -ForegroundColor Magenta
    Copy-CleanTree (Join-Path $Root "data\ops_ask\admin") (Join-Path $OpsAskDataDst "admin")
}

# 3. 构建并导出离线 Docker 镜像（linux/amd64）
if (-not $SkipImageBuild) {
    Write-Host "`n[3/4] 构建 linux/amd64 运行环境镜像 ($ImageName) ..." -ForegroundColor Yellow
    $pwFlag = if ($NoPlaywright) { "false" } else { "true" }
    Push-Location $Root
    try {
        docker build `
            --platform linux/amd64 `
            --build-arg "PIP_INDEX_URL=$PipIndexUrl" `
            --build-arg "INSTALL_PLAYWRIGHT=$pwFlag" `
            -t $ImageName .
        if ($LASTEXITCODE -ne 0) { throw "docker build 失败，请确认本机 Docker Desktop/Engine 已启动" }

        $ImageTar = Join-Path $ReleaseDir "ops-ask-runtime-$Tag.tar"
        Write-Host "      导出离线镜像到 $ImageTar ..." -ForegroundColor Yellow
        docker save -o $ImageTar $ImageName
        if ($LASTEXITCODE -ne 0) { throw "docker save 失败" }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "`n[3/4] 跳过镜像构建（仅代码/前端增量更新包）" -ForegroundColor DarkGray
}

# 4. 打包成单个 .tar.gz 便于上传到服务器 /data/ops-ask
Write-Host "`n[4/4] 生成归档包 $ArchivePath ..." -ForegroundColor Yellow
if (Test-Path $ArchivePath) { Remove-Item -LiteralPath $ArchivePath -Force }
tar -czf $ArchivePath -C $ReleaseDir .
if ($LASTEXITCODE -ne 0) { throw "tar 压缩失败" }

$sizeMB = [math]::Round((Get-Item $ArchivePath).Length / 1MB, 1)
Write-Host "`n打包完成！" -ForegroundColor Green
Write-Host "归档文件: $ArchivePath ($sizeMB MB)" -ForegroundColor Green
Write-Host ""
Write-Host "服务器部署命令（请将文件上传到 /data/ops-ask）：" -ForegroundColor Cyan
Write-Host "  mkdir -p /data/ops-ask"
Write-Host "  tar -xzf /data/ops-ask/$ArchiveName -C /data/ops-ask"
Write-Host "  chmod +x /data/ops-ask/server-deploy.sh"
Write-Host "  /data/ops-ask/server-deploy.sh"
