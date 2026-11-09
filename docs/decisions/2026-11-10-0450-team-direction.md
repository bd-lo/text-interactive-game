# ADR · 2026-11-10 04:50 四角色团队方向决策（D-TEAM-1..7）

- 状态：已定案（只追加）｜决策人：lead｜参与/复核：四角色团队 Round 1 独立立场书 + Round 2 交叉质询（frontend-dev / backend-dev / player-rep / oss-user-rep，论文见 `.work/team-2026-11-10/`，不入库）｜日期：2026-11-10｜基线：`1f74de2`（=origin/main，工作树 clean；1129 passed / replay 61 / 四门绿 / 覆盖率 90%，Lead 04:5x 复跑）
- 关联：M5 019–024 + 出口附加项；M6 025–028 + **M6 后端前置（不新增编号）**；`docs/decisions/2026-11-10-m5-api-governance.md`（D-M5-1..9）；`docs/decisions/2026-11-10-m6-backend-contract.md`（D-M6-1..6）；`.work/team-2026-11-10/00-PROBLEMS.md`（问题清单，不入库）
- 编号说明：本文件为**跨里程碑方向决策**，条目用 `D-TEAM-*`（`docs/decisions/README.md` 已同步该类别）；里程碑内契约决策仍用 `D-M<里程碑>-*`。

## 1. 背景与授权

- 用户 2026-11-09 22:50 指示：六小时后（≈2026-11-10 04:50）读工作区开发文档、检查当时存在的问题、由「前端开发 / 后端开发 / 玩家 / 希望使用该开源项目的人」四角色组成的智能体团队**在不考虑工时的前提下**讨论并做出最优决策、写入文档、然后开发。
- 时点现状（Lead 与四角色各自实测/核验）：
  - 工程：M1–M4 已完成（4/4、5/5、5/5、4/4），M5 进行中 **1/6**（019-llmclient），M6 **0/4**；HEAD `1f74de2` clean；1129 passed、replay 61、四门全绿、覆盖率 90%。
  - 产品：**无任何游戏界面**（`web/` 不存在，M6 未开始；FastAPI 自带 `/api/docs` 只是 API 文档 UI）；真人从未以玩家身份玩过本项目；叙事为确定性模板（`TemplateNarrator`），真实 LLM 默认关闭（`TEXTGAME_LLM_ENABLED=false` → `DisabledLLMClient`）。
  - 玩家实测（player-rep，8765 端口、默认离线、5 存档约 40 回合）：`GET /` 404；`查看背包` 返回房间描述、`行囊里有什么`/`我的生命值` 均 `INVALID_INPUT`（`get_inventory` 在生产解析器无关键词可达）；同一动作叙事模板逐字重复（全游戏约 10 屏）；世界拒绝仅 16 字「这个动作没有生效」；M3 首回合文本断在句中（`_compose_text` 硬切）。
  - 开源采用：README 无 `git clone`/安装章节/5 分钟上手，首个上手章节为「本地开发环境（已就绪）」；无 `CONTRIBUTING/CHANGELOG/Dockerfile/examples`、无 git tag；M4 已交付但 README 无 M4 试玩段（oss-user-rep 实测）。
  - 关键隐藏范围（backend-dev/frontend-dev 独立核出）：第 23 章 M6 依赖的 11 个新端点中 10 个未实现（8 个 `character-cards` + `POST /games` 的 `card_id` 语义 + `GET /games/{id}/replay`）；`CharacterCard` 在 `src`/`tests` 零定义；`card_service` 未落盘；`POST /games` 实际 `{seed,scenario}`+`save_seed` 与第 23 章 `{card_id}` 已漂移；静态托管/SPA fallback 未实现；`web-build` CI 不存在。
- 用户约束：不考虑工时/人力成本；讨论须有真实分歧并收敛；决策写入入库文档；随后进行开发。

## 2. 决策

### D-TEAM-1 方向：定向双轨（B″），两轨共同出口 = 首个可玩版本

- **决策**：采用「M5 治理链（不裁不减）+ 可玩/采用入口轨（并行）」的定向双轨；两条轨**都不得以另一条为前置而停摆**，共同出口定义为「真人可玩 + 可公开演示的首个版本」。
- **理由**：① 四角色独立核出 M6 存在未立项的后端隐藏范围（10 个端点 + SPA），回避它会让 M6 出口不可验收（backend-dev/frontend-dev）；② 项目从未被真人玩过、默认离线且叙事模板化，P0-1/P0-2 必须尽早出现真实用户证据（player-rep/oss-user-rep）；③ 020/022 是 M5 治理链内不可跳的配置/成本/并发底座（D-M5-4、D-M4-15），与玩家价值不冲突，只是**节奏门控**问题，不需要二选一（四角色 Round 2 均收敛到 B″）。
- **被否**：A 单轨 M5 先行（玩家代表/开源代表/前端代表明确反对：M6 隐藏范围与可玩入口继续后置，M6 出口届时不可验收）；C′ 单轨玩家价值优先并整体后移 020–024（backend-dev 指出依赖倒置：021/022/024 是真实 LLM 接线与成本/可用性前提；且 M6 后端前置仍逃不掉）；D 开源可用性优先（四角色一致认为一键体验不能替代「可玩 + 真实叙事」的核心卖点，其入口部分并入 OSS-1）。
- **影响面**：见 D-TEAM-3/4/7；两轨写作用域不重叠（`src/textgame/llm/**` vs `src/textgame/api|services|orchestration` vs `src/textgame/{console,demo}.py|scripts|examples|README` vs `docs/**`）。

### D-TEAM-2 M5 治理链顺序与门控（020→022→021→024→023→出口接线）

- **决策**：M5 剩余切片顺序定为 **020-model-tier-cache → 022-cost-budget（含 D-M4-15 关闭、`llm_call_log` 表 + Alembic 0003）→ 021-schema-repair → 024-provider-fallback → 023-batch-sleep → M5 出口附加项（解析白名单派生 P2-4(a) → 叙事 → 角色 → 裁判，裁判需 Lead 授权改 Resolver）**；021/023/024 可后移但**不可删除**。M5 完成度在 6/6 + D-M5-9 到期承诺闭环前不得标 100%。
- **门控定位**（不是排期门，而是发布/接线门）：022+D-M4-15 → 浏览器 UI 自动重试、公网/多用户部署、默认开启真实 LLM 之前；021 → 真实角色/裁判 LLM 接线之前（修复语义 D-M5-2）；024 → 对外发布/默认开启真实 LLM 之前；023 → 多 NPC LLM 并发之前。
- **理由**：020 是 022/024 的配置底座（D-M5-4 的 JSON 分级/回退/价格在 020 落地）；022 是并发幂等与成本上限的跨轨前置（前端重试会放大 D-M4-15，第 23 章 §23.3 幂等键生命周期）；021 是结构化输出一次修复语义（D-M5-2）的前置；024 是可用性门；023 只在真实多 NPC LLM 并发时需要。
- **被否**：把 020–024 当「治理尾巴」整体后移（player-rep Round 1 主张，Round 2 修正为门控定位）；020→021→022 顺序（frontend-dev 指出 022 才是 UI 重试/成本前置，应与 021 交换）。

### D-TEAM-3 可玩/采用入口轨：本轮 OSS-1，下轮 M6 后端前置 + 前端 025/027-lite

- **决策**：可玩/采用轨按以下顺序推进：
  1. **本轮**：OSS-1「一条命令开玩」终端入口（`python -m textgame.console` / `python -m textgame.demo --check` / `scripts/quickstart.{ps1,sh}` / `examples/m2-demo.md` / README「5 分钟上手」/ 集成测试）；配套**玩家安全子集**（意图背包关键词、只读 `GET /api/v1/games/{id}/view`、console 状态行与失败横幅）。
  2. **下轮**：M6 后端前置（`view` 之外的 `replay` 端点 + 8 个 `character-cards` 端点 + `card_service` + 静态托管/SPA fallback，契约冻结见 D-TEAM-4）与前端 025 骨架 + 027-lite（仅用现有端点先做对局回路）。
  3. **之后**：026-card-import、027 完整（回放拖动）、028-llm-card-generate；发布形态（git tag/CHANGELOG/CONTRIBUTING/英文 README/PyPI）在 M6-v1 后评估；Docker 在 `web/dist` 与 `web-build` CI 就绪后评估（当前本机 docker 不可用，不发布无法验收的镜像）。
- **理由**：终端入口是 2G MUD 原生形态（第 2 章风格约束即等宽/深色终端/键盘优先/无图片），且是当前环境**唯一可离线自动验收**的人机可玩路径；同时满足玩家「能玩」与开源使用者「5 分钟旅程」两个诉求，且不占 M6 编号、不改变 M6 完成度。Web UI 仍是 M6 主线（D-TEAM-4），console 长期保留为 CI 烟测与无 UI 入口。
- **被否**：本轮直接做 `web/` 代码（无 pnpm 依赖安装窗口时 `pnpm build`/vitest/Playwright 不可执行，属不可验收产物；frontend-dev 提出并接受 F-025a 作为下轮第一件）；把 console 当一次性 demo（保留为正式入口 + 烟测）。

### D-TEAM-4 M6 后端契约冻结

- **决策**：M6 后端前置按 `docs/decisions/2026-11-10-m6-backend-contract.md`（D-M6-1..6）冻结：新增端点清单与 DTO、`POST /games` 加法兼容策略、replay/view 契约、静态托管与 SPA fallback、Q6=A / Q7=A 定案、分发策略与验收门。**「M6 后端前置」登记为 M6 出口附加项（不新增 025–028 编号）**；M6 出口验收必须包含它，不得以「前端 4/4」代替。
- **理由**：M6 按现表启动会在 026/027 撞上 0 端点（frontend-dev/backend-dev 独立核实）；先冻结契约再开前端，避免 mock 写死→后端落地→返工。本轮提前交付其中最小只读端点 `GET /games/{id}/view`（玩家状态诉求的合法出口，避免改叙事文本触 golden）。
- **被否**：把 M6 后端前置并入 025 前端切片（不可验收、职责混淆）；新增 029–0xx 编号（与第 17 章 M5 出口附加项先例不一致）。

### D-TEAM-5 玩家体验修复分级：safe-now vs 需重钉 golden

- **决策**：
  - **本轮安全子集（实现）**：① 意图解析新增背包/行囊/物品栏/身上/带了/装备等关键词 → `get_inventory`（优先于 `look`）；② 只读 `GET /api/v1/games/{id}/view`（HP/背包/房间，零事件）；③ console 层状态行与失败横幅（不写入叙事正文）；④ 缺省 `suggested_actions` 填充列为候选（若与本轮边界冲突则后移）。
  - **必须走 ADR + 重钉 golden（本轮不做）**：既有叙事模板改写、`_compose_text` 句末裁剪、16 字世界拒绝文案、把状态行写入叙事正文。理由：M2/M3/M4 golden 对每回合 `text_len`/`event_ids`/`rng_seeds`/`state_hash` 精确断言（M2 7 回合 114/83/81/85/82/84/81 字；M3 第 2 回合 80 字；M4 失败回合 `text_len=16`），任何改动都会破 61 例零回归；「等长改写」技术上可过但属规避语义保护，明确不接受（player-rep 提出，Lead 采纳）。改文案须 Lead 授权 + golden 变更登记 + 进度文档记录，禁止 `--snapshot-update`。
  - **正解路径**：叙事多样性由 M5 出口附加项的真实 LLM 叙事（cassette 回放、默认仍离线）解决，golden 只覆盖离线确定性路径。
- **理由**：玩家代表把「能玩」拆为「可玩入口 + 可见状态 + 可理解失败」；前两项本轮可安全交付，第三项必须尊重回放契约。

### D-TEAM-6 020 配置实现裁决（D-M5-4 落地细节）

- **决策**：① 把既有 `src/textgame/llm/config.py` 迁为包 `src/textgame/llm/config/`（`__init__.py` + `llm.default.json`），`from textgame.llm.config import LLMClientConfig` 导入路径不变（路径冲突以此解，替代「登记路径偏离」）；② **JSON 为模型名/分级/供应商回退/价格唯一权威**（`llm.default.json` + `TEXTGAME_LLM_CONFIG` 覆盖），`Settings` 只保留密钥/开关/配置路径（本机环境变量 `TEXTGAME_LLM_ENABLED`/`TEXTGAME_LLM_API_KEY`/`TEXTGAME_LLM_CONFIG`；历史 `TEXTGAME_LLM_PROVIDER/MODEL/BASE_URL` 保留兼容但不再是客户端模型来源，第 12/16 章回写）；③ 本轮不改既有叙事/失败文案（见 D-TEAM-5）。
- **理由**：backend-dev 提出并经四方确认；避免两处模型来源导致「配置改了但请求没变」的漂移。

### D-TEAM-7 本轮交付清单、验收与不做项

- **本轮交付（04:50 会话）**：
  | # | 交付 | 写入范围 | 验收 |
  | --- | --- | --- | --- |
  | 1 | M5-020-model-tier-cache | `src/textgame/llm/**`、`config/settings.py`、`.env.example`、`tests/unit/test_model_tier.py` | `uv run pytest tests/unit/test_model_tier.py -q` ≥12；全量 ≥1129+新增；replay 61；四门绿 |
  | 2 | OSS-1 终端可玩入口 | `src/textgame/{console,demo}.py`、`scripts/quickstart.*`、`examples/m2-demo.md`、`tests/integration/test_console_walkthrough.py`、README「5 分钟上手」 | `uv run python -m textgame.demo --check` exit 0；`console --check` exit 0；全量+replay 零回归 |
  | 3 | 玩家安全子集 | `services/query_service.py`、`api/routes/games.py`、`api/schemas.py`、`orchestration/intent.py` + 两个新测试 | `pytest tests/integration/test_game_view.py tests/unit/test_intent_inventory.py -q`；replay 61 逐值不变 |
  | 4 | 本 ADR + M6 契约 ADR + PROGRESS/章节回写 + 020 进度文档 | `docs/**` | 文档重建 verify/roundtrip PASS；数字与实测一致 |
  | 5 | 玩家独立验收 | `.work/team-2026-11-10/player-acceptance.md`（不入库） | PASS/CONDITIONAL/FAIL + 剩余缺口 |
- **本轮不做**：不打开真实 LLM 默认、不做 async/SSE、不打开自由制造开关（P2-4(b)/D-M4-3 未解）、不改 Resolver（裁判接线需 Lead 授权）、不做 Docker/英文 README/PyPI 发布、不做 026/028 与卡库后端、不改叙事文本/golden、不新增依赖（cachetools 已在锁内）。
- **复审触发（到期点）**：见 D-TEAM-2 门控 + D-TEAM-3 轨道顺序 + D-M6 契约 ADR。

## 3. 四角色立场与关键分歧（Round 1 → Round 2 收敛）

| 角色 | Round 1 主张 | Round 2 修正 | 被采纳/被否 |
| --- | --- | --- | --- |
| 玩家代表 | C′：先可玩（M6-025+027 对局回路 + 体验硬伤包），M5 后移 | B″：接受 console 为正式入口，但必须补只读 view+背包词；M5 不裁但按门控 | 采纳「可玩入口 + 安全子集 + 分级修复」；否「本轮 web UI」（不可离线验收）与「整体后移 020–024」 |
| 后端代表 | B′：M5 不裁（020→022→021→024→023）+ M6 后端前置立项；本轮 020+C-1/C-2 | 接受 OSS-1 同轮；收回本轮 C-1/C-2 折入 022/下轮；M6 立项「不新增编号」 | 采纳 020 方案、顺序、配置切包、JSON 权威；C-1/C-2 折入 022 |
| 前端代表 | B′：M5 不裁 + M6 并行（025→027-lite→后端使能→026→028）；本轮 F-025a+020 | B″：接受本轮 OSS-1+020+契约 ADR；给出 11 行 M6 契约冻结要点；拒绝空 web 骨架 | 采纳 M6 契约冻结与离线/联网边界；F-025a 后移为下轮前端第一件 |
| 开源代表 | C′（C+D）：OSS-1 入口 + 后续 M6/LLM 接线；020–024 后移 | B″：接受 020 并行；接受 M6-v1 收窄与「M6 后端前置不新增编号」；022 作部署门而非入口门 | 采纳 OSS-1 全部要求（5 分钟旅程、脚本化示例、不提交 .db、零新增依赖）；否「整体后移 020」 |

**被记录的少数派/保留意见**：玩家代表保留「console 是入口不是体验解药」的判断（叙事/失败文案仍待 LLM 叙事接线）；开源代表保留「本轮无浏览器可玩/截图」的采用缺口；二者均未阻碍决策。

## 4. 证据与可复跑命令（本轮实测）

- 基线：`git log --oneline -6`（HEAD `1f74de2`）；`uv run pytest -q` → 1129 passed；`uv run pytest tests/replay -q` → 61 passed；四门（ruff/format/mypy 81 files/lint-imports 2 kept）全绿；`--cov=textgame` → 90%。
- 文档：`uv run python .work/status-2026-11-09/rebuild_doc.py verify` → PASS。
- 四角色论文与实测：`.work/team-2026-11-10/{frontend-dev,backend-dev,player-rep,oss-user-rep}.md` + `*-rebuttal.md` + `00-PROBLEMS.md`（`.work/` 已 gitignore，不入库；关键结论已摘入本 ADR 与 D-M6 契约 ADR）。

## 5. 变更纪律

- 只增不改：推翻任一条目须新增 ADR（`supersedes: D-TEAM-x`）→ 回写对应章节 → 重跑全量 + replay → 更新 `docs/PROGRESS.md` §5 与本文件索引。
- 本文件不复制 M5/M6 契约正文；契约细节引用 D-M5-*、D-M6-* 与开发文档章节。
