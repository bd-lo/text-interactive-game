# 贡献指南（CONTRIBUTING）

感谢你愿意参与《文字交互游戏》。本项目为 MIT 许可的开源项目，开发文档 v0.3 是权威设计源（`docs/开发文档.md`，按章拆分版在 `docs/开发文档/`）。

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
uv run python -m textgame.demo --check      # 固定 seed 七屏离线自检（exit 0）
uv run python -m textgame.console --check   # 终端入口四回合自检（exit 0）
uv run pytest -q tests/integration/test_console_walkthrough.py
```

## 3. 四门质量门（与 CI 同命令，PR 必须全绿）

```bash
uv run ruff format --check . && uv run ruff check .
uv run mypy src/textgame            # strict，源码保持 3.10 语法
uv run lint-imports                 # 分层依赖契约（domain 零框架依赖）
uv run pytest -q                    # 全量（当前基线 1179 passed）
uv run pytest tests/replay -q       # 回放（当前基线 61 passed）
```

- 测试默认禁网（`tests/conftest.py` 的 socket guard 只放行本机回环）；真实 LLM 一律 FakeLLM/cassette，**PR 不得引入真实网络调用或密钥**。
- 一次性安装提交前钩子：`uv run pre-commit install --install-hooks`（含 secret-scan）；也可用 `uv run pre-commit run --all-files` 手动跑。

## 4. 硬性纪律（PR 会被拒绝的情况）

- **golden 回放**：禁止 `--snapshot-update` 或改断言来掩盖回归；`tests/fixtures/golden/` 的 `state_hash`/事件/种子逐值固定。确需改叙事文本或事件语义时，先提 ADR（Lead 授权）+ 重钉 golden + 进度文档记录，并在 PR 说明里贴证据。
- **唯一写入者/事件溯源/`actor_scope`/工具白名单**：不得绕过 `GameEngine`、`EventLog`/CAS、`Resolver`，不放宽权限矩阵。
- **密钥与数据**：真实密钥只写 `.env`（不入库），`.env*`、`*.db`、`data/`、`artifacts/` 不得提交；`service_role`/API Key/玩家私有数据不得进测试或文档。
- **依赖与许可证**：不新增依赖（先在 Issue 讨论并通过第 19 章「依赖方向 + 许可证 + 3.10 兼容」三道门）；GPL/AGPL 只能参考设计，不得拷贝代码；`uv.lock`/`pnpm-lock.yaml` 变更单独提交。
- **契约变更**：先查 `docs/decisions/`（ADR 是决策单一权威源）。推翻既有决策 = 新增 ADR（`supersedes: D-x`）→ 回写对应章节 → 重跑全量 + 回放 → 更新 `docs/PROGRESS.md` §5。

## 5. 提交与文档制度

- trunk-based 小步提交；建议前缀 `slice: NNN <topic>` / `docs:` / `fix:`；失败即回滚到上一绿点，不连续堆叠未验证改动。
- 每个开发会话：在 `docs/progress/` 新建 `NNN-YYYY-MM-DD-<主题>.md`（第 22 章模板，≤60 行）并同步 `docs/PROGRESS.md`（完成度只按「已通过测试的任务数/总数」计算）。
- 用户可见行为/文档变更请同步更新 `CHANGELOG.md`；新增外部使用方式请补 README「5 分钟上手」并跑通干净环境验证。

## 6. 交流

- 用 GitHub Issue 报告 bug / 提需求（附复现命令、期望/实际、`uv run python -VV` 与系统信息）；安全敏感问题不要公开贴密钥/私有数据，先按仓库安全流程私下报告。
