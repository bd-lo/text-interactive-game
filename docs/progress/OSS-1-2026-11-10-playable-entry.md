# OSS-1 · 2026-11-10 · 入口轨（不占 M1–M6 编号） · playable-entry

> 切片目标一句话：交付「一条命令开玩」的 2G 终端入口（`python -m textgame.console`）+ 固定 seed 七回合 demo（`--check` 离线烟测）+ quickstart 脚本 + 示例文档 + README「5 分钟上手」，配套玩家安全子集（只读 `GET /games/{id}/view` 状态 + 意图背包关键词）｜ 验收：`uv run python -m textgame.demo --check` exit 0、`uv run python -m textgame.console --check` exit 0、`uv run pytest -q tests/integration/test_console_walkthrough.py` 5 passed ｜ 决策：D-TEAM-3/5（`docs/decisions/2026-11-10-0450-team-direction.md`）｜ 不占 001–028 编号（见 `docs/progress/README.md` 例外条款）

## 1. 目标与验收命令

- 目标（团队裁决 B″ 的可玩/采用入口轨）：① 玩家不写 JSON、不管版本号、不填幂等键即可连续游玩（第 2 章 2G 文字风格：等宽终端/键盘优先/每屏 80–150 字/无图片音频）；② 默认离线、无需 API Key、无需 Node/pnpm/curl；③ 状态可见（HP/位置/行囊）但不写入叙事正文（不破 golden `text_len`）；④ 示例存档=脚本+预期文本（不提交 `.db`）。
- 验收命令与真实输出：
  - `uv run python -m textgame.demo --check` → **exit 0**；7/7 回合 ok，屏字数 **114/83/81/85/82/84/81**（与 M2 golden 一致），两次运行逐字一致，输出 0 个 JSON/game_id/version/Idempotency-Key 字样。
  - `uv run python -m textgame.console --check` → **exit 0**；4/4 回合（含失败样本「使用火把」），失败仅显示服务端文本 +「（这一回合没有生效）」，状态行 `HP 20/20 ｜ 位置 ｜ 行囊`。
  - `uv run pytest -q tests/integration/test_console_walkthrough.py` → **5 passed**（长期进 CI 烟测）。
  - 全量：`uv run pytest -q` → **1177 passed**；`uv run pytest tests/replay -q` → **61 passed**（逐值不变）；四门全绿；零新增依赖（`git diff --stat pyproject.toml uv.lock` 空）。
  - 覆盖率：**88%**（020 + 入口代码新增 500+ stmts 后仍 >80%）。
- 不做什么：不做浏览器 UI（M6 025b/027-lite 待依赖安装窗口与后端前置）；不改叙事模板/失败文案/golden（D-TEAM-5：需 ADR + 重钉 golden）；不提交示例 `.db`；不使用真实 LLM/网络。

## 2. 改动与提交

- 新增 `src/textgame/console.py`（交互循环 + `--check`；`GameSession` 内部维护版本链与幂等键；进程内 `TestClient` 装配，不占端口不联网；`:help/:look/:inv/:status/:quit`；视图缺失时降级提示不伪造数据）。
- 新增 `src/textgame/demo.py`（`--check` 两遍运行确定性、7 屏打印、`--markdown --out` 生成示例）；新增 `scripts/quickstart.ps1` / `quickstart.sh`（唯一前置 uv）；新增 `examples/m2-demo.md`（由 `--markdown` 实跑生成，seed=42 七屏 + 复现命令）。
- 新增 `tests/integration/test_console_walkthrough.py`（5 例：demo 7 屏/确定性/字数、console 4 回合/失败提示、视图缺失降级）。
- 玩家安全子集（D-TEAM-5）：`services/query_service.py` 增 `get_player_view`（hp/max_hp/inventory/room，只读零事件）；`api/routes/games.py` + `api/schemas.py` 增 `GET /api/v1/games/{game_id}/view`；`orchestration/intent.py` 增背包/行囊/物品栏/身上/带了/装备 → `get_inventory`（优先于 look，修「查看背包」被抢占）；对应测试 `tests/integration/test_game_view.py`、`tests/unit/test_intent_inventory.py`。
- README 新增「5 分钟上手（开源使用者）」（git clone / uv / PowerShell+bash / 无需 Key/Node/curl）。
- 提交：`feat: OSS-1 可玩终端入口 + 只读对局视图/背包意图（M6 首件）`（与 020 同轮次按文件分组提交）。
- 契约变更：D-M6-4（view 端点，M6 后端前置首件）、D-TEAM-5（安全子集；叙事文本改动后移）。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| demo 自检 | 7/7 ok；屏字数 114/83/81/85/82/84/81；两次一致；无协议字段；exit 0 |
| console 自检 | 4/4；失败样本有「这一回合没有生效」提示且不编造原因；状态行来自 `/view`；exit 0 |
| 交互试玩（玩家代表，管道 8 回合） | 一条命令进入；环顾 → 查看背包（93 字含火把）→ 使用火把（失败）→ 拿起火把 → 查看背包 → `:inv` → 北上 → `:quit`；全程无 JSON/版本/幂等键 |
| `/view` 只读 | hp=20/max_hp=20、inventory 与回合文本一致；连读 3 次 version/snapshot_version 不变；未知存档 404 统一信封 |
| 背包可达 | `查看背包`/`行囊里有什么`/`我身上带了什么` 均 → `get_inventory`（93 字、含物品名、非房间描述） |
| golden | replay 61 passed 逐值不变；`git status tests/fixtures/golden` 空；未 `--snapshot-update` |
| 全量/四门 | 1177 passed；ruff/format/mypy(84)/lint-imports 全绿；零新增依赖 |
| 玩家独立验收 | **PASS**（`.work/team-2026-11-10/player-acceptance.md`，13 项强制项全通过；判据「不写 JSON/不管版本/不填幂等键 + 80–150 字 + 可见状态 + 失败可理解且不伪造」全部成立） |

## 4. 下一步与风险

- 下一步（D-TEAM-3）：M6 后端前置（replay → 卡库 8 端点 + `card_service` + Alembic 0004 → 静态托管/SPA fallback → `web-build` CI）→ 前端 025 骨架 → 027-lite；console 保留为离线/烟测入口与 M6 对照基线。
- 风险/遗留（玩家验收 §4，均不改变本轮 PASS）：① 叙事约 10 屏固定模板且存在物品错配（「使用火把」成功叙事提到"药水草根味"），需叙事 ADR/LLM 叙事接线；② 失败无具体原因（改文案会破 golden，需 ADR + 重钉流程）；③ 内容量 2 房间/4 物品/1 NPC/无任务成长，建议后续「古物线索 + 可结束状态」小切片；④ 真实终端键盘/中文 IME 交互未由真人在本机验证（本次为管道输入）。
