# 贡献指南（CONTRIBUTING）

感谢你愿意参与《文字交互游戏》。本项目为 MIT 许可的开源项目，开发文档 v0.3 是权威设计源（`docs/开发文档.md`，按章拆分版在 `docs/开发文档/`）；H4 批次的设计见 `docs/design/`（视觉参考 / 世界书格式兼容 / 跑团模组）。

## 1. 环境（唯一前置 uv）

- 安装 [uv](https://github.com/astral-sh/uv)（Python 3.12 由 uv 自动下载管理；源码保持 3.10 语法兼容）。
- 首次安装需联网（下载 CPython 3.12 与锁定依赖）：

```bash
uv sync --frozen                 # 全部依赖（开发/测试/四门）
uv sync --frozen --no-dev        # 仅运行时依赖（只想试玩时更快，quickstart 用这个）
```

- **不要**用全局 Python/Anaconda 装依赖，所有命令在仓库根目录执行；`.venv/`、`data/`、`artifacts/`、`.env` 均已在 `.gitignore` 中。

## 2. 先跑起来（提交前自测）

```bash
.\scripts\play.ps1 -Check                  # 网页版自检：启动服务 → /healthz 与 / 均 200 → 退出（Windows）
./scripts/play.sh --check                  # 同上（bash）
.\scripts\play.ps1 -Terminal               # 终端版（等价旧 quickstart）
uv run python -m textgame.demo --check      # 固定 seed 七屏离线自检（exit 0）
uv run python -m textgame.console --check   # 终端入口四回合自检（exit 0）
uv run pytest -q tests/integration/test_console_walkthrough.py
```

真实浏览器 e2e（H4/L6；零依赖 CDP 驱动；需先起后端，或由 CI 临时起 uvicorn:8794）：

```bash
uv run --no-dev uvicorn textgame.main:app --port 8794 &   # 另开终端也可；CI 由 web-build job 起临时服务
node scripts/e2e-web.mjs --base-url http://127.0.0.1:8794 --out artifacts/e2e-web
# 覆盖首页/对局/结局/回放/卡库/生成卡/世界书（导入→绑定→触发→面板→删除）/375px/视觉开关；
# 失败时 artifacts/e2e-web/ 内有截图与服务器日志（CI 会作为 artifact 上传）
```

> 仓库已入库预构建的 `web/dist`（R2 分发方案 A）：玩家 fresh clone 无需 Node 即可玩网页版。
> 因此**改了 `web/src/**` 或 `web/index.html` 就必须** `pnpm --dir web build` 并提交产物
> `web/dist`（否则玩家拿到旧页面）；CI/集成阶段会校验产物与源码一致。

## 3. 四门质量门（与 CI 同命令，PR 必须全绿）

```bash
uv run ruff format --check . && uv run ruff check .
uv run mypy src/textgame            # strict，源码保持 3.10 语法
uv run lint-imports                 # 分层依赖契约（domain 零框架依赖）
uv run pytest -q                    # 全量（数字以当前 HEAD 实际输出为准，不写死基线）
uv run pytest tests/replay -q       # 回放（旧 golden 61 冻结 + provenance 7 = 68；禁止改断言/--snapshot-update）
uv run python scripts/check_docs.py # 文档一致性门（开发文档拼接/进度索引/下一编号）
```

- 测试默认禁网（`tests/conftest.py` 的 socket guard 只放行本机回环）；真实 LLM 一律 FakeLLM/cassette，**PR 不得引入真实网络调用或密钥**。
- 一次性安装提交前钩子：`uv run pre-commit install --install-hooks`（含 secret-scan）；也可用 `uv run pre-commit run --all-files` 手动跑。

## 4. 硬性纪律（PR 会被拒绝的情况）

- **golden 回放**：禁止 `--snapshot-update` 或改断言来掩盖回归；`tests/fixtures/golden/` 的 `state_hash`/事件/种子逐值固定。确需改叙事文本或事件语义时，先提 ADR（Lead 授权）+ 重钉 golden + 进度文档记录，并在 PR 说明里贴证据。
- **唯一写入者/事件溯源/`actor_scope`/工具白名单**：不得绕过 `GameEngine`、`EventLog`/CAS、`Resolver`，不放宽权限矩阵。
- **密钥与数据**：真实密钥只写 `.env`（不入库），`.env*`、`*.db`、`data/`、`artifacts/` 不得提交；`service_role`/API Key/玩家私有数据不得进测试或文档。
- **依赖与许可证**：不新增依赖（先在 Issue 讨论并通过第 19 章「依赖方向 + 许可证 + 3.10 兼容」三道门）；GPL/AGPL 只能参考设计，不得拷贝代码；`uv.lock`/`pnpm-lock.yaml` 变更单独提交。
- **分发产物**：`web/dist` 已入库（分发给玩家的预构建网页版）。改前端必须重建并提交该产物；禁止手改 `web/dist` 内容（由 `pnpm --dir web build` 生成）。
- **安全**：真实密钥只写 `.env`；安全问题不要公开 Issue，按 [SECURITY.md](SECURITY.md) 私下报告。
- **契约变更**：先查 `docs/decisions/`（ADR 是决策单一权威源）。推翻既有决策 = 新增 ADR（`supersedes: D-x`）→ 回写对应章节 → 重跑全量 + 回放 + demo/console + e2e → 更新 `docs/PROGRESS.md` §5。

## 5. 世界书改动纪律（H4）

- **只读注入**：激活条目必须是读时纯计算（`domain/world_book.py`），不得写 `GameState`/事件/快照；无世界书存档的叙事文本必须逐字不变（旧 golden 与 `demo --check` 7 屏不变）。
- **格式兼容层只借鉴 SillyTavern 的「数据结构 + 触发语义」**：SillyTavern 代码为 **AGPL-3.0，禁止复制**；Character Card V2 规范仓库无 LICENSE，只按公开字段语义独立实现。
- 字段支持状态以 [docs/design/world-book-format-compat.md](docs/design/world-book-format-compat.md) 的兼容矩阵为准；新增支持字段需同步该文档 + ADR。
- **导入内容按不可信数据处理**：≤256KB、JSON-only、前端文本插值（禁 `v-html`）、不执行任何导入内语义（`automationId`/`outlet` 等一律忽略）；后端只做结构化校验。
- 世界书语义差异（order 方向/概率确定性/scanDepth 缺省/regex key/预算口径）见兼容文档 §5；改变语义需单独 ADR。

## 6. 前端视觉/性能红线（H4）

- 特效层必须**懒加载**：首屏 gzip 增量 ≤100KB；Canvas 粒子不得进首屏 chunk（当前懒加载 chunk 1.49KB gzip，首屏 51.7KB）。
- **无障碍降级**：`prefers-reduced-motion: reduce` 时粒子停播/打字机瞬显/过渡接近 0；正文对比度不降；375px 不溢出；信息架构与可用性不变。
- **可关闭**：三档视觉设置（全特效/低功耗/关闭）持久化，关闭即时生效；不得因特效改变可读性。
- **禁** `v-html`/`innerHTML`/`eval`/`EventSource`/`setInterval`（`web/tests/unit/security-scan.test.ts` 守护）；粒子/动效用 rAF + CSS。
- 新增前端依赖必须为 MIT/Apache-2.0/BSD/ISC/CC0，且过 `pnpm-lock` + 许可证门禁 + 包体/懒加载证据；**当前前端新增运行时依赖为 0**，引入需在 ADR/视觉参考文档登记来源与署名。

## 7. 提交与文档制度

- trunk-based 小步提交；建议前缀 `slice: NNN <topic>` / `docs:` / `fix:`；失败即回滚到上一绿点，不连续堆叠未验证改动。
- 每个开发会话：在 `docs/progress/` 新建 `NNN-YYYY-MM-DD-<主题>.md`（第 22 章模板，≤60 行）并同步 `docs/PROGRESS.md`（完成度只按「已通过测试的任务数/总数」计算）。
- 用户可见行为/文档变更请同步更新 `CHANGELOG.md`；新增外部使用方式请补 README 并跑通干净环境验证。
- 世界书/视觉相关设计变更请同步 `docs/design/`（`frontend-visual-refs.md` / `world-book-format-compat.md` / `trpg-world-module.md`）。

## 8. 交流

- 用 GitHub Issue 报告 bug / 提需求（附复现命令、期望/实际、`uv run python -VV` 与系统信息）；安全敏感问题不要公开贴密钥/私有数据，先按仓库安全流程私下报告。
