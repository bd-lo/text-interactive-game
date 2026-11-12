# 内容包一键体验（N1-035-036）：校验并导入 examples/content-pack/，建一局「灰烬灯塔 + 世界书」后打开对局页。
#
# 用法：
#   .\scripts\quickstart-content-pack.ps1                 # 临时数据库 + 离线 + 自动导入 + 打开对局页
#   .\scripts\quickstart-content-pack.ps1 -Check          # headless 自检：校验/导入/建局全过则退出码 0
#   .\scripts\quickstart-content-pack.ps1 -Port 8001      # 换端口（-Check 时端口被占用会自动向后找空端口）
#   .\scripts\quickstart-content-pack.ps1 -NoBrowser      # 不自动打开浏览器
#   .\scripts\quickstart-content-pack.ps1 -KeepDb         # 退出后保留临时数据库（默认清理）
#
# 说明（护栏）：
# - 显式离线（TEXTGAME_LLM_ENABLED=false）+ 独立临时数据库：不读写 data/textgame.db、不调用真实 LLM；
# - 不复制/不打印任何密钥；服务只监听 127.0.0.1（复用 scripts/play.ps1 的 uv/uvicorn 启动口径）；
# - 内容包是纯数据：脚本只把它 POST 到既有 validate/import 端点，不执行其中任何内容；
# - PowerShell 5.1 / 7 兼容（.ps1 文件本身须保存为 UTF-8 with BOM，见仓库 .gitattributes）。

param(
    [switch]$Check,
    [switch]$NoBrowser,
    [switch]$KeepDb,
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$PackDir = Join-Path (Join-Path $Root 'examples') 'content-pack'
$BaseUrl = "http://127.0.0.1:$Port"

function Write-Step([string]$Message) {
    Write-Host ("== " + $Message)
}

function Write-Failure([string]$Message) {
    Write-Host ("[content-pack] 失败：" + $Message)
}

# 1) 前置：uv（与 scripts/play.ps1 相同的唯一必需前置）
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    Write-Host '未找到 uv：请先安装 https://github.com/astral-sh/uv 后重试。'
    exit 1
}
$uvExe = $uvCommand.Source

if (-not (Test-Path $PackDir)) {
    Write-Failure ("缺少内容包目录：" + $PackDir)
    exit 1
}

# 2) 依赖：首次运行安装运行时依赖（与 play.ps1 同口径；已安装则秒级）
if (-not (Test-Path (Join-Path $Root '.venv'))) {
    Write-Step '首次运行：安装 Python 运行时依赖（需要联网）…'
    Push-Location $Root
    try {
        & $uvExe sync --frozen --no-dev
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Failure '依赖安装失败：首次运行需要联网下载 CPython 与依赖包。'
        exit $LASTEXITCODE
    }
}

function Test-PortBusy([int]$LocalPort) {
    $listener = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
    return [bool]$listener
}

# 3) 端口：-Check 自动找空端口；交互模式端口被占用时给可操作提示（与 play.ps1 一致）。
if (Test-PortBusy([int]$Port)) {
    if ($Check) {
        $pickedPort = 0
        for ($candidate = $Port; $candidate -lt ($Port + 20); $candidate++) {
            if (-not (Test-PortBusy([int]$candidate))) {
                $pickedPort = $candidate
                break
            }
        }
        if ($pickedPort -eq 0) {
            Write-Failure ("端口 " + $Port + " 起连续 20 个端口都被占用，请用 -Port 指定。")
            exit 1
        }
        $Port = $pickedPort
        $BaseUrl = "http://127.0.0.1:$Port"
    } else {
        Write-Failure ("端口 " + $Port + " 已被占用：请先关闭占用进程，或用 -Port 换一个端口（如 -Port 8001）。")
        exit 1
    }
}

# 4) 临时运行目录：临时 SQLite + 临时凭据路径（不读写仓库 data/，不复制任何真实密钥）
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("textgame-content-pack-" + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
$dbFile = Join-Path $tempRoot 'content-pack.db'
$logFile = Join-Path $tempRoot 'server.log'
$errLogFile = Join-Path $tempRoot 'server.err.log'
$runToken = [System.Guid]::NewGuid().ToString('N').Substring(0, 10)

$env:TEXTGAME_DB_URL = 'sqlite:///' + ($dbFile -replace '\\', '/')
$env:TEXTGAME_LLM_ENABLED = 'false'
$env:TEXTGAME_FREE_CRAFT_ENABLED = 'false'
$env:TEXTGAME_LLM_CREDENTIALS_FILE = Join-Path $tempRoot 'llm.credentials.json'

$script:serverProcess = $null

function Stop-PackServer {
    if ($null -eq $script:serverProcess) {
        return
    }
    $serverProcessId = $script:serverProcess.Id
    if (-not $script:serverProcess.HasExited) {
        # 与 play.ps1 同口径：按进程树结束 uv → uvicorn 子进程。
        & taskkill /T /F /PID $serverProcessId 2>$null | Out-Null
        Start-Sleep -Milliseconds 300
        if (-not $script:serverProcess.HasExited) {
            Stop-Process -Id $serverProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    $script:serverProcess = $null
}

function Remove-TempData {
    if ($KeepDb) {
        Write-Host ("临时数据库已保留：" + $dbFile)
        Write-Host ("服务日志已保留：" + $logFile)
    } else {
        Remove-Item -Path $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Show-ServerLogTail {
    foreach ($path in @($errLogFile, $logFile)) {
        if (Test-Path $path) {
            Get-Content $path -ErrorAction SilentlyContinue | Select-Object -Last 8 | ForEach-Object {
                Write-Host ("  " + $_)
            }
        }
    }
}

# 5) 内容包客户端（内嵌 Python，只用标准库；把每个 JSON POST 到既有 validate/import 端点）。
#    代码保持纯 ASCII（PS 5.1 管道默认编码），中文只出现在 JSON 文件与输出 ID 中。
$clientCode = @'
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1].rstrip("/")
PACK = Path(sys.argv[2])
TOKEN = sys.argv[3]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(method, path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, headers=dict(headers or {}), method=method)
    try:
        with OPENER.open(req, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def fail(message):
    print("RESULT=FAIL")
    print(message)
    sys.exit(1)


def expect(status, raw, wanted, label):
    if status != wanted:
        fail("%s failed: HTTP %s %s" % (label, status, raw.decode("utf-8", "replace")[:600]))
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        fail("%s returned non-JSON: %s" % (label, raw.decode("utf-8", "replace")[:300]))


def post_json(path, payload, key=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if key:
        headers["Idempotency-Key"] = key
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return request("POST", path, body, headers)


def post_raw(path, raw, key=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if key:
        headers["Idempotency-Key"] = key
    return request("POST", path, raw, headers)


def post_multipart(path, filename, content, key):
    boundary = "----textgame-content-pack-boundary"
    head = (
        "--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
        "Content-Type: application/json\r\n\r\n" % (boundary, filename)
    ).encode("utf-8")
    tail = ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    headers = {
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "Idempotency-Key": key,
    }
    return request("POST", path, head + content + tail, headers)


def read_json(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail("cannot read %s: %s" % (path, exc))
    if not isinstance(payload, dict):
        fail("%s root must be a JSON object" % path)
    return payload


def main():
    pack = PACK.resolve()
    scenarios = sorted((pack / "scenarios").glob("*.json"))
    books = sorted((pack / "world-books").glob("*.json"))
    cards = sorted((pack / "character-cards").glob("*.json"))
    if len(scenarios) < 2 or not books or not cards:
        fail(
            "content pack layout incomplete: scenarios=%d books=%d cards=%d"
            % (len(scenarios), len(books), len(cards))
        )

    scenario_ids = {}
    for index, path in enumerate(scenarios, start=1):
        raw = path.read_bytes()
        payload = read_json(path)
        slug = str(payload.get("slug") or path.stem)
        status, body = post_raw("/api/v1/scenarios/validate", raw)
        result = expect(status, body, 200, "validate scenario %s" % slug)
        if result.get("ok") is not True:
            fail("scenario %s not valid: %s" % (slug, json.dumps(result.get("errors"), ensure_ascii=False)[:600]))
        status, body = post_raw("/api/v1/scenarios", raw, key="cp-%s-scenario-%02d" % (TOKEN, index))
        imported = expect(status, body, 201, "import scenario %s" % slug)
        scenario_ids[slug] = str(imported["scenario_id"])
        print("SCENARIO slug=%s id=%s" % (slug, scenario_ids[slug]))

    book_ids = []
    for index, path in enumerate(books, start=1):
        payload = read_json(path)
        status, body = post_json("/api/v1/world-books/validate", {"payload": payload})
        result = expect(status, body, 200, "validate world book %s" % path.stem)
        if result.get("valid") is not True:
            fail("world book %s not valid: %s" % (path.stem, json.dumps(result.get("errors"), ensure_ascii=False)[:600]))
        status, body = post_multipart(
            "/api/v1/world-books/import",
            path.name,
            path.read_bytes(),
            "cp-%s-book-%02d" % (TOKEN, index),
        )
        imported = expect(status, body, 201, "import world book %s" % path.stem)
        book_ids.append(str(imported["world_book_id"]))
        print("WORLD_BOOK file=%s id=%s entries=%s" % (path.name, book_ids[-1], imported.get("entry_count")))

    card_ids = []
    for index, path in enumerate(cards, start=1):
        raw = path.read_bytes()
        status, body = post_raw("/api/v1/character-cards/validate", raw)
        result = expect(status, body, 200, "validate card %s" % path.stem)
        if result.get("valid") is not True:
            fail("card %s not valid: %s" % (path.stem, json.dumps(result.get("errors"), ensure_ascii=False)[:600]))
        status, body = post_raw("/api/v1/character-cards", raw, key="cp-%s-card-%02d" % (TOKEN, index))
        imported = expect(status, body, 201, "save card %s" % path.stem)
        card_ids.append(str(imported["card_id"]))
        print("CARD file=%s id=%s" % (path.name, card_ids[-1]))

    main_slug = "ashen-lighthouse" if "ashen-lighthouse" in scenario_ids else sorted(scenario_ids)[0]
    game_payload = {"scenario_id": scenario_ids[main_slug], "seed": 7}
    if book_ids:
        game_payload["world_book_id"] = book_ids[0]
    status, body = post_json("/api/v1/games", game_payload, key="cp-%s-game-main" % TOKEN)
    created = expect(status, body, 201, "create game")
    game_id = str(created["game_id"])
    print("GAME id=%s scenario=%s" % (game_id, main_slug))

    status, body = request("GET", "/api/v1/games/%s/view" % game_id)
    view = expect(status, body, 200, "game view")
    if view.get("version") != created.get("version"):
        fail("game view version mismatch: %s != %s" % (view.get("version"), created.get("version")))
    print("VIEW game=%s version=%s" % (game_id, view.get("version")))

    if card_ids:
        status, body = post_json("/api/v1/games", {"card_id": card_ids[0]}, key="cp-%s-game-card" % TOKEN)
        card_game = expect(status, body, 201, "create card game")
        print("CARD_GAME id=%s" % (card_game["game_id"]))

    print("RESULT=PASS")
    return 0


sys.exit(main())
'@

$exitCode = 0
$gameId = ''

try {
    Write-Step ("启动网页服务（离线、临时数据库）：" + $BaseUrl)
    $script:serverProcess = Start-Process -FilePath $uvExe -ArgumentList @(
        'run', '--no-dev', 'uvicorn', 'textgame.main:app',
        '--host', '127.0.0.1', '--port', "$Port"
    ) -WorkingDirectory $Root -PassThru -NoNewWindow -RedirectStandardOutput $logFile -RedirectStandardError $errLogFile

    $healthy = $false
    for ($attempt = 0; $attempt -lt 160; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($script:serverProcess.HasExited) {
            break
        }
        try {
            $health = Invoke-WebRequest -Uri ($BaseUrl + '/healthz') -UseBasicParsing -TimeoutSec 2
            if ($health.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        } catch {
            # 服务尚未就绪：继续等待
        }
    }
    if (-not $healthy) {
        Write-Failure '服务未在 80 秒内就绪（端口被占用/依赖缺失/启动异常？）。'
        Show-ServerLogTail
        throw '服务未就绪'
    }

    Write-Step '校验并导入内容包（场景 / 世界书 / 人物卡 → 建局 → /view）'
    $packOutput = @($clientCode | & $uvExe run --no-dev python - $BaseUrl $PackDir $runToken 2>&1)
    $clientExit = $LASTEXITCODE
    $packOutput | ForEach-Object { Write-Host ([string]$_) }
    if ($clientExit -ne 0) {
        Show-ServerLogTail
        throw '内容包校验/导入/建局失败（见上方输出与服务日志）'
    }

    $gameLine = $packOutput | Where-Object { [string]$_ -match '^GAME id=' } | Select-Object -First 1
    if ($null -ne $gameLine) {
        $gameId = ([string]$gameLine) -replace '^GAME id=', ''
        $gameId = $gameId.Split(' ')[0]
    }
    if ([string]::IsNullOrEmpty($gameId)) {
        throw '未从输出中解析到对局 ID'
    }

    if ($Check) {
        try {
            $index = Invoke-WebRequest -Uri ($BaseUrl + '/') -UseBasicParsing -TimeoutSec 5
            if ($index.StatusCode -ne 200) {
                throw ('GET / 返回 ' + $index.StatusCode + '（期望 200）')
            }
        } catch {
            throw ('GET / 校验失败：' + $_.Exception.Message)
        }
        Write-Host ('PASS: 内容包自检通过（2 场景校验+导入、1 世界书、1 人物卡、建局 /view、GET /=200；对局 ' + $gameId + '）')
    } else {
        $gameUrl = $BaseUrl + '/game/' + $gameId
        Write-Step ('对局页面：' + $gameUrl)
        Write-Host ('场景库：' + $BaseUrl + '/scenarios（可另开「镜井」；人物卡在「卡库」，世界书在「世界书」）')
        Write-Host '终端等价：uv run --no-dev python -m textgame.console --scenario-id <上方 SCENARIO 的 id>（或 --card-id <CARD 的 id>）'
        Write-Host '按 Ctrl+C 结束游戏；临时数据库会被清理（想保留请加 -KeepDb）。'
        if (-not $NoBrowser) {
            try {
                Start-Process $gameUrl | Out-Null
            } catch {
                Write-Host '（未能自动打开浏览器，请手动复制上面的对局页面 URL）'
            }
        }
        $script:serverProcess.WaitForExit()
    }
} catch {
    Write-Failure $_.Exception.Message
    $exitCode = 1
} finally {
    Stop-PackServer
    Remove-TempData
}

exit $exitCode
