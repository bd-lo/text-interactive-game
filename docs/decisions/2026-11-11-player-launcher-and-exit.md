# ADR · 玩家启动器与退出体验（D-LA-1..N6）

- 状态：已定案 ｜ 决策人：lead ｜ 复核人：独立复核（脚本实跑 + 浏览器 e2e，`.work` 证据不入库） ｜ 日期：2026-11-11 ｜ 基线：`2aeaa54`
- 关联：第 17 章 §17.9 **N5 发布体验轨（057–060，057 本批）**；路线图 [D-N1..N7](2026-11-11-post-m6-roadmap.md)；
  R-T8（LLM 延迟）不涉及；R2 的「一键入口」后续（`play.ps1`）；用户需求（2026-11-11）：
  「Release 有没有什么好的实现方式，可以让普通玩家像正常玩游戏一样启动和退出」。

## 1. 背景与红线

- 现状：玩家启动 = 终端执行 `scripts/play.ps1`（需要 uv、PowerShell 执行策略知识）；退出 = 在终端按 Ctrl+C。
  对「普通玩家」不是正常游戏体验；0.2.0 已发布（tag `v0.2.0`），这是发布形态上最后一块明显的体验缺口。
- 目标：**双击即可玩、游戏内可退出**；关闭浏览器后不留下后台进程；出问题有兜底退出方式。
- 红线：不新增依赖；不改 `/turns`/事件/`GameState`/golden；默认 dev/CI 行为零变化（控制面不可用）；
  不写用户数据、不联网（除本机回环）、不暴露新攻击面（仅回环 + confirm + 仅启动器模式可用）。

## 2. 决策

### D-LA-1 启动器形态：双击 `启动游戏.bat` → `play.ps1 -Launcher`

- 决策：Windows 普通玩家双击仓库根 `启动游戏.bat`（ASCII 内容 .bat 调 `powershell -ExecutionPolicy Bypass -File scripts\play.ps1 -Launcher`）。
  `-Launcher` 行为：preflight（uv 缺失给中文说明 + 安装网址，不自动安装/不下载脚本）→ 已有实例则只打开浏览器 →
  首次无 `.venv` 先 `uv sync --frozen --no-dev`（显式提示「首次运行需几分钟」）→ 以隐藏方式启动
  `python -m textgame.launcher`（日志追加到 `data/logs/launcher-server.log`）→ 等 `/healthz` ≤90s → 打开默认浏览器 →
  等待服务退出并自动关闭启动器；玩家关闭启动器窗口 → `taskkill /T /F` 清理服务进程树。
- 界面：Windows 优先 WinForms 小窗口（标题/状态/「打开游戏」「退出游戏」按钮）；非 Windows 或无 GUI 自动降级为
  控制台模式（同样清晰的中文提示）。GUI 只是壳，**不承载业务逻辑**。
- 被否：① 打包 exe（PyInstaller 等新增构建依赖/体积大/签名问题，留 057+ 评估）；② 自动下载安装 uv
  （供应链风险 + 静默执行外部脚本，违反安全口径）；③ 仅文档教玩家敲命令（不满足「正常游戏」诉求）。
- 回写落点：README/README.en 快速开始、`docs/design/deployment-and-distribution.md`、RELEASE.md。
- 验收断言：`pwsh -File scripts/play.ps1 -LauncherCheck -Port <空闲>` 全绿；`play.ps1 -Check` 旧口径仍 PASS。

### D-LA-2 控制面：`/api/v1/app/status|heartbeat|shutdown`（仅启动器模式可用）

- 决策：新增 3 个控制端点（详见任务冻结契约）：
  - `GET /api/v1/app/status` → 永远 200 `{launcher_mode, idle_exit_seconds, shutdown_supported, version}`；
    无控制器时 `launcher_mode=false, shutdown_supported=false, idle_exit_seconds=0`。
  - `POST /api/v1/app/heartbeat` → 200 `{ok, idle_exit_seconds}`；无控制器 404。
  - `POST /api/v1/app/shutdown`（`{"confirm": true}`）→ 200 `{ok, message}`；无控制器 404；非回环 403；重复幂等。
  控制器只在 `python -m textgame.launcher` 入口注入；`uvicorn textgame.main:app` / 测试 / CI 均无控制器。
- 理由：端点常开但能力由控制器门控，老部署/CI 行为零变化；前端凭 `status` 决定显示与动作，无需试探 404。
- 被否：① 用 `os.kill(os.getpid(), SIGINT)` 直接杀进程（无法优雅 flush/日志，Windows 语义差）；
  ② 端点永远可用（dev 服务器可被网页误关）；③ 用 Idempotency-Key 表做控制面（控制面不是领域写，幂等由控制器状态保证）。
- 回写落点：第 12/23 章、`.env.example`、README API 段（可选）。
- 验收断言：`test_app_control_api.py` 全分支；子进程测试 shutdown 后 returncode=0；无控制器 404。

### D-LA-3 退出语义：游戏内按钮 / 关浏览器空闲退出 / 兜底脚本

- 决策（三条退出通道，层层兜底）：
  1. **游戏内「退出游戏」**：全局 header 按钮 → 二次确认 → `POST /api/v1/app/shutdown` → 服务优雅退出 →
     前端立即显示全屏「游戏已退出 / 可以关闭此页面了。」；启动器检测到进程结束自动关闭。
  2. **关闭浏览器/标签页**：前端在 `launcher_mode` 下每 15s 心跳；服务端在「收到过心跳 且 距最近心跳 >
     `TEXTGAME_IDLE_EXIT_SECONDS`（默认 300s，0=关闭）」时自动优雅退出（杀软/崩溃时最多残留 5 分钟）。
  3. **`退出游戏.bat` 兜底**：无游戏内页面可用（浏览器崩了/端口卡死）时双击，按精确端口找 PID → `taskkill /T /F`。
- 语义：退出**不写任何领域事件/状态**，存档本来就在每次回合提交后落库；退出后下次启动自动继续最近存档。
- 被否：① 关闭标签页立即退出（浏览器后台标签/多标签会误杀；5 分钟窗口更稳）；② 进程常驻托盘（新增 GUI 常驻
  组件与依赖，收益低）；③ 退出时做「保存」动画（无必要，回合已提交）。
- 回写落点：README FAQ、第 23 章、`docs/design/deployment-and-distribution.md`。
- 验收断言：浏览器 e2e 在启动器模式分支点「确认退出」→ 覆盖层出现 → `/healthz` 不可达；非启动器模式分支只显示说明面板；
  `LauncherCheck` 验证 shutdown 后进程退出；前端 vitest 覆盖心跳/覆盖层/失败重试一次。

### D-LA-4 安全边界

- 决策：`shutdown` 仅接受回环 client（`127.0.0.1/::1/localhost`；测试进程的 `testclient` 视作回环），
  必须 JSON `{"confirm": true}`（CORS 预检保护跨站简单请求），无控制器一律 404；不新增 CORS；不记录/回显任何密钥；
  launcher 只监听 `127.0.0.1`（保持现状）。
- 理由：单机单用户没有账号体系，控制面唯一可被滥用点是「本机其它程序/网页关游戏」——即使发生损失也只是 DoS 一局游戏，
  但仍以回环 + confirm + 门控三重限制收窄。
- 被否：token 鉴权（前端与启动器共享随机 token 需注入 index.html/额外启动参数，复杂度高、收益低）；
  localhost-only 靠反向代理（过度设计）。
- **门禁窄放宽（Lead 签字，2026-11-11）**：前端静态门禁 `_FORBIDDEN_SOURCE_PATTERNS["自动定时重试"]`
  原全禁 `setInterval(`；启动器心跳是固定节拍存活上报（非失败自动重试），经复核后新增唯一例外——
  **仅 `QuitGameControl.vue` 中行尾标注 `// 心跳 keep-alive` 的那一行**可通过（文件 + 标注双重限定；
  其它文件/其它行仍 BLOCKED）。该例外在 `tests/integration/test_frontend_contract.py` 有明确注释与负向路径。
- 回写落点：第 15/20 章风险注记（可选）。
- 验收断言：非回环 host → 403；`confirm=false` → 422；无控制器 → 404。

### D-LA-5 默认行为零变化（dev/CI/终端）

- 决策：不传控制器（即 `uvicorn textgame.main:app`、`demo`、`console`、全部既有测试）时：
  `status` 报非启动器模式、`heartbeat/shutdown` 返回 404；前端按钮可见但显示「当前不是启动器模式：请在运行服务的窗口按
  Ctrl+C 退出。」；CI e2e 走该分支断言，不触碰服务生命周期。
- 理由：保住 golden/回放/既有 e2e/CI；启动器是「发布形态」不是「运行默认」。
- 回写落点：第 17 章 §17.9 N5、README。
- 验收断言：全量 pytest 基线不回归（旧数字 + 新增测试）；e2e 旧 25 步不变 + 新 1 步；`demo/console --check` 逐字不变。

### D-LA-6 版本、文档与发布动作

- 决策：本批为玩家可见能力（发布体验），版本 `0.2.0 → 0.3.0`；新增进度文档 `057-2026-11-11-player-launcher.md`；
  ADR 本文件；README/README.en/RELEASE/部署文档/`.env.example` 同批回写；CHANGELOG `[0.3.0]`；
  GitHub Release 与 PyPI 仍由维护者决定（本批只 push main；tag `v0.3.0` 由维护者拍板）。
- 理由：RELEASE.md 版本策略「次版本 = 玩家可见能力成批交付」；0.3.0 = 发布体验批次。
- 被否：0.2.1（这是新能力不是修复）；立刻打 v0.3.0 tag（用户只说 push 时 push；tag 等验收/拍板）。
- 回写落点：CHANGELOG/README/RELEASE/PROGRESS/RELEASE.md。
- 验收断言：`check_docs` PASS、`docs/开发文档.md` 重建一致、CHANGELOG/README/RELEASE 版本文字一致。

## 3. 契约→切片映射

| 决策 | 实现（文件） | 测试/证据 |
| --- | --- | --- |
| D-LA-1 启动器 | `scripts/play.ps1 -Launcher/-LauncherCheck`、`启动游戏.bat`、`退出游戏.bat`、`scripts/play.sh` | `play.ps1 -LauncherCheck` PASS + `-Check` 旧口径 PASS |
| D-LA-2 控制面 | `launcher_control.py`、`api/routes/app_control.py`、`api/schemas.py`、`api/app.py`、`main.py`、`launcher.py` | 3 个测试文件 + 子进程测试 |
| D-LA-3 退出 | `web/src/components/QuitGameControl.vue`、`App.vue`、client/types、心跳 | vitest + e2e 新步骤 + LauncherCheck |
| D-LA-4 安全 | app_control 回环/confirm/404 分支 | API 全分支测试 |
| D-LA-5 零变化 | 默认 controller=None | 全量 pytest/replay/e2e 旧步骤不变 |
| D-LA-6 发布 | 版本/文档/进度/ADR | check_docs + 构建 |

## 4. 已知问题归属

| 编号 | 摘要 | 归属 | 关闭条件 |
| --- | --- | --- | --- |
| — | 首次启动需 uv + 联网 sync，初学者仍可能卡住 | 057 启动器文档 | README「首次启动慢」FAQ + 启动器显式提示；后续可评估便携包/PyInstaller |
| — | 无 GUI 环境（SSH/Linux 无桌面）启动器降级为控制台 | 057 | `-Launcher` 非 Windows 走控制台模式并提示 |
| — | 多标签页/后台标签心跳节流可能触发自动退出 | 057 | 阈值 300s 且心跳仅 launcher 模式；FAQ 说明 |
| — | 硬杀/断电时端口进程残留 | 057 | `退出游戏.bat` 兜底 + 重启时「已有实例」检测 |
| R-T8 | LLM 首字延迟（流式） | N2 037–040 | 与启动器无关，不改变状态 |

## 5. 出口验收判据与到期承诺处置

- 057 出口：后端控制面 + 启动器脚本 + 前端退出 UI + 文档同批；`play.ps1 -LauncherCheck` PASS；e2e 非启动器分支断言通过；
  全量 pytest/replay/web 基线不回归；`demo/console --check` 逐字不变；`check_docs` PASS；版本 0.3.0 一致。
  **实测（2026-11-11）**：pytest 2186、replay 68、web 56 files/347 tests、e2e 非启动器 26/125 + 启动器模式 26/126
  （确认退出→覆盖层→`/healthz` 不可达→服务进程 exit 0）、`LauncherCheck` PASS、`play -Check` PASS、四门全绿。
- 到期承诺处置：R2「一键网页入口」由本批升级为「双击启动器」；0.2.0 发布遗留的「玩家仍需终端」问题关闭。
- 后移：exe/便携包、系统托盘、自动更新（未排期；用户 2026-11-11 明确「暂不做安装包」，N5 058 只保留评估位）。

## 6. 后移与复审

- 若 WinForms 在目标机器上不稳定：降级控制台模式即可（脚本内置回退），不阻塞发布；
- 若 idle 自动退出造成误退投诉：`TEXTGAME_IDLE_EXIT_SECONDS` 可调/置 0（启动器仍可用按钮退出）；
- 后续若要「双击 exe / 安装包」：新增 ADR 评估 PyInstaller/便携 Python，本 ADR 不改写历史结论。

## 7. 变更纪律

- 控制面契约（端点/字段/状态码）变更必须改本 ADR + 前端契约测试 + e2e；不得绕过「默认 controller=None 时不可用」；
- 启动器脚本改动必须保留 `-Check`/`-Terminal` 旧行为，并跑 `-LauncherCheck`；
- 版本号/文档/CHANGELOG 必须同批更新（RELEASE.md 策略）。
