# 057 · 2026-11-11 · N5 · 玩家启动器与退出体验（双击启动 / 游戏内退出 / 空闲自动退出）

> 切片目标：普通玩家「像正常游戏一样启动和退出」——Windows 双击 `启动游戏.bat` 启动（启动器窗口 + 浏览器），
> 游戏内「退出游戏」优雅退出，关浏览器 5 分钟空闲自动退出，`退出游戏.bat` 兜底。
> 编号区间：N5 057–060 ｜ 决策：[D-LA-1..N6](../decisions/2026-11-11-player-launcher-and-exit.md) ｜
> 路线图：第 17 章 §17.9（N5 发布体验轨）

## 1. 目标与验收命令

- 目标：把 0.2.0 的「终端敲命令启动 / Ctrl+C 退出」升级为双击启动 + 页内退出；默认 dev/CI 行为零变化。
- 验收命令（先定后写）：
  - `uv run pytest -q` → 2186 passed（基线 2156 + 30）
  - `uv run pytest tests/replay -q` → 68 passed
  - `uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run lint-imports` → 全绿
  - `uv run python scripts/check_docs.py` → PASS
  - `pnpm --dir web test` → 56 files/347 tests；`typecheck`/`build`；tracked `web/dist` 零漂移
  - `pwsh -File scripts/play.ps1 -LauncherCheck -Port <空闲>` → 「Launcher 自检通过」exit 0
  - `pwsh -File scripts/play.ps1 -Check` → 旧口径 PASS
  - 浏览器 e2e：非启动器 **26 步/125 断言**；启动器模式 **26 步/126 断言**（最后一步真实退出服务）
- 不做什么：不做 exe/安装包（用户 2026-11-11 明确「算了」；仅保留 N5 058 评估位）、不做系统托盘/自动更新。

## 2. 交付内容

### 2.1 后端控制面（task-6 / backend-scenario）

- 新增 `src/textgame/launcher_control.py`：无框架依赖 `LauncherController`（心跳时间、幂等 `request_shutdown`
  0.2s 延迟 timer → `server.should_exit`、`idle_exit_due` 严格 > 阈值且需 ≥1 次心跳、0=关闭、`now` 可注入）。
- 新增 `src/textgame/api/routes/app_control.py`：`GET /api/v1/app/status`（永远 200；无控制器
  `launcher_mode=false/shutdown_supported=false/idle_exit_seconds=0`）、`POST /heartbeat`（无控制器 404）、
  `POST /shutdown`（`{"confirm":true}`；无控制器 404、非回环 403、confirm 非 true 422、重复幂等 200；
  无 `Idempotency-Key` 要求）。
- 新增 `src/textgame/launcher.py`：`python -m textgame.launcher --host/--port/--idle-exit-seconds/--log-level`；
  `uvicorn.Server` + 注入控制器；`LAUNCHER: ready/exit reason=...` 日志；空闲 monitor daemon；
  `TEXTGAME_IDLE_EXIT_SECONDS`（默认 300）。
- 修改 `api/schemas.py`（4 个响应/请求模型）、`api/app.py`（`create_app(launcher_controller=None)` + router）、
  `main.py`（`_build_app(launcher_controller=None)` 透传，`app = _build_app()` 不变）、`config/settings.py`
  （`idle_exit_seconds: int = 300, ge=0`；不引入 env 开关：控制面可用性由控制器注入决定）。
- 测试 +29：`tests/unit/test_launcher_control.py`(12)、`tests/integration/test_app_control_api.py`(15)、
  `tests/integration/test_launcher_process.py`(2，真实子进程 shutdown 后 exit 0)。

### 2.2 前端退出体验（task-7 / frontend-scenario）

- `web/src/api/{types,client}.ts`：4 个 TS 接口 + 3 条 `API_PATHS` + `getAppStatus/sendLauncherHeartbeat/shutdownGame`
  （运行时 `assertKeys` 字段校验、5s/5s/8s 超时、无幂等键）。
- 新增 `web/src/components/QuitGameControl.vue` + `web/src/utils/launcher.ts`：全局 header 控件状态机
  loading(隐藏)→idle→confirm/info→exited；确认 → POST shutdown → 立即全屏「游戏已退出 / 可以关闭此页面了。」
  覆盖层（role=status aria-live=polite）；非启动器模式固定文案「当前不是启动器模式：请在运行服务的窗口按
  Ctrl+C 退出。」；失败显示人话错误并恢复可点、无自动重试；心跳 15s（`setInterval` + 行尾
  `// 心跳 keep-alive` 白名单；`visibilitychange` 可见时立即补一拍），非启动器模式零心跳。
- `App.vue` 用 `defineAsyncComponent` 挂载（避免把 API client 拖进入口 bundle：QuitGameControl 独立 chunk ~2.7KB）。
- `tests/integration/test_frontend_contract.py`：新增 3 response + 1 request 映射、3 条路径、方法/路径/schema/无幂等键断言；
  **安全门禁窄放宽**：`setInterval` 静态禁令新增唯一例外——仅 `QuitGameControl.vue` 中行尾标注
  `// 心跳 keep-alive` 的那一行（文件+标注双重限定，其余一律 BLOCKED；Lead 已复核并签字）。
- 测试 +18（client 7 / 心跳 6 / 控件 5），`web/dist` 重建。

### 2.3 启动器脚本与文档（task-8 / launcher-ux）

- `scripts/play.ps1`：新增 `-Launcher`（WinForms 小窗 + 隐藏服务 + 自动开浏览器 + 已有实例只开浏览器 +
  uv 缺失中文指引 + 日志追加 `data/logs/launcher-server.log` + 关窗/退出清理进程树 + 无 GUI 降级控制台）、
  `-LauncherCheck`（headless 自检）、`-Stop`（netstat 四列精确匹配 PID → taskkill /T /F，禁进程名宽泛匹配）；
  原有默认/`-Check`/`-Terminal`/`-Port`/`-Llm` 行为逐字保留。
- 根目录新增 `启动游戏.bat`、`退出游戏.bat`（纯 ASCII + CRLF；中文输出走 UTF-8 BOM 的 ps1）。
- `scripts/play.sh`：`--launcher`、`--launcher-check`（trap 清理；默认/`--check`/`--terminal` 不变）。
- 文档：README/README.en 快速开始改「双击启动器优先」+ FAQ（怎么退出/首次启动慢）、RELEASE.md（§2.1 L1–L3 启动器门禁）、
  deployment-and-distribution（分发形态 A + 启动器/控制面/退出状态机）、`.env.example`（`TEXTGAME_IDLE_EXIT_SECONDS`）。
- 实跑：`LauncherCheck` 连跑 3 次 PASS；`-Check` PASS；GUI 真实路径（Windows PowerShell 5.1）窗口退出/关窗/竞态均 exit 0、
  端口释放；已有实例只开浏览器；`退出游戏.bat` 无实例/真实 listener 两分支均正确；`bash -n scripts/play.sh` exit 0。

### 2.4 Lead 集成

- 版本 `0.2.0 → 0.3.0`（`pyproject`/`__version__`/`uv.lock`）、CHANGELOG `[0.3.0]`、README/README.en/RELEASE 版本与发布段。
- e2e 新增最后一步 `app.exit`：CI/非启动器模式断言说明面板；启动器模式断言确认→覆盖层→`/healthz` 不可达。
- 安全门禁白名单从「行级标记」收紧为「文件 + 行级标记」双重限定；ADR 记录签字。
- 删除未使用的 `launcher_mode` 设置项（控制面可用性由控制器注入决定，避免死配置）。

## 3. 验证结果（Lead 实跑）

| 项 | 结果 |
| --- | --- |
| 全量 pytest | **2186 passed / 0 failed**（基线 2156 + 30） |
| 回放 | `tests/replay` **68 passed**（旧 golden 零改动） |
| web | `pnpm test` **56 files/347 tests**；`typecheck` 0；`build` OK；tracked `web/dist` 零漂移 |
| 浏览器 e2e（非启动器，CI 口径） | **26/26 步、125/125 断言**，最后 `app.exit` 走说明面板分支 |
| 浏览器 e2e（启动器模式，本地证据） | **26/26 步、126/126 断言**：确认退出 → 覆盖层 → `/healthz` 不可达 → 服务进程 exit 0 |
| 启动器 headless | `play.ps1 -LauncherCheck -Port 8821` → **Launcher 自检通过**（status/heartbeat/shutdown/退出/端口释放） |
| 默认网页自检 | `play.ps1 -Check` → `/healthz=200`、`/=200`（旧行为未破坏） |
| 终端/演示确定性 | `demo --check` 7/7 逐字、`console --check` 4/4（无控制器默认路径零变化） |
| 质量门 | ruff format/check（231 files）、mypy strict（110 files）、lint-imports（2 kept）、check_docs **PASS** |
| 数据库 | 无迁移（head 仍 0005） |

## 4. 已知限制 / 后续

1. GUI 启动器窗口仅 Windows（WinForms）；Linux/macOS/无 GUI 自动降级控制台模式（文档已写）；
2. L2「启动器窗口人工冒烟」需发布前人工过一遍（自动验收只覆盖无 GUI 控制面与网页 e2e）；
3. 首次启动仍需 uv + 联网 `uv sync`（同步期在控制台可见进度；启动器窗口在依赖就绪后才弹出）；
4. 后台标签页会被浏览器节流（任一 timer 方案）；`visibilitychange` 可见时立即补拍降低误退风险，仍建议把
   `TEXTGAME_IDLE_EXIT_SECONDS` 理解为「无页面心跳容忍窗口」（默认 300s）；
5. exe/安装包不做（用户 2026-11-11 明确不需要；N5 058 仅保留评估位）；
6. `launcher.py` 启动时 `import textgame.main` 会先执行一次模块级 `app = _build_app()`（组合根既有行为），
   随后再建带控制器 app；功能/退出均验证正常，消除该冗余需把组合根改成惰性 app（未做，另行评估）。
