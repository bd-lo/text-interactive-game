# 036 · 2026-11-12 · N1 · 示例内容包一键体验脚本（PowerShell 5.1/7 + bash）

> 切片目标：让玩家一条命令校验/导入 `examples/content-pack/` 并直接进入对局；无 GUI 环境可 headless 自检。
> 验收命令：`pwsh/powershell -File scripts/quickstart-content-pack.ps1 -Check`、
> `bash scripts/quickstart-content-pack.sh --check` ｜ 编号区间 N1 029–036 ｜ 上游 035（内容包）

## 1. 目标与验收命令

- 目标（垂直小功能）：新增 `scripts/quickstart-content-pack.ps1` 与 `.sh`——**临时数据库 + 显式离线**启动网页
  服务，把内容包每个 JSON 经既有 `validate`/`import` 端点导入，建一局「灰烬灯塔 + 世界书」，打开
  `/game/<id>`；`-Check`/`--check` 全程 headless 且退出码表达成败；退出清理临时库（`-KeepDb`/`--keep-db` 保留）。
- 验收命令/断言（先定后写）：
  - `pwsh -File scripts/quickstart-content-pack.ps1 -Check` → `PASS` + exit 0（PowerShell 7）
  - `powershell.exe -NoProfile -ExecutionPolicy Bypass -File …-Check` → `PASS` + exit 0（Windows PowerShell 5.1）
  - `bash scripts/quickstart-content-pack.sh --check` → `PASS` + exit 0（Git Bash；Linux/mac 同构）
  - `scripts/play.ps1 -Check` → 既有入口仍 `自检通过`
- 不做什么：不改 `play.ps1`/`play.sh`/`quickstart.ps1` 既有入口；不新增依赖；不复制/打印密钥；
  不改 `web/`（复用既有导入/对局/场景库页面，无新路由）。

## 2. 改动与提交

- 文件：`scripts/quickstart-content-pack.ps1`（UTF-8 **with BOM** + CRLF，PS 5.1/7 兼容）、
  `scripts/quickstart-content-pack.sh`（LF，复用 `play.sh` 的 uv/uvicorn/健康检查口径）、本进度文档；
  内容包入口说明写入 `examples/content-pack/README.md`（按并发约束不改第 23 章）。
- 提交：**未提交**（Lead 统一提交）。
- 关键 diff 摘要（≤5 行）：脚本设 `TEXTGAME_DB_URL=临时 SQLite`、`TEXTGAME_LLM_ENABLED=false`、
  临时凭据路径 → 与 `play.ps1`/`play.sh` 相同的 `uv run --no-dev uvicorn textgame.main:app` 启动并等待
  `/healthz`；内嵌纯标准库 Python 客户端（urllib，ProxyHandler({}) 只连回环）逐文件校验/导入/建局；
  失败时转储服务日志尾并 exit 1；交互模式启动浏览器、`Ctrl+C` 结束并清理，`-Check` 不弹窗不写仓库 data/。
- 护栏检查：不新增依赖；脚本不含/不打印 API Key（显式离线）；临时目录退出即删；服务只监听 127.0.0.1。
- 契约变更：无（只调用既有端点）。

## 3. 验证结果

- PowerShell 7：`-Check` → `RESULT=PASS` + `PASS: 内容包自检通过（2 场景…对局 game_…）`，exit 0；
- Windows PowerShell 5.1：同上 `-Check` → PASS，exit 0（BOM/CRLF/中文正常，无 parse error）；
- bash（Git Bash，Windows 路径经 `cygpath -m` 转写）：`--check` → PASS，exit 0；
- 交互模式（`-NoBrowser -Port 8011` 手跑）：`/healthz` 200、导入+建局成功、结束服务后端口释放、临时库清理；
- 既有入口：`scripts/play.ps1 -Check` → `自检通过：/healthz=200，/=200`；`demo --check` 7/7、`console --check` 4/4 不变；
- 门禁：变更 py 仅测试文件，`ruff format/check` 通过；`mypy src/textgame` Success（118 files）。
- 失败与回滚：无（未 commit；新增脚本删除即回滚）。

## 4. 下一步与风险

- 下一步：040 验收（真实 LLM 流式 + README/第 12 章回写）；N1 033–034 跑团模组待用户拍板（D-N6）。
- 风险/开放问题：默认端口 8000 被占用时交互模式直接提示换 `-Port`（`-Check` 自动向后找空端口）；
  Linux/mac 若 `*.sh` 无执行位可 `bash scripts/quickstart-content-pack.sh --check`；
  首次运行仍需联网装依赖（与 `play.ps1` 相同前置）；脚本只做导入/建局，不代替 040 的真实 LLM 手跑。
- 需要 Lead 处理：`docs/PROGRESS.md` §3 索引（035/036 两篇）与 §4 N1 编号（029–036 用尽）；
  第 23 章内容包入口说明由 Lead 集成时补（并发约束：前端队友正在改同一章，本切片未改章文件）。
