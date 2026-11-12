# 部署与分发评估（Deployment & Distribution）

> 归属：N4 技术/发布轨（051 docker-assessment；054–058 状态见 §5）；决策依据 [D-N3](../decisions/2026-11-11-post-m6-roadmap.md)；
> 状态：**评估已定案（2026-11-11，2026-11-12 补齐 054–058 评估/工具状态）**：默认 wheel / 仓库 + `play` 脚本分发，
> **Docker 镜像暂不提供**（给出最小 Dockerfile 草案与复评条件）；便携包/安装器已评估为**本轮不实现**（D-N5-1）；
> PyPI 正式发布暂缓（由维护者拍板）。

## 0. 结论先行

1. 目标用户 = **单机单玩家**（本地跑一个进程、浏览器访问 `127.0.0.1`，或纯终端）。数据只有一个 SQLite 文件
   + 一个可选的 LLM 凭据 JSON，**没有多副本/多租户/外部中间件**。
2. 因此分发优先级：**① Windows 普通玩家双击启动器 `启动游戏.bat`（启动器窗口 + 浏览器，游戏内/关窗/空闲
   自动退出）+ 仓库克隆 + `scripts/play` 一条命令（已含 `web/dist`，无需 Node）→ ② Python wheel
   （`pip install` 后同样带网页版）→ ③ Docker 镜像**。前两种已交付并通过发布冒烟（RELEASE.md §3；
   启动器冒烟见 §1.1 与 `play.ps1 -LauncherCheck`）。
3. **Docker 暂不提供**：收益（环境隔离/一条命令跑容器）对「本来就是一条命令」的单机场景增益有限；成本是
   维护镜像、数据卷、凭据文件挂载、端口绑定安全（默认只能绑回环）。等出现「服务器部署/多人共享」需求再评估。
4. **PyPI 暂缓**：wheel 元数据已就绪（MIT、内嵌 `textgame/web_dist`、`play.ps1|sh`），但包名/README 英文版/
   版本承诺需维护者拍板；在有明确用户渠道前不发。
5. **便携包/安装包评估结论（N5-058，D-N5-1）**：PyInstaller/Nuitka exe、embedded-Python 便携 zip、MSI/Inno
   安装器**本轮均不实现**——维持「仓库/zip + uv + 启动器 + wheel」分发；原因、成本与复评触发条件见
   [ADR D-N5-1](../decisions/2026-11-12-portable-package-eval.md)。**e2e 工具口径（N4-055，D-N4-2）**：
   继续零依赖 CDP（`scripts/e2e-web.mjs`），不引入 Playwright 长期 CI 浏览器矩阵。

## 1. 三种分发形态对比

| 形态 | 上手成本 | 前提 | 数据/凭据 | 适用场景 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| **A. 双击启动器（Windows）/ git clone + `play` 脚本** | 最低（双击，0 命令） | `uv`（自动装 Python 3.12）+ 网络（首次 sync） | `data/textgame.db`；`.env` 可选；日志 `data/logs/launcher-server.log` | 本机试玩、开发、演示 | ✅ 主推荐（启动器为玩家默认入口；`play.ps1 -Check`/`-LauncherCheck` 冒烟通过） |
| **B. Python wheel** | 中（需 Python 3.10+ 环境） | `pip install textgame-*.whl` | 同上；静态资源用包内 `textgame/web_dist` | 已装 Python 的玩家/离线机器 | ✅ 构建 + 干净 venv 冒烟通过 |
| **C. Docker 镜像** | 中（需 Docker） | 拉镜像 + `-v` 数据卷 + 端口 | 数据卷 + 可选挂载凭据文件 | 服务器/NAS/统一环境 | 🚫 本次不提供（评估见 §2） |

**共同注意（A/B/C 都适用）**：

- 服务默认只应绑定 `127.0.0.1`（`play` 脚本即如此）；**不要监听公网**——单机版没有账号体系，任何能访问端口的人
  都能操作存档；LLM Key 仅存本机。
- `.env` / `data/llm.credentials.json` **不进任何分发包**（gitignore + 打包排除）；`.env.example` 只给键名。
- 数据备份 = 复制 `data/textgame.db`（SQLite 单文件；停进程后复制最稳）。

### 1.1 玩家启动器（Windows 双击启动/退出，本批新增）

面向「像正常游戏一样启动/退出」的普通玩家，**不要求玩家敲命令**；前置仍只有 `uv`（不打包 exe、不自动安装任何东西）。
脚本入口：根目录 `启动游戏.bat` → `scripts/play.ps1 -Launcher` → `python -m textgame.launcher`。

**启动流程（`-Launcher`）**：

1. preflight：找 `uv`；缺失 → 弹中文说明框（安装网址 <https://docs.astral.sh/uv/getting-started/installation/> +
   「安装后重新双击」）并 `exit 2`，`启动游戏.bat` 失败时 `pause`；
2. 已有实例（`GET http://127.0.0.1:<port>/healthz` 200）→ 只打开浏览器，不重复启动第二个服务；
3. 首次无 `.venv` → 先 `uv sync --frozen --no-dev`（控制台可见进度）；
4. 以隐藏方式启动 launcher server（`--host 127.0.0.1 --port 8000 --idle-exit-seconds 300`，stdout/stderr 合并**追加**到
   `data/logs/launcher-server.log`，自动建目录）；等 `/healthz` ≤90s（失败显示日志最后 5 行 + 提示）；
5. `Start-Process http://127.0.0.1:<port>` 打开默认浏览器；
6. Windows 下优先 **WinForms 启动器小窗口**（标题「文字交互游戏」；状态文字「正在准备…（首次运行需几分钟）」→
   「已启动，正在打开浏览器…」→「游戏运行中：在游戏页面点「退出游戏」，或关闭本窗口退出。」；按钮「打开游戏」「退出游戏」）；
   非 Windows / 无图形环境自动降级为**控制台模式**（同样打印清晰启动/退出提示，Ctrl+C 退出）。

**退出状态机（四条路径）**：

| 玩家操作 | 服务端/脚本行为 | 结果 |
| --- | --- | --- |
| 游戏页面点「退出游戏」 | 前端 `POST /api/v1/app/shutdown {"confirm":true}`（仅回环）→ `LauncherController` 优雅停机（`reason=shutdown`，约 0.5s） | 启动器检测到进程退出 → 关闭窗口，提示「游戏已退出，感谢游玩」 |
| 关闭启动器小窗口 | 启动器 `finally` 用 `taskkill /T /F` 结束 server 进程树 | 端口释放，不留后台进程 |
| 双击 `退出游戏.bat`（兜底） | `play.ps1 -Stop` 用 `netstat -ano` 精确匹配 `127.0.0.1:<port>` 的 LISTENING 行（协议/地址/状态列校验，不做进程名宽泛匹配）→ `taskkill /PID <pid> /T /F` → 等端口释放 | 打印「已退出游戏」；无实例时打印「游戏没有在运行」 |
| 只关掉浏览器（不退出游戏） | 前端停止心跳；空闲 monitor 线程在 `TEXTGAME_IDLE_EXIT_SECONDS`（默认 300=5 分钟；0=关闭）后优雅退出（`reason=idle`；**从未收到心跳不判空闲**） | 启动器提示「游戏已退出，感谢游玩」并释放端口 |

**控制面契约**（前端「退出游戏」按钮依赖，默认 `uvicorn textgame.main:app` 不启用）：

- `GET /api/v1/app/status` → `{launcher_mode, idle_exit_seconds, shutdown_supported, version}`（非启动器模式全 false/0）；
- `POST /api/v1/app/heartbeat` → `{ok, idle_exit_seconds}`（刷新空闲计时；无控制器 404）；
- `POST /api/v1/app/shutdown` body `{"confirm":true}` → `{ok, message}`（非回环 403、重复调用幂等 200、0.2s 后置 `should_exit`）；
- `GET /healthz` → 200（启动器就绪判定）。

**自动化验收**：`pwsh -File scripts/play.ps1 -LauncherCheck -Port 8801` / `./scripts/play.sh --launcher-check`
——headless、不弹窗、不开浏览器；临时 `TEXTGAME_DB_URL=sqlite:///…` + `--idle-exit-seconds 0` + 自动空闲端口；
断言 `/healthz`、status（`launcher_mode=true`、`shutdown_supported=true`）、heartbeat 200、shutdown 200、
进程 ≤15s 退出且端口释放，打印「Launcher 自检通过」退出码 0。默认网页自检 `play.ps1 -Check` 行为不变。

**边界**：启动器窗口仅 Windows（WinForms）；非 Windows/无 GUI 降级控制台模式；不做 exe 打包、不自动安装 uv；
仍只监听 `127.0.0.1`；`-Port` 对启动器/自检/退出都生效；`-Llm` 仍透传环境变量。

## 2. Docker 评估（为什么不默认提供）

| 维度 | 评估 |
| --- | --- |
| 体积 | 基础镜像（python:3.12-slim）+ 依赖 ≈ 300–500MB；而 wheel 方案复用宿主 Python、不额外占空间 |
| 数据持久化 | 必须挂卷保存 `data/`（SQLite + 凭据）；挂载 Windows 路径时 SQLite 锁语义/权限有坑（WAL 与网络盘不兼容） |
| 凭据 | 玩家在网页「设置」页保存的 key 落在容器内 `data/`，重建容器即丢，需卷挂载；`-e TEXTGAME_LLM_API_KEY` 会把密钥暴露在 `docker inspect`/进程环境 |
| 静态资源 | `web/dist` 已随仓库/wheel 分发，无需容器内构建 Node |
| 端口安全 | 容器默认 `-p 8000:8000` 会绑 0.0.0.0；必须显式 `-p 127.0.0.1:8000:8000`，对普通用户是易错点 |
| 维护成本 | 需维护 Dockerfile/`.dockerignore`/多架构构建/镜像发布渠道；本轮无 CI Docker job，无法自动验证 |
| 本机验证 | **本开发机未安装 Docker**，无法实跑镜像（不提交未验证的 Dockerfile） |

**结论**：本轮**不提交** Dockerfile/镜像；文档给出「将来若要提供」的最小草案与复评条件，避免半成品配置误导用户。

### 2.1 若将来提供镜像（草案，未验证）

> 触发条件：出现「不装 uv/Python 也能跑」「服务器/NAS 长期运行」「多人共享同一实例」的真实需求，且 CI 有能力
> 跑 `docker build` + `/healthz` 冒烟。届时新增 ADR 并补 CI job，再提交以下草案（**当前仅存于本文档**）。

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY web/dist ./web/dist
COPY hatch_build.py ./
RUN pip install --no-cache-dir .
ENV TEXTGAME_DB_URL=sqlite:////data/textgame.db \
    TEXTGAME_LLM_CREDENTIALS_FILE=/data/llm.credentials.json \
    TEXTGAME_LLM_ENABLED=false
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
CMD ["python", "-m", "uvicorn", "textgame.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
# 构建与运行（将来）
docker build -t textgame:0.3.0 .
docker run --rm -p 127.0.0.1:8000:8000 -v textgame-data:/data textgame:0.3.0
# 首次打开 http://127.0.0.1:8000 ，在「设置」页填 DeepSeek API Key（不通过 -e 传密钥）
```

**复评时要补的验证**：镜像构建成功；`/healthz`、`/`（SPA）、`/api/v1/games` 冒烟；`data/` 卷持久化（重启后存档仍在）；
凭据文件权限；仅回环端口；`license/SBOM` 复检；镜像内不含 `.env`/`.git`/测试。

## 3. 发布产物与冒烟（已落地，见 RELEASE.md）

- `uv build` 产出 `dist/textgame-0.3.0-py3-none-any.whl` + sdist（当前版本；下一版 `0.4.0` 待维护者拍板）；wheel 内嵌 `textgame/web_dist`（`hatch_build.py`）；
- 干净 venv 安装 → `python -m textgame.console --check`（退出码 0）→ `python -m textgame.demo --check`（7 屏逐字）；
- 仓库 clone → `scripts/play.ps1 -Check`（`/healthz=200`、`/=200`）；
- 仓库 clone → `scripts/play.ps1 -LauncherCheck`（headless 启动器冒烟：status/heartbeat/shutdown + 进程退出 + 端口释放）。

## 4. 安全与合规注记

- 不监听公网；容器/服务器场景必须在前面加认证代理（属于未来服务器化需求，不在本轮范围）。
- 数据卷里包含存档与（若玩家在网页保存）API Key——按敏感数据对待，不入库、不进镜像层、不随日志导出。
- 分发不含用户内容；用户导入的场景 JSON/世界书/人物卡属于玩家数据，只存本机 `data/` 或数据库。
- 存档导出/导入（N3-048）是**玩家数据交换**：导出包只含事件/当前头/校验和，不含 API Key/凭据/日志；
  `checksum` 是完整性校验、**不是数字签名**，分享给他人前请自行评估（详见
  [save-export-import](save-export-import.md)）。

## 5. 与轨道/切片的对应

| 事项 | 轨道切片 | 状态 |
| --- | --- | --- |
| 版本号/CHANGELOG/README 发布段 | N4 049 | ✅ 2026-11-11（0.2.0）；0.3.0/0.4.0 待维护者发布拍板 |
| 英文 README 补全 | N4 050 | ✅ |
| Docker 评估（本文档） | N4 051 | ✅（结论：暂不提供 + 草案） |
| 构建产物冒烟 | N4 052 | ✅（RELEASE §3） |
| 发布 checklist / tag 流程 | N4 053 | ✅（RELEASE.md） |
| Windows 玩家启动器（双击启动/退出、空闲自动退出） | N5 057（发布体验轨；ADR [D-LA-1..N6](../decisions/2026-11-11-player-launcher-and-exit.md)） | ✅ 2026-11-11（`-LauncherCheck` 通过；窗口路径人工冒烟见 RELEASE §2.1 L2） |
| m3 叙事受控重基线工具/流程 | N4 054（ADR [D-N4-1](../decisions/2026-11-12-m3-narrative-rebaseline.md)） | ✅ 2026-11-12（`scripts/rebaseline_golden.py` dry-run 0 diff；本轮不改任何叙事/golden） |
| Playwright/a11y 工具口径复评 | N4 055（ADR [D-N4-2](../decisions/2026-11-12-playwright-a11y-eval.md)） | ✅ 已定案：继续零依赖 CDP，不引入 Playwright 长期 CI 矩阵 |
| 长会话性能基线 | N4 056（[long-session-perf](long-session-perf.md)） | ✅ 2026-11-12（120 回合/601 事件 + 宽松阈值回归） |
| 便携包/安装器评估 | N5 058（ADR [D-N5-1](../decisions/2026-11-12-portable-package-eval.md)） | ✅ 已评估：**本轮不实现**（维持现有 A/B 分发） |
| 场景分享导出 / 存档导出导入 / 终端批量导入导出 | N1 032 / N3 045·048（设计：[user-scenario-modules](user-scenario-modules.md)、[scenario-pack-batch](scenario-pack-batch.md)、[save-export-import](save-export-import.md)） | ✅ 后端已交付；网页多文件 UI 待前端批次 |
| 镜像实现 + CI Docker job | 未排期（057–060 预留 / 用户拍板） | ⏳ |
| PyPI 正式发布 | 未排期（用户拍板） | ⏳ |
