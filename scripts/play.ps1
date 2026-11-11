# 一键启动（网页优先）· 文字交互游戏
# 用法：
#   .\scripts\play.ps1                 # 有 web/dist → 启动网页版并尝试打开浏览器；否则回终端版
#   .\scripts\play.ps1 -Terminal       # 直接玩终端版（等价于旧的 quickstart）
#   .\scripts\play.ps1 -Check          # 自检：启动网页服务 → GET /healthz 与 / 均 200 → 退出
#   .\scripts\play.ps1 -Launcher       # 普通玩家：启动器小窗口 + 浏览器；关窗/游戏内退出即结束
#   .\scripts\play.ps1 -LauncherCheck  # 自动化验收：无窗口无浏览器，验证启动器控制面后自动退出
#   .\scripts\play.ps1 -Stop           # 兜底退出：按端口精确结束后台游戏进程（退出游戏.bat 使用）
#   .\scripts\play.ps1 -Port 8001      # 指定端口（默认 8000，对 -Launcher/-LauncherCheck/-Stop 同样生效）
#   .\scripts\play.ps1 -Llm            # 用真实 LLM 叙事（需 .env 填 TEXTGAME_LLM_API_KEY）
# 调试/自动化环境变量（普通玩家无需设置）：
#   TEXTGAME_LAUNCHER_GUI=console   → 强制 -Launcher 用控制台模式（不弹窗口）
#   TEXTGAME_LAUNCHER_NO_BROWSER=1  → -Launcher 不自动打开浏览器（冒烟测试用）
param(
    [switch]$Terminal,
    [switch]$Check,
    [switch]$Launcher,
    [switch]$LauncherCheck,
    [switch]$Stop,
    [int]$Port = 8000,
    [int]$IdleExitSeconds = 300,
    [switch]$Llm
)
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# 平台判定（PowerShell 5.1 没有 $IsWindows）
$OnWindows = ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT)
$OpenBrowser = ($env:TEXTGAME_LAUNCHER_NO_BROWSER -ne '1')

function Stop-PlayServer {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process -or $Process.HasExited) { return }
    # taskkill /T 连同 uv 派生的 python/uvicorn 子进程一起结束，避免继续占用端口
    & taskkill /T /F /PID $Process.Id | Out-Null
}

function Test-UrlOk {
    param([string]$Url, [int]$TimeoutSec = 2)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ([int]$response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# 按端口精确查监听进程：只用 netstat 输出的「协议 + 本地地址 + 状态 + PID」列判定，
# 不做进程名宽泛匹配，避免误杀其它程序（退出游戏.bat 的兜底逻辑）。
function Get-ListeningProcessIds {
    param([int]$LocalPort)
    $found = New-Object System.Collections.Generic.List[int]
    try { $rawLines = @(& netstat -ano 2>$null) } catch { return @() }
    foreach ($rawLine in $rawLines) {
        $columns = ([string]$rawLine).Trim() -split '\s+'
        if ($columns.Length -lt 5) { continue }
        if ($columns[0] -ne 'TCP') { continue }
        if ($columns[1] -ne ("127.0.0.1:" + $LocalPort)) { continue }
        if ($columns[3] -ne 'LISTENING') { continue }
        $parsedPid = 0
        if ([int]::TryParse($columns[4], [ref]$parsedPid)) { $found.Add($parsedPid) }
    }
    return @($found | Sort-Object -Unique)
}

function Find-FreePort {
    param([int]$StartPort = 8000, [int]$Count = 50)
    for ($candidate = $StartPort; $candidate -lt ($StartPort + $Count); $candidate++) {
        if (@(Get-ListeningProcessIds -LocalPort $candidate).Count -eq 0) { return $candidate }
    }
    throw "在 $StartPort..$($StartPort + $Count - 1) 范围内找不到空闲端口"
}

function Get-LogTail {
    param([string]$Path, [int]$Lines = 5)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    try {
        return @(Get-Content -LiteralPath $Path -Tail $Lines -Encoding UTF8 -ErrorAction Stop)
    } catch {
        return @()
    }
}

function Show-LogTail {
    param([string]$Path, [int]$Lines = 5)
    $tail = @(Get-LogTail -Path $Path -Lines $Lines)
    if ($tail.Count -eq 0) { return }
    Write-Host "  —— 日志最后 $($tail.Count) 行（$Path）："
    foreach ($tailLine in $tail) { Write-Host "  $tailLine" }
}

function Invoke-JsonGet {
    param([string]$Url, [int]$TimeoutSec = 5)
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    $body = $null
    if (-not [string]::IsNullOrWhiteSpace($response.Content)) { $body = $response.Content | ConvertFrom-Json }
    return @{ StatusCode = [int]$response.StatusCode; Body = $body }
}

function Invoke-JsonPost {
    param([string]$Url, [string]$Body = '{}', [int]$TimeoutSec = 5)
    $headers = @{ 'Idempotency-Key' = ('launcher-' + [System.Guid]::NewGuid().ToString('N')) }
    $response = Invoke-WebRequest -Uri $Url -Method Post -ContentType 'application/json' -Body $Body -Headers $headers -UseBasicParsing -TimeoutSec $TimeoutSec
    $parsedBody = $null
    if (-not [string]::IsNullOrWhiteSpace($response.Content)) { $parsedBody = $response.Content | ConvertFrom-Json }
    return @{ StatusCode = [int]$response.StatusCode; Body = $parsedBody }
}

# ---------------------------------------------------------------------------
# 后台进程 + 日志（stdout/stderr 合并追加到同一日志文件，UTF-8、不覆盖旧内容）
# ---------------------------------------------------------------------------
$script:DrainProcess = $null
$script:DrainWriter = $null
$script:DrainOutTask = $null
$script:DrainErrTask = $null

function Stop-LogDrain {
    if ($null -ne $script:DrainWriter) {
        try { $script:DrainWriter.Flush(); $script:DrainWriter.Dispose() } catch { }
        $script:DrainWriter = $null
    }
    $script:DrainOutTask = $null
    $script:DrainErrTask = $null
    $script:DrainProcess = $null
}

function Update-LogDrain {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process -or $null -eq $script:DrainWriter) { return }
    if ($null -ne $script:DrainOutTask) {
        while ($script:DrainOutTask.IsCompleted) {
            $line = $null
            $finished = $false
            try { $line = $script:DrainOutTask.Result } catch { $finished = $true }
            if ($finished -or $null -eq $line) { $script:DrainOutTask = $null; break }
            try { $script:DrainWriter.WriteLine($line) } catch { }
            try { $script:DrainOutTask = $Process.StandardOutput.ReadLineAsync() } catch { $script:DrainOutTask = $null; break }
        }
    }
    if ($null -ne $script:DrainErrTask) {
        while ($script:DrainErrTask.IsCompleted) {
            $line = $null
            $finished = $false
            try { $line = $script:DrainErrTask.Result } catch { $finished = $true }
            if ($finished -or $null -eq $line) { $script:DrainErrTask = $null; break }
            try { $script:DrainWriter.WriteLine($line) } catch { }
            try { $script:DrainErrTask = $Process.StandardError.ReadLineAsync() } catch { $script:DrainErrTask = $null; break }
        }
    }
}

function Complete-LogDrain {
    param([System.Diagnostics.Process]$Process)
    for ($i = 0; $i -lt 10; $i++) {
        Update-LogDrain -Process $Process
        if ($null -eq $script:DrainOutTask -and $null -eq $script:DrainErrTask) { return }
        Start-Sleep -Milliseconds 100
    }
}

function Start-DrainedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][string]$LogPath,
        [string]$WorkingDirectory = $Root
    )
    Stop-LogDrain
    $logDir = Split-Path -Parent $LogPath
    if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
    # FileShare.ReadWrite：允许同时运行多个启动器/服务时共用同一日志文件（追加写入，读日志不冲突）
    $logStream = New-Object System.IO.FileStream -ArgumentList @($LogPath, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
    $script:DrainWriter = New-Object System.IO.StreamWriter -ArgumentList @($logStream, $utf8)
    $script:DrainWriter.AutoFlush = $true
    $quotedArgs = @()
    foreach ($argItem in $ArgumentList) {
        if ([string]$argItem -match '\s') { $quotedArgs += ('"' + $argItem + '"') } else { $quotedArgs += $argItem }
    }
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = ($quotedArgs -join ' ')
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    try {
        $startInfo.StandardOutputEncoding = $utf8
        $startInfo.StandardErrorEncoding = $utf8
    } catch { }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw "无法启动进程：$FilePath" }
    $script:DrainProcess = $process
    try { $script:DrainOutTask = $process.StandardOutput.ReadLineAsync() } catch { $script:DrainOutTask = $null }
    try { $script:DrainErrTask = $process.StandardError.ReadLineAsync() } catch { $script:DrainErrTask = $null }
    return $process
}

function Wait-ProcessDrained {
    param(
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 0,
        [scriptblock]$OnTick = $null
    )
    $deadline = $null
    if ($TimeoutSeconds -gt 0) { $deadline = (Get-Date).AddSeconds($TimeoutSeconds) }
    while ($true) {
        Update-LogDrain -Process $Process
        if ($Process.HasExited) { Complete-LogDrain -Process $Process; return $true }
        if ($null -ne $deadline -and (Get-Date) -gt $deadline) { return $false }
        if ($null -ne $OnTick) { & $OnTick }
        Start-Sleep -Milliseconds 250
    }
}

function Wait-ForHealth {
    param(
        [string]$BaseUrl,
        [int]$TimeoutSeconds = 90,
        [System.Diagnostics.Process]$Process = $null,
        [scriptblock]$OnTick = $null
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($null -ne $Process) {
            Update-LogDrain -Process $Process
            if ($Process.HasExited) { return $false }
        }
        if (Test-UrlOk -Url ($BaseUrl + '/healthz') -TimeoutSec 2) { return $true }
        if ($null -ne $OnTick) { & $OnTick }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

# ---------------------------------------------------------------------------
# 启动器窗口（WinForms；无图形环境/非 Windows 自动降级为控制台模式）
# ---------------------------------------------------------------------------
$script:LauncherGuiActive = $false
$script:LauncherForm = $null
$script:LauncherStatusLabel = $null
$script:LauncherExitReason = ''
$script:LauncherBaseUrl = ''
$script:LauncherServerProc = $null
$script:LauncherFarewellBox = $null
$script:LauncherFarewellTimer = $null

function Test-LauncherGuiAvailable {
    if (-not $OnWindows) { return $false }
    if ($env:TEXTGAME_LAUNCHER_GUI -eq 'console' -or $env:TEXTGAME_LAUNCHER_GUI -eq '0') { return $false }
    try {
        if (-not [Environment]::UserInteractive) { return $false }
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Enable-GuiVisualStyles {
    try { [System.Windows.Forms.Application]::EnableVisualStyles() } catch { }
}

function Set-LauncherStatus {
    param([string]$Text)
    Write-Host "== $Text"
    if ($null -ne $script:LauncherStatusLabel) {
        try {
            $script:LauncherStatusLabel.Text = $Text
            [System.Windows.Forms.Application]::DoEvents()
        } catch { }
    }
}

function New-LauncherForm {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    Add-Type -AssemblyName System.Drawing -ErrorAction Stop
    Enable-GuiVisualStyles
    $form = New-Object System.Windows.Forms.Form
    $form.Text = '文字交互游戏'
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedSingle'
    $form.MaximizeBox = $false
    $form.MinimizeBox = $true
    $form.ClientSize = New-Object System.Drawing.Size -ArgumentList 540, 240
    try { $form.Font = New-Object System.Drawing.Font -ArgumentList @('Microsoft YaHei UI', 10) } catch { }
    $titleLabel = New-Object System.Windows.Forms.Label
    $titleLabel.Text = '文字交互游戏'
    $titleLabel.AutoSize = $true
    $titleLabel.Location = New-Object System.Drawing.Point -ArgumentList 26, 18
    try { $titleLabel.Font = New-Object System.Drawing.Font -ArgumentList @('Microsoft YaHei UI', 14, [System.Drawing.FontStyle]::Bold) } catch { }
    $form.Controls.Add($titleLabel)
    $statusLabel = New-Object System.Windows.Forms.Label
    $statusLabel.AutoSize = $false
    $statusLabel.Location = New-Object System.Drawing.Point -ArgumentList 28, 64
    $statusLabel.Size = New-Object System.Drawing.Size -ArgumentList 480, 60
    $statusLabel.Text = '正在准备…（首次运行需几分钟）'
    $form.Controls.Add($statusLabel)
    $openButton = New-Object System.Windows.Forms.Button
    $openButton.Text = '打开游戏'
    $openButton.Location = New-Object System.Drawing.Point -ArgumentList 26, 164
    $openButton.Size = New-Object System.Drawing.Size -ArgumentList 160, 40
    $form.Controls.Add($openButton)
    $exitButton = New-Object System.Windows.Forms.Button
    $exitButton.Text = '退出游戏'
    $exitButton.Location = New-Object System.Drawing.Point -ArgumentList 206, 164
    $exitButton.Size = New-Object System.Drawing.Size -ArgumentList 160, 40
    $form.Controls.Add($exitButton)
    $script:LauncherForm = $form
    $script:LauncherStatusLabel = $statusLabel
    $script:LauncherExitReason = ''
    $openButton.Add_Click({
            try {
                if ($script:LauncherBaseUrl) { Start-Process $script:LauncherBaseUrl | Out-Null }
            } catch { }
        })
    $exitButton.Add_Click({
            # 先请服务端优雅退出（存档已提交），再由主流程兜底 taskkill
            try { Invoke-JsonPost -Url ($script:LauncherBaseUrl + '/api/v1/app/shutdown') -Body '{"confirm":true}' -TimeoutSec 3 | Out-Null } catch { }
            $script:LauncherExitReason = 'button'
            try { $script:LauncherForm.Close() } catch { }
        })
    $form.Add_FormClosed({
            if (-not $script:LauncherExitReason) { $script:LauncherExitReason = 'window' }
        })
    $form.Show()
    [System.Windows.Forms.Application]::DoEvents()
    return $form
}

function Close-LauncherForm {
    if ($null -eq $script:LauncherForm) { return }
    try { $script:LauncherForm.Close() } catch { }
    try { $script:LauncherForm.Dispose() } catch { }
    $script:LauncherForm = $null
    $script:LauncherStatusLabel = $null
}

function Show-LauncherError {
    param([string]$Message)
    Write-Host $Message
    Close-LauncherForm
    if ($script:LauncherGuiActive) {
        try {
            Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
            [void][System.Windows.Forms.MessageBox]::Show($Message, '文字交互游戏', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning)
        } catch { }
    }
}

function Show-LauncherFarewell {
    param([string]$Text)
    Write-Host $Text
    if (-not $script:LauncherGuiActive) { return }
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $box = New-Object System.Windows.Forms.Form
        $box.Text = '文字交互游戏'
        $box.FormBorderStyle = 'FixedDialog'
        $box.StartPosition = 'CenterScreen'
        $box.ClientSize = New-Object System.Drawing.Size -ArgumentList 380, 120
        $box.MaximizeBox = $false
        $box.MinimizeBox = $false
        try { $box.Font = New-Object System.Drawing.Font -ArgumentList @('Microsoft YaHei UI', 10) } catch { }
        $label = New-Object System.Windows.Forms.Label
        $label.Text = $Text
        $label.AutoSize = $false
        $label.Location = New-Object System.Drawing.Point -ArgumentList 20, 30
        $label.Size = New-Object System.Drawing.Size -ArgumentList 340, 60
        $label.TextAlign = 'MiddleCenter'
        $box.Controls.Add($label)
        $script:LauncherFarewellBox = $box
        $timer = New-Object System.Windows.Forms.Timer
        $timer.Interval = 2500
        $script:LauncherFarewellTimer = $timer
        $timer.Add_Tick({
                try { $script:LauncherFarewellTimer.Stop() } catch { }
                try { $script:LauncherFarewellBox.Close() } catch { }
            })
        $timer.Start()
        [void]$box.ShowDialog()
        try { $box.Dispose() } catch { }
    } catch { }
    $script:LauncherFarewellBox = $null
    $script:LauncherFarewellTimer = $null
}

function Hide-ConsoleWindow {
    if (-not $OnWindows) { return }
    try {
        if (-not ('TextgamePlay.ConsoleWindow' -as [type])) {
            Add-Type -Namespace 'TextgamePlay' -Name 'ConsoleWindow' -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
[DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);
'@
        }
        $handle = [TextgamePlay.ConsoleWindow]::GetConsoleWindow()
        if ($handle -ne [System.IntPtr]::Zero) { [void][TextgamePlay.ConsoleWindow]::ShowWindow($handle, 0) }
    } catch { }
}

function Show-LauncherUvMissing {
    $text = @(
        '未找到 uv，游戏暂时无法启动。',
        '',
        'uv 是免费的 Python 运行环境管理器，安装一次即可：',
        '  1. 打开 https://docs.astral.sh/uv/getting-started/installation/',
        '  2. 按页面说明完成安装（Windows 可复制页面上的 PowerShell 命令）',
        '  3. 安装完成后，重新双击「启动游戏.bat」',
        '',
        '首次运行需要联网下载游戏依赖（几分钟）；之后启动不需要联网。'
    ) -join [Environment]::NewLine
    Write-Host $text
    if ($OnWindows -and [Environment]::UserInteractive) {
        try {
            Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
            [void][System.Windows.Forms.MessageBox]::Show($text, '文字交互游戏', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning)
        } catch { }
    }
}

# ---------------------------------------------------------------------------
# -Launcher：普通玩家双击体验（启动器窗口 + 浏览器；关窗即退出）
# ---------------------------------------------------------------------------
function Invoke-LauncherMode {
    param([string]$UvExe, [int]$ListenPort, [int]$IdleSeconds)
    $baseUrl = 'http://127.0.0.1:' + $ListenPort
    $logPath = Join-Path $Root 'data\logs\launcher-server.log'
    $script:LauncherBaseUrl = $baseUrl
    $distIndex = Join-Path $Root 'web\dist\index.html'

    # b) 已有实例：只打开浏览器，绝不重复启动第二个服务
    if (Test-UrlOk -Url ($baseUrl + '/healthz')) {
        Write-Host "== 游戏已经在运行：$baseUrl"
        Write-Host '   已为你打开浏览器（不会重复启动服务）。'
        if ($OpenBrowser) { try { Start-Process $baseUrl | Out-Null } catch { } }
        return 0
    }

    if (-not (Test-Path -LiteralPath $distIndex)) {
        Show-LauncherError (@(
                '未找到网页产物 web/dist\index.html，无法启动网页版。',
                '',
                '请重新下载完整的游戏仓库（网页产物随仓库分发），',
                '或在开发环境执行 pnpm --dir web build 重新构建。'
            ) -join [Environment]::NewLine)
        return 1
    }

    $occupants = @(Get-ListeningProcessIds -LocalPort $ListenPort)
    if ($occupants.Count -gt 0) {
        Show-LauncherError (@(
                "端口 $ListenPort 已被其它程序占用（PID $($occupants -join ', ')）。",
                '',
                '请关闭占用该端口的程序，或用 -Port 换一个端口后再启动。'
            ) -join [Environment]::NewLine)
        return 1
    }

    # 依赖已由上游步骤安装（首次运行时控制台可见安装进度）
    $useGui = Test-LauncherGuiAvailable
    $script:LauncherGuiActive = $useGui
    if ($useGui) {
        try {
            New-LauncherForm | Out-Null
        } catch {
            $useGui = $false
            $script:LauncherGuiActive = $false
            Close-LauncherForm
            Write-Host '启动器窗口不可用，改用控制台模式。'
        }
    }
    $pump = {
        try { [System.Windows.Forms.Application]::DoEvents() } catch { }
        # 启动过程中玩家关闭窗口：立刻结束服务进程，避免窗口已关却还在等待
        if ($script:LauncherExitReason -and $null -ne $script:LauncherServerProc -and -not $script:LauncherServerProc.HasExited) {
            Stop-PlayServer -Process $script:LauncherServerProc
        }
    }

    Set-LauncherStatus '正在准备…（首次运行需几分钟）'
    Write-Host "== 正在启动游戏服务（$baseUrl）…"
    Write-Host "   日志：$logPath"
    $serverProc = $null
    $exitCode = 0
    $startedOk = $false
    try {
        $serverProc = Start-DrainedProcess -FilePath $UvExe -ArgumentList @(
            'run', '--no-dev', 'python', '-m', 'textgame.launcher',
            '--host', '127.0.0.1', '--port', "$ListenPort", '--idle-exit-seconds', "$IdleSeconds"
        ) -LogPath $logPath
        $script:LauncherServerProc = $serverProc
        $healthy = Wait-ForHealth -BaseUrl $baseUrl -TimeoutSeconds 90 -Process $serverProc -OnTick $pump
        if ($script:LauncherExitReason) {
            # 启动过程中玩家关闭了启动器窗口/点了退出：静默退出（finally 负责清理进程树）
            return 0
        }
        if (-not $healthy) {
            # 启动过程中被正常退出（刚打开就被退出/空闲策略）：按正常退出处理，不弹错误框
            if ($serverProc.HasExited -and $serverProc.ExitCode -eq 0) {
                Write-Host '游戏已退出，感谢游玩。'
                Show-LauncherFarewell '游戏已退出，感谢游玩。'
                return 0
            }
            $reason = '游戏启动失败：服务未在 90 秒内就绪。'
            if ($serverProc.HasExited) { $reason = "游戏启动失败：启动器进程提前退出（退出码 $($serverProc.ExitCode)）。" }
            $tail = @(Get-LogTail -Path $logPath -Lines 5)
            $message = $reason + [Environment]::NewLine + [Environment]::NewLine + "日志：$logPath"
            if ($tail.Count -gt 0) { $message += [Environment]::NewLine + '最后几行：' + [Environment]::NewLine + ($tail -join [Environment]::NewLine) }
            Show-LauncherError $message
            return 1
        }
        $startedOk = $true

        Set-LauncherStatus '已启动，正在打开浏览器…'
        if ($OpenBrowser) { try { Start-Process $baseUrl | Out-Null } catch { } }
        Set-LauncherStatus '游戏运行中：在游戏页面点「退出游戏」，或关闭本窗口退出。'

        if ($useGui) {
            Hide-ConsoleWindow
            try { $script:LauncherForm.Activate() } catch { }
            $exitReason = ''
            while ($true) {
                try { [System.Windows.Forms.Application]::DoEvents() } catch { }
                Update-LogDrain -Process $serverProc
                if ($script:LauncherExitReason) { $exitReason = $script:LauncherExitReason; break }
                if ($serverProc.HasExited) { $exitReason = 'server'; break }
                Start-Sleep -Milliseconds 200
            }
            Close-LauncherForm
            if ($exitReason -eq 'server') {
                # 玩家在网页点「退出游戏」或空闲自动退出：服务进程已自行结束
                Complete-LogDrain -Process $serverProc
                Show-LauncherFarewell '游戏已退出，感谢游玩。'
            } else {
                # 玩家关闭启动器窗口或点「退出游戏」按钮：先等优雅退出，再兜底结束进程树
                $graceful = Wait-ProcessDrained -Process $serverProc -TimeoutSeconds 5
                if (-not $graceful) {
                    Write-Host '正在结束后台游戏进程…'
                    Stop-PlayServer -Process $serverProc
                    [void](Wait-ProcessDrained -Process $serverProc -TimeoutSeconds 10)
                } else {
                    Write-Host '游戏已退出，感谢游玩。'
                }
            }
            return 0
        }

        # 非 Windows / 无图形环境：控制台降级模式
        Write-Host "== 启动器（控制台模式）：游戏运行中 $baseUrl"
        Write-Host '   在游戏页面点「退出游戏」，或在本窗口按 Ctrl+C / 关闭本窗口退出。'
        try {
            while (-not $serverProc.HasExited) {
                Update-LogDrain -Process $serverProc
                Start-Sleep -Milliseconds 400
            }
        } finally {
            if (-not $serverProc.HasExited) { Stop-PlayServer -Process $serverProc }
            [void](Wait-ProcessDrained -Process $serverProc -TimeoutSeconds 10)
        }
        Write-Host '游戏已退出，感谢游玩。'
        return 0
    } catch {
        Show-LauncherError (@(
                '游戏启动失败：' + $_.Exception.Message,
                '',
                "日志：$logPath",
                '请关闭本窗口后重新双击「启动游戏.bat」；若反复失败，请把日志内容反馈给开发者。'
            ) -join [Environment]::NewLine)
        return 1
    } finally {
        if ($null -ne $serverProc -and -not $serverProc.HasExited) { Stop-PlayServer -Process $serverProc }
        Stop-LogDrain
        Close-LauncherForm
        if ($null -ne $serverProc) { try { $serverProc.Dispose() } catch { } }
        $script:LauncherServerProc = $null
        $script:LauncherExitReason = ''
        if ($startedOk) { Write-Host "== 游戏进程已结束（端口 $ListenPort 已释放）。" }
    }
    return $exitCode
}

# ---------------------------------------------------------------------------
# -LauncherCheck：headless 自检（不弹窗、不开浏览器、临时数据库、自动空闲端口）
# ---------------------------------------------------------------------------
function Invoke-LauncherCheck {
    param([string]$UvExe, [int]$RequestedPort)
    Write-Host '== Launcher 自检：启动 launcher 控制面（无窗口、无浏览器、临时数据库）…'

    $listenPort = $RequestedPort
    if (@(Get-ListeningProcessIds -LocalPort $listenPort).Count -gt 0) {
        $listenPort = Find-FreePort -StartPort ($listenPort + 1)
        Write-Host "提示：端口 $RequestedPort 被占用，自检改用空闲端口 $listenPort。"
    }
    $baseUrl = 'http://127.0.0.1:' + $listenPort

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('textgame-launcher-check-' + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    $logPath = Join-Path $tempRoot 'launcher-server.log'
    $oldDbUrl = $env:TEXTGAME_DB_URL
    $oldLlmEnabled = $env:TEXTGAME_LLM_ENABLED
    $serverProc = $null
    try {
        $dbPath = Join-Path $tempRoot 'textgame.db'
        $env:TEXTGAME_DB_URL = 'sqlite:///' + ($dbPath -replace '\\', '/')
        $env:TEXTGAME_LLM_ENABLED = 'false'   # 自检强制离线：确定性、零联网
        Write-Host "   端口：$listenPort ｜ 临时库：$dbPath"
        $serverProc = Start-DrainedProcess -FilePath $UvExe -ArgumentList @(
            'run', '--no-dev', 'python', '-m', 'textgame.launcher',
            '--host', '127.0.0.1', '--port', "$listenPort", '--idle-exit-seconds', '0'
        ) -LogPath $logPath

        $healthy = Wait-ForHealth -BaseUrl $baseUrl -TimeoutSeconds 90 -Process $serverProc
        if (-not $healthy) {
            if ($serverProc.HasExited) {
                Write-Host "Launcher 自检失败：launcher 进程提前退出（退出码 $($serverProc.ExitCode)）。"
                Write-Host '  （若 src/textgame/launcher.py 尚未实现，会在这里看到 ModuleNotFoundError）'
            } else {
                Write-Host 'Launcher 自检失败：服务未在 90 秒内就绪。'
            }
            Show-LogTail -Path $logPath
            return 1
        }
        Write-Host "  [1/5] /healthz 就绪：$baseUrl"

        $status = $null
        try {
            $status = Invoke-JsonGet -Url ($baseUrl + '/api/v1/app/status') -TimeoutSec 5
        } catch {
            Write-Host "Launcher 自检失败：GET /api/v1/app/status 出错（$($_.Exception.Message)）。"
            Show-LogTail -Path $logPath
            return 1
        }
        if ($status.StatusCode -ne 200) {
            Write-Host "Launcher 自检失败：GET /api/v1/app/status 返回 $($status.StatusCode)，期望 200。"
            Show-LogTail -Path $logPath
            return 1
        }
        $statusBody = $status.Body
        if ($null -eq $statusBody -or $statusBody.launcher_mode -ne $true) {
            Write-Host 'Launcher 自检失败：status.launcher_mode 不为 true（服务未以 launcher 模式启动）。'
            Show-LogTail -Path $logPath
            return 1
        }
        if ($statusBody.shutdown_supported -ne $true) {
            Write-Host 'Launcher 自检失败：status.shutdown_supported 不为 true（控制面不可用）。'
            Show-LogTail -Path $logPath
            return 1
        }
        Write-Host "  [2/5] GET /api/v1/app/status → 200（launcher_mode=true，shutdown_supported=true，idle_exit_seconds=$($statusBody.idle_exit_seconds)，version=$($statusBody.version)）"

        $heartbeat = $null
        try {
            $heartbeat = Invoke-JsonPost -Url ($baseUrl + '/api/v1/app/heartbeat') -Body '{}' -TimeoutSec 5
        } catch {
            Write-Host "Launcher 自检失败：POST /api/v1/app/heartbeat 出错（$($_.Exception.Message)）。"
            Show-LogTail -Path $logPath
            return 1
        }
        if ($heartbeat.StatusCode -ne 200) {
            Write-Host "Launcher 自检失败：POST /api/v1/app/heartbeat 返回 $($heartbeat.StatusCode)，期望 200。"
            Show-LogTail -Path $logPath
            return 1
        }
        Write-Host "  [3/5] POST /api/v1/app/heartbeat → 200（idle_exit_seconds=$($heartbeat.Body.idle_exit_seconds)）"

        $shutdown = $null
        try {
            $shutdown = Invoke-JsonPost -Url ($baseUrl + '/api/v1/app/shutdown') -Body '{"confirm":true}' -TimeoutSec 5
        } catch {
            Write-Host "Launcher 自检失败：POST /api/v1/app/shutdown 出错（$($_.Exception.Message)）。"
            Show-LogTail -Path $logPath
            return 1
        }
        if ($shutdown.StatusCode -ne 200) {
            Write-Host "Launcher 自检失败：POST /api/v1/app/shutdown 返回 $($shutdown.StatusCode)，期望 200。"
            Show-LogTail -Path $logPath
            return 1
        }
        if ($shutdown.Body.ok -ne $true) {
            Write-Host 'Launcher 自检失败：POST /api/v1/app/shutdown 响应 ok 不为 true。'
            Show-LogTail -Path $logPath
            return 1
        }
        Write-Host '  [4/5] POST /api/v1/app/shutdown → 200（ok=true）'

        $exited = Wait-ProcessDrained -Process $serverProc -TimeoutSeconds 15
        if (-not $exited) {
            Write-Host 'Launcher 自检失败：shutdown 后进程未在 15 秒内退出。'
            Show-LogTail -Path $logPath
            return 1
        }
        $released = $false
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Milliseconds 250
            if (@(Get-ListeningProcessIds -LocalPort $listenPort).Count -eq 0) { $released = $true; break }
        }
        if (-not $released) {
            Write-Host "Launcher 自检失败：shutdown 后端口 $listenPort 仍被占用。"
            Show-LogTail -Path $logPath
            return 1
        }
        Write-Host "  [5/5] 进程已退出且端口 $listenPort 已释放"
        Write-Host ''
        Write-Host 'Launcher 自检通过'
        return 0
    } catch {
        Write-Host "Launcher 自检失败：$($_.Exception.Message)"
        Show-LogTail -Path $logPath
        return 1
    } finally {
        if ($null -ne $serverProc -and -not $serverProc.HasExited) { Stop-PlayServer -Process $serverProc }
        Stop-LogDrain
        if ($null -ne $serverProc) { try { $serverProc.Dispose() } catch { } }
        if ($null -ne $oldDbUrl) { $env:TEXTGAME_DB_URL = $oldDbUrl } else { Remove-Item Env:\TEXTGAME_DB_URL -ErrorAction SilentlyContinue }
        if ($null -ne $oldLlmEnabled) { $env:TEXTGAME_LLM_ENABLED = $oldLlmEnabled } else { Remove-Item Env:\TEXTGAME_LLM_ENABLED -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------------
# -Stop：兜底退出（退出游戏.bat 调用；按端口精确匹配，误杀风险最小）
# ---------------------------------------------------------------------------
if ($Stop) {
    $targets = @(Get-ListeningProcessIds -LocalPort $Port)
    if ($targets.Count -eq 0) {
        Write-Host "游戏没有在运行（127.0.0.1:$Port 没有监听的游戏进程）。"
        exit 0
    }
    $failedPids = @()
    foreach ($targetPid in $targets) {
        Write-Host "找到游戏进程 PID $targetPid（端口 $Port），正在结束…"
        & taskkill /T /F /PID $targetPid | Out-Null
        if ($LASTEXITCODE -ne 0) { $failedPids += $targetPid }
    }
    $released = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 250
        if (@(Get-ListeningProcessIds -LocalPort $Port).Count -eq 0) { $released = $true; break }
    }
    if ($released) {
        Write-Host "已退出游戏（端口 $Port 已释放）。"
        exit 0
    }
    Write-Host "退出失败：端口 $Port 仍被占用（PID：$($failedPids -join ', ')）。请稍后重试。"
    exit 1
}

# 1) 前置：uv（唯一必需前置；Python 由 uv 自动下载管理）
$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $UvCommand) {
    if ($Launcher) { Show-LauncherUvMissing; exit 2 }
    Write-Host '未找到 uv：请先安装 https://github.com/astral-sh/uv 后重试。'
    exit 1
}
$UvExe = $UvCommand.Source

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

# 3.1) 自动化验收：launcher 控制面 headless 自检（不弹窗、不开浏览器）
if ($LauncherCheck) {
    exit (Invoke-LauncherCheck -UvExe $UvExe -RequestedPort $Port)
}

# 3.2) 普通玩家：启动器窗口 + 浏览器（关闭窗口即退出；空闲自动退出由服务端负责）
if ($Launcher) {
    exit (Invoke-LauncherMode -UvExe $UvExe -ListenPort $Port -IdleSeconds $IdleExitSeconds)
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
