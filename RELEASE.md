# 发布说明与 checklist（RELEASE）

> 当前版本：**`0.2.0`（2026-11-11，首个可发布快照）** ｜ 版本号唯一来源：`pyproject.toml` 与
> `src/textgame/__init__.py::__version__`（两处必须一致）｜ 变更记录：[CHANGELOG.md](CHANGELOG.md)
> 分发形态与 Docker 评估：[docs/design/deployment-and-distribution.md](docs/design/deployment-and-distribution.md)

## 1. 版本号策略

- 项目处于 `0.x`：`0.2.0` = 第一个「可发布快照」（M1–M6 全部里程碑 + 用户内容批次 + WB2）。
- 递增规则（轻量语义化）：
  - **次版本**（`0.2.0 → 0.3.0`）：玩家可见能力成批交付（例如 N1 场景编辑器/DSL v2 完成）；
  - **修订号**（`0.2.0 → 0.2.1`）：仅缺陷修复 / 文档修正 / 依赖安全更新，无新能力；
  - 破坏性契约变更（事件结构、API 破坏性删除）在 0.x 内也要在 CHANGELOG 明确标注「BREAKING」。
- 版本记录三处必须同批更新：`pyproject.toml`、`src/textgame/__init__.py`、`CHANGELOG.md` 顶部。
- `0.2.0` 之后是否发 PyPI / 打 tag / 建 GitHub Release 由维护者拍板（D-N3）；本文件给流程，不自动执行。

## 2. 发布前 checklist（首发 `0.2.0` 已全部通过）

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

> 说明：e2e 与 `play -Check` 属本地门禁（CI 无浏览器组合，D-M6-11）；CI 覆盖 quality（3.10/3.12 矩阵 +
> 覆盖率门 + 文档门）、deps、web-build（typecheck/build/vitest/e2e）。

## 3. 构建与冒烟（可复制）

```powershell
# 1) 构建 wheel / sdist（hatch 会把 web/dist 内嵌为 textgame/web_dist）
uv build
# 2) 干净环境安装冒烟（不污染开发 .venv）
python -m venv .work/.release-venv
.work\.release-venv\Scripts\python.exe -m pip install --quiet (Get-ChildItem dist\textgame-0.2.0*.whl | Select-Object -First 1).FullName
.work\.release-venv\Scripts\python.exe -m textgame.console --check   # 退出码 0
.work\.release-venv\Scripts\python.exe -m textgame.demo --check      # 7 屏逐字
# 3) 网页版冒烟（仓库根，使用仓库内 web/dist）
.\scripts\play.ps1 -Check   # /healthz=200 且 /=200，退出码 0
```

```bash
# bash 等价
uv build
python -m venv .work/.release-venv
.work/.release-venv/bin/python -m pip install --quiet dist/textgame-0.2.0*.whl
.work/.release-venv/bin/python -m textgame.console --check
./scripts/play.sh --check
```

## 4. 打 tag 与 Release notes 流程（由维护者在发布时执行）

```bash
# 0) 确认工作区干净、HEAD = 已验收提交、CHANGELOG 顶部版本与 pyproject 一致
git status --short            # 应为空
git diff --stat origin/main   # 确认待发布内容（本仓库历史上未 push 的提交需维护者先拍板）

# 1) 打注记 tag（tag 名 = v + 版本号）
git tag -a v0.2.0 -m "textgame 0.2.0（首个可发布快照）"

# 2) 推送 tag 与 main（仅在维护者明确要求时；本会话默认不 push）
git push origin main
git push origin v0.2.0

# 3) 建 GitHub Release（可用 gh，也可网页操作）；notes 从 CHANGELOG 顶层段落复制
gh release create v0.2.0 --title "textgame 0.2.0" --notes-file CHANGELOG.md
```

- Release notes 至少包含：版本号 + 日期；「新玩什么」（场景 JSON/世界书/设置页）；「怎么开始」（一条命令）；
  「已知限制」（见 README 限制段与 `docs/design/world-book-format-compat.md` §5）；测试数字。
- **CI 触发**：push main/tag 会触发 `.github/workflows/ci.yml`；发布前必须等待全部 job 绿。
- **回滚**：撤销 Release（`gh release delete v0.2.0`）→ 删除本地/远端 tag（`git tag -d`、`git push --delete`）
  → 修正后重新打 tag；已发布包不回滚 wheel，只发修订版。

## 5. 不随本次发布的事

| 项 | 结论 | 依据 |
| --- | --- | --- |
| PyPI 正式发布 | **暂缓**（需维护者拍板；当前分发 = 仓库 + wheel） | D-N3 |
| Docker 镜像 | **暂不提供**（评估见部署文档；单机 SQLite + 静态 SPA 用 zip/wheel 更简单） | D-N3 / deployment 文档 |
| 账号 / 云存档 / 场景市场 | 不做 | D-N7 |
| Playwright 长期 CI 矩阵 | 评估中（N4 055），当前仍用零依赖 CDP e2e | D-N7 |

## 6. 发布后跟进（N4 剩余切片）

- 054 m3 叙事受控重基线（需 ADR D-N4-1）；
- 055 Playwright/a11y 评估（D-M6-11 复评）；
- 056 长会话性能基线（100+ 回合 / 回放 500 事件）；
- 下一批能力按第 17 章 §17.9 轨道推进（N1 内容轨 029 起）。
