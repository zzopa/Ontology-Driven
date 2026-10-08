param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [switch]$CheckOnly,
    [switch]$NoBrowser,
    [switch]$LibraryOnly,
    [ValidateRange(15, 600)][int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'

function Test-ProjectCommand {
    param($Process, [string]$PythonPath, [string]$EntryPath)
    if (-not $Process -or -not $Process.CommandLine) { return $false }
    # Accept only the known entrypoint, never -c, -m, or arbitrary Python commands.
    # Relative ask_web.py supports the existing start.ps1 / manual startup.
    $pythonPattern = [regex]::Escape($PythonPath)
    $entryPattern = [regex]::Escape($EntryPath)
    $pattern = '^"?' + $pythonPattern + '"?\s+(?:-X\s+utf8\s+)?(?:"' +
        $entryPattern + '"|' + $entryPattern + '|"?ask_web\.py"?)\s*$'
    return [regex]::IsMatch($Process.CommandLine, $pattern, 'IgnoreCase')
}

function Test-ProjectProcess {
    param($Process, [hashtable]$Processes, [string]$PythonPath, [string]$EntryPath)
    if (-not (Test-ProjectCommand $Process $PythonPath $EntryPath)) { return $false }
    if ($Process.ExecutablePath -ieq $PythonPath) { return $true }
    # Windows venv python.exe wraps a base-interpreter child owning the socket.
    # Trust that child only when its live parent is this project's venv launcher.
    $parent = $Processes[[int]$Process.ParentProcessId]
    return ($parent -and $parent.ExecutablePath -ieq $PythonPath -and
        (Test-ProjectCommand $parent $PythonPath $EntryPath) -and
        [IO.Path]::GetFileName([string]$Process.ExecutablePath) -ieq 'python.exe')
}

function Get-ProcessSnapshot {
    $snapshot = @{}
    Get-CimInstance Win32_Process | ForEach-Object { $snapshot[[int]$_.ProcessId] = $_ }
    return $snapshot
}

function Get-PortOwners {
    param([int]$Port)
    # Enumerate listeners without treating an empty port as a cmdlet error.
    return @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object { $_.LocalPort -eq $Port } |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Assert-ProjectOwners {
    param([int[]]$Owners, [hashtable]$Processes, [string]$PythonPath, [string]$EntryPath)
    foreach ($ownerId in $Owners) {
        if (-not (Test-ProjectProcess $Processes[$ownerId] $Processes $PythonPath $EntryPath)) {
            throw "端口被其他程序占用或无法确认归属（PID $ownerId），未停止该程序。请先检查端口。"
        }
    }
}

function Stop-VerifiedProcess {
    param($Expected, [string]$PythonPath, [string]$EntryPath)
    $snapshot = Get-ProcessSnapshot
    $current = $snapshot[[int]$Expected.ProcessId]
    if (-not $current) { return }
    # Recheck identity immediately before stopping; avoid PID reuse after snapshot.
    if ($current.CreationDate -ne $Expected.CreationDate -or
        -not (Test-ProjectProcess $current $snapshot $PythonPath $EntryPath)) {
        throw '进程归属发生变化，已取消停止操作，请重新检查。'
    }
    Stop-Process -Id $current.ProcessId -ErrorAction Stop
}

function Test-DatabaseSocket {
    param([string]$Address, [int]$Port)
    $client = New-Object Net.Sockets.TcpClient
    try {
        $connection = $client.ConnectAsync($Address, $Port)
        if (-not $connection.Wait(3000)) { return $false }
        $connection.GetAwaiter().GetResult()
        return $client.Connected
    } catch { return $false }
    finally { $client.Dispose() }
}

if ($LibraryOnly) { return }

$exitCode = 1
$restartMutex = $null
$hasLock = $false
$stdoutLog = $null
$stderrLog = $null
try {
    $projectDir = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\')
    $pythonExe = Join-Path $projectDir '.venv\Scripts\python.exe'
    $entryFile = Join-Path $projectDir 'ask_web.py'
    foreach ($required in @($pythonExe, $entryFile)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "缺少文件：$required。请先按 README 安装项目依赖。"
        }
    }
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        $lockId = [BitConverter]::ToString($hash.ComputeHash(
            [Text.Encoding]::UTF8.GetBytes($projectDir.ToUpperInvariant()))).Replace('-', '')
    } finally { $hash.Dispose() }
    $restartMutex = New-Object Threading.Mutex($false, "Local\hbairport-restart-$lockId")
    try { $hasLock = $restartMutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $hasLock = $true }
    if (-not $hasLock) { throw '另一个重启任务正在运行，请不要重复双击。' }

    Push-Location -LiteralPath $projectDir
    try {
        # Load the same effective settings as ask_web.py; output only non-secret fields.
        $configCode = "import json; from app.config import settings as s; print(json.dumps(dict(host=s.host, port=s.port, db_host=s.db['host'], db_port=s.db.get('port',5432), page=str(s.page_path))))"
        $configJson = & $pythonExe -X utf8 -c $configCode
        if ($LASTEXITCODE -ne 0) { throw '配置加载失败，旧服务未停止，请检查配置文件。' }
        $config = ($configJson -join "`n") | ConvertFrom-Json
        if (-not (Test-Path -LiteralPath $config.page -PathType Leaf)) {
            throw '缺少前端构建产物，请在 frontend 执行 npm ci 和 npm run build。旧服务未停止。'
        }
        if ([int]$config.port -lt 1 -or [int]$config.port -gt 65535) { throw '监听端口不合法。' }
        $browseHost = [string]$config.host
        if ($browseHost -in @('0.0.0.0', '', '*')) { $browseHost = '127.0.0.1' }
        elseif ($browseHost -eq '::') { $browseHost = '[::1]' }
        elseif ($browseHost.Contains(':') -and -not $browseHost.StartsWith('[')) { $browseHost = "[$browseHost]" }
        $pageUrl = "http://${browseHost}:$($config.port)/"
        Write-Host "项目：$projectDir"
        Write-Host "地址：$pageUrl"
        Write-Host '[1/4] 检查数据库、前端与端口归属...'
        if (-not (Test-DatabaseSocket $config.db_host ([int]$config.db_port))) {
            throw '数据库端口不可连接，请先启动数据库。旧服务未停止。'
        }
        $owners = @(Get-PortOwners ([int]$config.port))
        $snapshot = Get-ProcessSnapshot
        Assert-ProjectOwners $owners $snapshot $pythonExe $entryFile
        if ($CheckOnly) {
            Write-Host "检查通过，监听进程：$($owners -join ', ')。仅检查，未停止或启动服务。" -ForegroundColor Green
            $exitCode = 0
        } else {
            Write-Host '[2/4] 停止本项目旧服务（正在进行的问答会中断）...'
            $parents = @{}
            foreach ($ownerId in $owners) {
                $oldProcess = $snapshot[[int]$ownerId]
                $parent = $snapshot[[int]$oldProcess.ParentProcessId]
                if ($parent -and $parent.ExecutablePath -ieq $pythonExe -and
                    (Test-ProjectCommand $parent $pythonExe $entryFile)) {
                    $parents[[int]$parent.ProcessId] = $parent
                }
                Stop-VerifiedProcess $oldProcess $pythonExe $entryFile
            }
            foreach ($parent in $parents.Values) { Stop-VerifiedProcess $parent $pythonExe $entryFile }
            $stopDeadline = [DateTime]::UtcNow.AddSeconds(10)
            while (@(Get-PortOwners ([int]$config.port)).Count -gt 0) {
                if ([DateTime]::UtcNow -ge $stopDeadline) { throw '端口未释放，未启动新服务，请查看进程。' }
                Start-Sleep -Milliseconds 300
            }

            Write-Host '[3/4] 后台启动服务，日志写入 artifacts/logs...'
            $logDir = Join-Path $projectDir 'artifacts\logs'
            New-Item -ItemType Directory -Path $logDir -Force | Out-Null
            $logId = 'restart-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
            $stdoutLog = Join-Path $logDir "$logId.stdout.log"
            $stderrLog = Join-Path $logDir "$logId.stderr.log"
            $started = Start-Process -FilePath $pythonExe -ArgumentList @('-X', 'utf8', ('"' + $entryFile + '"')) `
                -WorkingDirectory $projectDir -WindowStyle Hidden -RedirectStandardOutput $stdoutLog `
                -RedirectStandardError $stderrLog -PassThru
            Write-Host '[4/4] 等待数据库元数据刷新与页面就绪...'
            $watch = [Diagnostics.Stopwatch]::StartNew()
            $ready = $false
            $lastNotice = -10
            while ($watch.Elapsed.TotalSeconds -lt $StartupTimeoutSeconds) {
                $newOwners = @(Get-PortOwners ([int]$config.port))
                if ($newOwners.Count -gt 0) {
                    $newSnapshot = Get-ProcessSnapshot
                    Assert-ProjectOwners $newOwners $newSnapshot $pythonExe $entryFile
                    foreach ($ownerId in $newOwners) {
                        $listener = $newSnapshot[[int]$ownerId]
                        if ($listener.ProcessId -ne $started.Id -and $listener.ParentProcessId -ne $started.Id) {
                            throw '监听进程不是本次启动的服务，请检查是否有其他启动任务。'
                        }
                    }
                    try {
                        $status = Invoke-RestMethod -Uri ($pageUrl + 'api/status') -TimeoutSec 3 -UseBasicParsing
                        $page = Invoke-WebRequest -Uri $pageUrl -TimeoutSec 3 -UseBasicParsing
                        if ($status.ready -eq $true -and $page.StatusCode -eq 200) { $ready = $true; break }
                    } catch { }
                }
                $started.Refresh()
                if ($started.HasExited) { throw '新服务已经退出，请检查启动日志（数据库认证、配置或依赖）。' }
                if ($watch.Elapsed.TotalSeconds - $lastNotice -ge 10) {
                    Write-Host ("正在启动，已等待 {0:N0} 秒..." -f $watch.Elapsed.TotalSeconds)
                    $lastNotice = $watch.Elapsed.TotalSeconds
                }
                Start-Sleep -Seconds 1
            }
            if (-not $ready) { throw "等待启动超过 $StartupTimeoutSeconds 秒。进程可能仍在初始化，请查看日志，不要连续重启。" }
            Write-Host "重启成功：$pageUrl" -ForegroundColor Green
            if (-not $NoBrowser) {
                try { Start-Process $pageUrl | Out-Null }
                catch { Write-Host '未能自动打开浏览器，请复制上方地址。' }
            }
            $exitCode = 0
        }
    } finally { Pop-Location }
} catch {
    Write-Host ("重启失败：" + $_.Exception.Message) -ForegroundColor Red
    Write-Host '如提示进程/端口访问被拒绝，可右键 restart.cmd，以管理员身份运行。'
} finally {
    if ($stdoutLog) { Write-Host "标准日志：$stdoutLog" }
    if ($stderrLog) { Write-Host "错误日志：$stderrLog" }
    if ($hasLock) { $restartMutex.ReleaseMutex() }
    if ($restartMutex) { $restartMutex.Dispose() }
}
exit $exitCode
