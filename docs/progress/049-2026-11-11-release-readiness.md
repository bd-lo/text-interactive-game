# 049 · 2026-11-11 · N4 · 发布就绪（版本记录 / 英文 README / Docker 评估 / 构建冒烟 / 发布 checklist）

> 切片目标：把项目从「可玩」推进到「可发布快照」——版本号与变更记录、英文 README 补全、Docker 评估落文档、
> `uv build` 产物冒烟、发布 checklist 与 tag 流程。**本批合并交付 N4 049–053**（编号 050–053 留空不后移，
> 符合第 17 章 §17.9.1「实际篇数少于预留时编号留空不后移」）。
> 编号区间：N4 049–056 ｜ 决策：[D-N1..N7](../decisions/2026-11-11-post-m6-roadmap.md) ｜ 计划表：第 17 章 §17.9

## 1. 目标与验收命令

- 目标：产出**首个可发布快照 `0.2.0`** 的完整发布材料与可复现冒烟；不改变运行时行为（除版本号）。
- 验收命令（先定后写）：
  - `uv run pytest -q` → 2156 passed（版本号变更零回归）
  - `uv run pytest tests/replay -q` → 68 passed
  - `uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run lint-imports` → 全绿
  - `uv run python scripts/check_docs.py` → PASS
  - `uv build` → `dist/textgame-0.2.0-py3-none-any.whl` + sdist；干净 venv 安装后
    `python -m textgame.console --check` exit 0、`python -m textgame.demo --check` 7/7 屏
  - `pwsh -File scripts/play.ps1 -Check` → `/healthz=200`、`/=200`
  - `pnpm --dir web test` → 53 files/329 tests；`typecheck`/`build` 通过
- 不做什么：不发 PyPI、不打远端 tag、不 push（由用户/维护者拍板）；不提供 Docker 镜像（评估见文档）。

## 2. 交付内容

### 2.1 版本记录与发布段（049）

- `pyproject.toml` `version = "0.2.0"` + `src/textgame/__init__.py::__version__ = "0.2.0"`（唯一两处版本源；
  FastAPI OpenAPI `version` 自动跟随）。
- `CHANGELOG.md`：新增 `## [0.2.0] - 2026-11-11（首个可发布快照）` 段（WB2 世界书详情可见性、M6 后计划表、
  发布就绪四件套），`0.1.0` 降为「开发期历史记录」；顶部说明指向 `RELEASE.md` 与部署评估。
- `README.md`：新增「版本与发布」段；状态块更新为 0.2.0 + WB2 + M6 后计划表；修正过期的世界书限制描述
  （UC 起已支持正则键/定时效果/包含组）、Alembic 0001–0005、目录结构（`/scenarios`、`/settings`、`scenarios` 表、
  `RELEASE.md`）、文档索引（WB2 进度文档、M6 后计划表 ADR、部署评估）。

### 2.2 英文 README 补全（050）

`README.en.md` 从 64 行摘要扩到与中文 README 对齐的能力型说明（不逐字翻译）：版本/状态、快速开始、
「本项目是什么」（确定性核心/安全构造/分发）、场景 JSON、世界书（含 UC 新增语义与仍不支持项）、
DeepSeek API Key 三来源与安全、发布与分发、开发/质量门/e2e、技术栈、已知限制、许可证。

### 2.3 Docker 评估与分发（051）

新增 `docs/design/deployment-and-distribution.md`：

- 结论：默认分发 = 仓库克隆 + `play` 脚本（已含 `web/dist`）→ wheel（内嵌网页版）→ Docker（**暂不提供**）；
  PyPI 暂缓（维护者拍板）。
- Docker 不提供的理由表（体积/数据卷/SQLite 锁/凭据泄漏面/端口绑定/无 CI Docker job/本机未装 Docker 无法实跑）；
- 将来提供镜像时的最小 Dockerfile 草案（**仅存文档、未验证、不提交仓库**）、运行命令与复评条件；
- 三种分发形态对比 + 数据/凭据/端口安全注意。

### 2.4 构建产物冒烟（052）

- `uv build` → wheel `textgame-0.2.0-py3-none-any.whl`（含 `textgame/web_dist/index.html`，`importlib.resources` 验证）
  + sdist；干净 `.work/.release-venv` 安装后运行终端自检（`console --check` 4/4、`demo --check` 7/7 逐字）；
- 仓库 `play.ps1 -Check` → `/healthz=200`、`/=200`（失败退出码 2 已在脚本内）。

### 2.5 发布 checklist 与 tag 流程（053）

新增根目录 `RELEASE.md`：版本号策略（0.x 轻量语义化、三处同批更新）、11 项发布前 checklist（每项写命令/证据/
状态）、可复制的构建与冒烟命令（PowerShell + bash）、`git tag -a v0.2.0` → Release notes → CI 等待 → 回滚
流程、不随本次发布的事（PyPI/Docker/账号/Playwright）、发布后跟进（N4 054–056）。

## 3. 验证结果（Lead 实跑）

| 项 | 结果 |
| --- | --- |
| 全量 pytest | **2156 passed / 0 failed**（版本号变更零回归） |
| 回放 | `tests/replay` **68 passed** |
| 质量门 | ruff format/check、mypy strict（107 files）、lint-imports（2 kept）、check_docs **PASS** |
| web | `pnpm test` **53 files/329 tests**、`typecheck` 0、`build` 成功（本次未改前端，dist 不变） |
| 构建 | `dist/textgame-0.2.0-py3-none-any.whl` 536,961 B + sdist 930,441 B；wheel 内嵌 `textgame/web_dist` ✅ |
| wheel 冒烟 | 干净 venv 安装 → `textgame.__version__ == 0.2.0`；`console --check` exit 0（4/4 回合）；`demo --check` 7/7 屏逐字 |
| 网页冒烟 | `scripts/play.ps1 -Check` → `/healthz=200`、`/=200 text/html` |
| 数据库 | 无迁移改动（Alembic head 仍 **0005**） |

## 4. 改动文件

- 版本/发布：`pyproject.toml`、`src/textgame/__init__.py`、`CHANGELOG.md`、`README.md`、`README.en.md`、`RELEASE.md`（新增）、
  `docs/design/deployment-and-distribution.md`（新增）；
- 文档：本进度文档、`docs/PROGRESS.md`（§1 N4 状态、§3 索引、§7 证据）；
- 未改：前端（`web/dist` 无漂移）、任何运行时逻辑/事件/契约。

## 5. 已知限制/后续

1. 未真正打 tag / 推远端 / 发 Release（需维护者明确授权；RELEASE.md 给可复制流程）；
2. PyPI 正式发布暂缓（包名/英文文档/版本承诺需拍板，D-N3）；
3. Docker 镜像不提供（评估见部署文档；如出现服务器/多人需求再立 ADR + CI Docker job）；
4. N4 剩余 054 m3 叙事受控重基线、055 Playwright/a11y 评估、056 长会话性能基线待启动。
