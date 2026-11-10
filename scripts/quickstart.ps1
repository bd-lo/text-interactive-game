# 终端版一键入口（兼容旧命令）：等价于 .\scripts\play.ps1 -Terminal
# 网页版优先入口见 .\scripts\play.ps1（有 web/dist 时自动打开 http://127.0.0.1:8000）
& (Join-Path $PSScriptRoot 'play.ps1') -Terminal
exit $LASTEXITCODE
