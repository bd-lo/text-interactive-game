# 一键启动（网页优先）· 文字交互游戏
# 用法：
#   .\scripts\play.ps1               # 有 web/dist → 启动网页版并尝试打开浏览器；否则回终端版
#   .\scripts\play.ps1 -Terminal     # 直接玩终端版（等价于旧的 quickstart）
#   .\scripts\play.ps1 -Check        # 自检：启动网页服务 → GET /healthz 与 / 均 200 → 退出
#   .\scripts\play.ps1 -Port 8001    # 指定端口（默认 8000）
#   .\scripts\play.ps1 -Llm          # 用真实 LLM 叙事（需 .env 填 TEXTGAME_LLM_API_KEY）
param(
    [switch]$Terminal,
    [switch]$Check,
    [int]$Port = 8000,
    [switch]$Llm
)
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Stop-PlayServer {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process -or $Process.HasExited) { return }
    # taskkill /T 连同 uv 派生的 python/uvicorn 子进程一起结束，避免继续占用端口
    & taskkill /T /F /PID $Process.Id | Out-Null
}

# 1) 前置：uv（唯一必需前置；Python 由 uv 自动下载管理）
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host '未找到 uv：请先安装 https://github.com/astral-sh/uv 后重试。'
    exit 1
}

# 2) 可选：真实 LLM 叙事（默认离线确定性，不需要 Key）
if ($Llm) {
    $env:TEXTGAME_LLM_ENABLED = 'true'
    if (-not $env:TEXTGAME_LLM_API_KEY -and -not (Test-Path (Join-Path $Root '.env'))) {
        Write-Host '已请求 -Llm，但没有找到 API Key：'
        Write-Host '  请复制 .env.example 为 .env，填写 TEXTGAME_LLM_API_KEY=<你的 DeepSeek Key>；'
        Write-Host '  不加 -Llm 时默认离线模式，不填 Key 也能正常游玩。'
        exit 1
    }
}

# 3) 依赖：首次运行安装运行时依赖（需联网，约数百 MB；已安装过则秒级）
if (-not (Test-Path (Join-Path $Root '.venv'))) {
    Write-Host '== 首次运行：安装 Python 运行时依赖（需要联网）…'
    uv sync --frozen --no-dev
    if ($LASTEXITCODE -ne 0) {
        Write-Host '依赖安装失败：首次运行需要联网下载 CPython 与依赖包。'
        Write-Host '  如使用代理/内网，请先配置代理后重试；详见 README「常见问题」。'
        exit $LASTEXITCODE
    }
}

$DistIndex = Join-Path $Root 'web\dist\index.html'
$HasDist = Test-Path $DistIndex

# 4) 终端版：显式 -Terminal，或网页产物缺失时自动回退（保证离线/无 Node 也能玩）
if ($Terminal -or (-not $HasDist)) {
    if (-not $HasDist) {
        Write-Host '== 未找到网页产物 web/dist：先进入终端版。'
        Write-Host '   想玩网页版：pnpm --dir web install --frozen-lockfile ; pnpm --dir web build'
    }
    uv run --no-dev python -m textgame.console
    exit $LASTEXITCODE
}

# 5) 网页版
$Url = "http://127.0.0.1:$Port"
$InUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($InUse) {
    Write-Host "端口 $Port 已被占用：请先关闭占用进程，或用 -Port 换一个端口（如 -Port 8001）。"
    exit 1
}

if ($Check) {
    Write-Host "== 自检：启动网页服务并校验 $Url …"
    $log = Join-Path $env:TEMP "textgame-play-check-$Port.log"
    $errLog = "$log.err"
    $proc = Start-Process -FilePath 'uv' -ArgumentList @(
        'run', '--no-dev', 'uvicorn', 'textgame.main:app',
        '--host', '127.0.0.1', '--port', "$Port"
    ) -WorkingDirectory $Root -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError $errLog
    $exitCode = 1
    try {
        $healthOk = $false
        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Milliseconds 500
            try {
                $h = Invoke-WebRequest -Uri "$Url/healthz" -UseBasicParsing -TimeoutSec 2
                if ($h.StatusCode -eq 200) { $healthOk = $true; break }
            } catch { }
        }
        if (-not $healthOk) {
            Write-Host "自检失败：服务未在 20 秒内就绪（端口 $Port）。"
            if ($Llm) {
                Write-Host '  -Llm 已开启：请确认 .env 中 TEXTGAME_LLM_API_KEY 已填写且可用。'
            }
            if (Test-Path $errLog) {
                Get-Content $errLog | Select-Object -Last 5 | ForEach-Object { Write-Host "  $_" }
            }
        } else {
            try {
                $r = Invoke-WebRequest -Uri "$Url/" -UseBasicParsing -TimeoutSec 5
                $ctype = [string]$r.Headers['Content-Type']
                if ($r.StatusCode -eq 200 -and $ctype -match 'text/html') {
                    Write-Host "自检通过：/healthz=200，/=200（$ctype）"
                    $exitCode = 0
                } else {
                    Write-Host "自检失败：/ 返回 $($r.StatusCode)（$ctype），期望 200 text/html。"
                }
            } catch {
                Write-Host "自检失败：GET / 出错（$($_.Exception.Message)）。"
            }
        }
    } finally {
        Stop-PlayServer -Process $proc
        if (Test-Path $log) { Remove-Item $log -Force -ErrorAction SilentlyContinue }
        if (Test-Path $errLog) { Remove-Item $errLog -Force -ErrorAction SilentlyContinue }
    }
    exit $exitCode
}

Write-Host "== 网页版已启动：$Url"
Write-Host '   按 Ctrl+C 停止服务；存档在 data/textgame.db，下次启动会自动继续。'
try { Start-Process $Url | Out-Null } catch { }
uv run --no-dev uvicorn textgame.main:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
