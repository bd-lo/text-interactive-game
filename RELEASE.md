# 发布说明与 checklist（RELEASE）

> 当前版本：**`0.3.0`（2026-11-11，玩家启动器/退出体验；N5 057）** ｜ 最近发布：注记 tag **`v0.2.0`** ｜ 版本号唯一来源：
> `pyproject.toml` 与 `src/textgame/__init__.py::__version__`（两处必须一致）｜ 变更记录：[CHANGELOG.md](CHANGELOG.md)
> 分发形态与 Docker 评估：[docs/design/deployment-and-distribution.md](docs/design/deployment-and-distribution.md)

## 1. 版本号策略

- 项目处于 `0.x`：`0.2.0` = 第一个「可发布快照」；`0.3.0` = 玩家启动器/退出体验（N5 057）。
- 递增规则（轻量语义化）：
  - **次版本**（`0.2.0 → 0.3.0`）：玩家可见能力成批交付（启动器、场景编辑器等）；
  - **修订号**（`0.3.0 → 0.3.1`）：仅缺陷修复 / 文档修正 / 依赖安全更新，无新能力；
  - 破坏性契约变更（事件结构、API 破坏性删除）在 0.x 内也要在 CHANGELOG 明确标注「BREAKING」。
- 版本记录三处必须同批更新：`pyproject.toml`、`src/textgame/__init__.py`、`CHANGELOG.md` 顶部。
- 是否发 PyPI / 打 tag / 建 GitHub Release 由维护者拍板（D-N3）；本文件给流程，不自动执行。

## 2. 发布前 checklist（`0.2.0` 首发基线全部通过，2026-11-11）

| # | 项 | 命令/证据 | 状态 |
| --- | --- | --- | --- |
| 1 | 全量测试 | `uv run pytest -q` → **2156 passed**（记录数字） | ✅ 2026-11-11 |
| 2 | 回放确定性 | `uv run pytest tests/replay -q` → **68 passed**（旧 golden 零改动） | ✅ |
| 3 | 四道质量门 | `ruff format --check .` / `ruff check .` / `mypy src` / `lint-imports` 全绿 | ✅ |
| 4 | 文档一致性 | `uv run python scripts/check_docs.py` → **PASS**（`docs/开发文档.md` 与 23 章一致） | ✅ |
| 5 | 网页产物 | `pnpm --dir web test`（**53 files/329 tests**）、`typecheck`、`build`；tracked `web/dist` 与 fresh build 零漂移 | ✅ |
| 6 | 浏览器 e2e | `pwsh -File .work/r3-team/s1/run-e2e.ps1`（临时 uvicorn + Edge/Chrome CDP）→ **25/25 步、122/122 断言** | ✅ |
| 7 | 终端/演示确定性 | `uv run python -m textgame.demo --check`（7/7 屏逐字）、`uv run python -m textgame.console --check`（4 轮回合） | ✅ |
| 8 | 构建冒烟 | `uv build` → 干净 venv `pip install dist/textgame-0.2.0*.whl` → `python -m textgame.console --check`；网页版 `play.ps1 -Check` | ✅（命令见 §3） |
| 9 | 依赖/许可证/安全 | CI `deps` job（pip-licenses 门禁 / pip-audit / SBOM）全绿；[SECURITY.md](SECURITY.md) 核对 | ✅（CI）；报告路径见 §4 |
| 10 | 文档齐套 | README（中/英）发布段、CHANGELOG `0.2.0` 段、PROGRESS 当前状态、ADR/进度文档同批 | ✅ |
| 11 | 密钥/数据卫生 | `git ls-files` 无 `.env`/`data/*.db`/凭据文件；`check_secrets.py` pre-commit 通过 | ✅ |
| 12 | 远端推送与 tag | `git push origin main`（`1f6fc25..0e52814`）+ `git tag -a v0.2.0` + `git push origin v0.2.0`；远端 tag 指向 `0e52814`；CI run [43032265398](https://github.com/bd-lo/text-interactive-game/actions/runs/43032265398) **success** | ✅ 2026-11-11 |

> 说明：e2e 与 `play -Check` 属本地门禁（CI 无浏览器组合，D-M6-11）；CI 覆盖 quality（3.10/3.12 矩阵 +
> 覆盖率门 + 文档门）、deps、web-build（typecheck/build/vitest/e2e）。

### 2.1 本批新增门禁：玩家启动器（`0.3.0` 起纳入）

| # | 项 | 命令/证据 | 状态 |
| --- | --- | --- | --- |
| L1 | 启动器 headless 冒烟 | `pwsh -File scripts/play.ps1 -LauncherCheck -Port 8801` → **「Launcher 自检通过」**（临时 `TEXTGAME_DB_URL` + 空闲端口；断言 `GET /api/v1/app/status` 的 `launcher_mode`/`shutdown_supported`、`POST heartbeat`、`POST shutdown` 均 200，进程 ≤15s 退出且端口释放；不弹窗、不开浏览器） | ✅ 2026-11-11 实跑（端口 8801，可重复） |
| L2 | 启动器窗口人工冒烟 | 双击 `启动游戏.bat` → 启动器小窗口 + 浏览器；三条退出路径（游戏内「退出游戏」/关闭启动器窗口/`退出游戏.bat` 兜底）；仅关浏览器 → 5 分钟空闲自动退出 | 发布前人工过一遍（自动验收只覆盖无 GUI 控制面） |
| L3 | 默认网页自检未被破坏 | `.\scripts\play.ps1 -Check` → 「自检通过：/healthz=200，/=200」 | ✅ 2026-11-11 实跑 |

### 2.2 `0.3.0` 增量验证（2026-11-11 实跑，发布时复跑一次）

| 项 | 命令/证据 | 结果 |
| --- | --- | --- |
| 全量测试 | `uv run pytest -q` | **2186 passed**（+30：控制面/控制器/真实子进程 + 契约） |
| 回放 | `uv run pytest tests/replay -q` | **68 passed**（旧 golden 零改动） |
| 网页 | `pnpm --dir web test` / `typecheck` / `build` | **56 files/347 tests**、0 错、build OK、tracked dist 零漂移 |
| e2e（CI 口径，非启动器） | 临时 uvicorn + CDP | **26/26 步、125/125 断言**（新增 `app.exit` 非启动器提示分支） |
| e2e（启动器模式，本地证据） | `python -m textgame.launcher` + 同一份 e2e | **26/26 步、126/126 断言**（确认退出→覆盖层→`/healthz` 不可达→进程 exit 0） |
| 启动器 headless | `play.ps1 -LauncherCheck -Port <空闲>` | **Launcher 自检通过**（status/heartbeat/shutdown/进程退出/端口释放） |
| 终端/演示 | `demo --check` 7/7、`console --check` 4/4 | 逐字不变 |
| 四门 | ruff format/check、mypy、lint-imports、check_docs | 全绿 |

## 3. 构建与冒烟（可复制）

分发形态：Windows 普通玩家双击根目录 `启动游戏.bat`（启动器小窗口 + 浏览器；游戏内「退出游戏」/关窗/空闲自动退出，`退出游戏.bat` 兜底）；
开发者用 `play` 脚本或 wheel。启动器入口 `scripts/play.ps1 -Launcher`，底层是 `python -m textgame.launcher`（控制面在 `/api/v1/app/*`）。

```powershell
# 1) 构建 wheel / sdist（hatch 会把 web/dist 内嵌为 textgame/web_dist）
uv build
# 2) 干净环境安装冒烟（不污染开发 .venv）
python -m venv .work/.release-venv
.work\.release-venv\Scripts\python.exe -m pip install --quiet (Get-ChildItem dist\textgame-0.3.0*.whl | Select-Object -First 1).FullName
.work\.release-venv\Scripts\python.exe -m textgame.console --check   # 退出码 0
.work\.release-venv\Scripts\python.exe -m textgame.demo --check      # 7 屏逐字
# 3) 网页版冒烟（仓库根，使用仓库内 web/dist）
.\scripts\play.ps1 -Check   # /healthz=200 且 /=200，退出码 0
# 4) 玩家启动器冒烟（headless：无窗口、无浏览器、临时库 + 空闲端口）
.\scripts\play.ps1 -LauncherCheck -Port 8801   # → 「Launcher 自检通过」，退出码 0
```

```bash
# bash 等价
uv build
python -m venv .work/.release-venv
.work/.release-venv/bin/python -m pip install --quiet dist/textgame-0.3.0*.whl
.work/.release-venv/bin/python -m textgame.console --check
./scripts/play.sh --check
./scripts/play.sh --launcher-check
```

## 4. 打 tag 与 Release notes 流程（由维护者在发布时执行）

```bash
# 0) 确认工作区干净、HEAD = 已验收提交、CHANGELOG 顶部版本与 pyproject 一致
git status --short            # 应为空
git diff --stat origin/main   # 确认待发布内容（本仓库历史上未 push 的提交需维护者先拍板）

# 1) 打注记 tag（tag 名 = v + 版本号；示例：当前 0.3.0）
git tag -a v0.3.0 -m "textgame 0.3.0（玩家启动器与退出体验）"

# 2) 推送 main 与 tag（由维护者明确要求后执行；本仓库默认不自动推送）
git push origin main
git push origin v0.3.0

# 3) 建 GitHub Release（可用 gh，也可网页操作）；notes 从 CHANGELOG 顶层段落复制
gh release create v0.3.0 --title "textgame 0.3.0" --notes-file CHANGELOG.md
```

- Release notes 至少包含：版本号 + 日期；「新玩什么」（双击启动器/游戏内退出/空闲自动退出、场景 JSON/世界书/设置页）；
  「怎么开始」（双击 `启动游戏.bat`）；「已知限制」（见 README FAQ 与 `docs/design/world-book-format-compat.md` §5）；测试数字。
- **CI 触发**：当前 workflow 监听 `push: branches: [main]` 与 `pull_request`——**push main 会触发**全量 CI；
  **push tag 不单独触发**（tag 上没有独立 run，以 main push 的 run 为准）。发布前必须等待全部 job 绿。
- **回滚**：撤销 Release（`gh release delete v0.3.0`）→ 删除本地/远端 tag（`git tag -d`、`git push --delete`）
  → 修正后重新打 tag；已发布包不回滚 wheel，只发修订版。

**首发记录（2026-11-11，维护者授权执行；版本 0.2.0）**：

- 远端 main：`1f6fc25..0e52814` 已推送（9 个提交）；
- 注记 tag **`v0.2.0`**（tag object `6a8bb1d`，指向 commit `0e52814`）已推送远端；
- CI run [43032265398](https://github.com/bd-lo/text-interactive-game/actions/runs/43032265398)（sha `0e52814`）**success**；
- GitHub Release 与 PyPI **未做**（下一步待维护者决定；Release notes 可直接取 CHANGELOG `[0.2.0]` 段）。

## 5. 不随本次发布的事

| 项 | 结论 | 依据 |
| --- | --- | --- |
| PyPI 正式发布 | **暂缓**（需维护者拍板；当前分发 = 仓库 + wheel） | D-N3 |
| Docker 镜像 | **暂不提供**（评估见部署文档；单机 SQLite + 静态 SPA 用 zip/wheel 更简单） | D-N3 / deployment 文档 |
| 账号 / 云存档 / 场景市场 | 不做 | D-N7 |
| Playwright 长期 CI 矩阵 | 评估中（N4 055），当前仍用零依赖 CDP e2e | D-N7 |

## 6. 发布后跟进

- `0.3.0` 待维护者决定是否打 tag `v0.3.0` / 建 GitHub Release（PyPI 仍暂缓）；
- N4 剩余：054 m3 叙事受控重基线（需 ADR D-N4-1）、055 Playwright/a11y 评估（D-M6-11 复评）、056 长会话性能基线；
- N5 后续：058 便携包/安装包评估（用户 2026-11-11 表示暂不做，仅保留评估位）；GUI 启动器人工验收（L2）在发布前过一遍；
- 下一批能力按第 17 章 §17.9 轨道推进（N2 038–040 流式实现、N1 029 场景 DSL v2 设计）。
