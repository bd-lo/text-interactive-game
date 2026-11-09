# ADR · M6 后端前置契约冻结（D-M6-1..6）

- 状态：已定案（只追加）｜决策人：lead｜依据：四角色团队决策 D-TEAM-3/4（`2026-11-10-0450-team-direction.md`）+ 独立核实报告（frontend-dev 立场书 §B/§F 与 Round 2 B 节、backend-dev 立场书问题 2、oss-user-rep 采用旅程核验；论文见 `.work/team-2026-11-10/`，不入库）｜日期：2026-11-10｜基线：`1f74de2`
- 关联：M6 025–028 + **M6 后端前置（M6 出口附加项，不新增编号）**；D-TEAM-3/4/5；第 6 章 §6.7、第 16 章 §16.1、第 17 章 §17.2、第 20 章 Q6/Q7、第 23 章 §23.3；`docs/PROGRESS.md` §5（D-005-5）

## 1. 背景

- 第 23 章 v0.2 前端设计（`docs/开发文档/第23章-前端设计（Vue 3）.md` §23.3）依赖 **11 个新端点**：`character-cards` 8 个（schema/list/get/import/validate/save/generate/delete）+ `POST /games` 的 `card_id` 语义 + `GET /games/{id}/replay`；另有静态托管 + SPA fallback + `card_service`。
- 当前实现只有 5 条路径（`/healthz`、`POST /games`、`GET /games/{id}`、`GET /games/{id}/audit`、`POST /turns`）；`CharacterCard` 在 `src/` 与 `tests/` 零定义（`grep CharacterCard` 仅文档命中）；`services/card_service.py` 仅列在第 16 章「尚未落盘」清单；`GET /games/{id}/replay` → **404**（player-rep 实测）；`POST /games` 实际 body 为 `{seed?, scenario?}` 且响应含 `save_seed`，与第 23 章 `{card_id}` 已漂移；`StaticFiles`/SPA fallback 未实现；`.github/workflows/ci.yml` 无 web 构建 job，`.gitignore` 忽略 `web/dist`。
- 结论：按第 17 章 M6 四切片直接开工，026/027 的 e2e 无真实后端可打，**M6 出口不可验收**；必须先冻结并立项「M6 后端前置」。
- 约束：不得把 Node/pnpm 带入运行/部署路径（第 16 章 §16.1 承诺「运行期零 Node」）；M2–M4 golden 与现有 HTTP 契约（D-005-1..4）不得破坏；不新增 Python runtime 依赖。

## 2. 决策

### D-M6-1 立项与验收归属

- **决策**：登记「M6 后端前置」为 **M6 出口附加项（不新增 025–028 编号）**，仿第 17 章 §17.2 M5 出口附加项先例；M6 完成度与出口验收**必须包含**它，不得以「前端 4/4」代替；PROGRESS §1 M6 行备注该前置。
- **理由**：隐藏范围属既有设计承诺缺口，不占新编号可保持 001–028 编号纪律（第 17/22 章），又避免「纸面 4/4」。
- **被否**：新增 029–0xx 编号（编号区间纪律）；并入 025 前端切片（职责混淆、不可独立验收）。

### D-M6-2 `POST /api/v1/games` 加法兼容（card_id 语义）

- **决策**：body 扩为 `{card_id?: str, seed?: int, scenario?: Literal["m2","m3","m4"]}`：① 提供 `card_id` → 按人物卡生成初始 `Character`，忽略 `seed`/`scenario`（同时提供 → 422 `INVALID_INPUT`）；② 不提供 `card_id` → **现行为完全不变**（M2/M3/M4 与既有测试零回归）；③ 响应保留现有 `{game_id, turn, version, snapshot_version, save_seed}`，第 6 章定稿时按需追加 `scenario`（加法）；④ 前端类型以**实际 6 字段**为源，不以第 23 章旧示例为源。
- **理由**：加法兼容保证 M2–M4 golden/契约零回归；同时把第 23 章漂移登记为 D-005-5（PROGRESS §5）。
- **被否**：直接改 body 为 `{card_id}`（破坏既有调用与测试）；另开端点（前端契约重复）。

### D-M6-3 `GET /api/v1/games/{game_id}/replay` 契约

- **决策**：`GET /api/v1/games/{id}/replay?from_seq=<int ≥1>&limit=<int 默认 200 上限 500>` → 200 `{events: [{id, seq, turn_id, actor_id, method, event_type, params, result, rng_seed, ts}], from_seq, to_seq}`；`events` 按全局 `seq` 升序；`result.narrative` 供文本流回放；未知 `game_id` → 404 `NOT_FOUND` 统一信封；`from_seq` 越界/非法 → 422 `INVALID_INPUT`；只读、零事件、不写库、`version` 不变。
- **理由**：第 23 章 §23.3 回放 UI 与审计契约的最小实现；复用现有 `GameRepository.read_events`，不引入新持久化。

### D-M6-4 `GET /api/v1/games/{game_id}/view` 契约（本轮已交付）

- **决策**：`GET /api/v1/games/{id}/view` → 200 `{game_id, turn, version, snapshot_version, status, hp, max_hp, inventory: [{item_id, name}], room: {room_id, title, description, exits, items, npcs}}`；只读零事件、`version` 不变、未知 404；作为 `StatusPanel` 与 terminal console 状态行的**唯一数据源**，替代「把状态行写进叙事正文」。
- **理由**：玩家可见状态（HP/背包）诉求的合法出口，避免触 golden `text_len` 红线（D-TEAM-5）；本轮已随玩家安全子集交付（见 PROGRESS/进度文档）。
- **被否**：把状态行拼进 `TurnResult.text`（破 80–150 字与 golden）；前端从事件自行推演状态（前端永不持有权威）。

### D-M6-5 静态托管、SPA fallback 与分发

- **决策**：① `create_app` 末尾挂 `StaticFiles(directory="web/dist", html=True)`，非 `/api` 前缀且未命中静态文件的 GET 返回 `index.html`（SPA 深链）；API 路由优先；`web/dist` 缺失时仅跳过静态挂载，API 仍可用；② 运行期零 Node/pnpm（构建期 Node 仅 CI/开发者本机）；③ `web/dist` 来源二选一在 M6-v1 前定：发布物内嵌（wheel data 或 release artifact）或 README 明示源码用户自建；④ CI 增加 `web-build` job（`pnpm install --frozen-lockfile` → lint → build → vitest → playwright），**web-build 失败阻断发布**；⑤ 前端安全基线：禁 `v-html`/`innerHTML`/`eval`，文本插值渲染，CSP 同源默认，上传/生成走服务端权威校验（第 23 章 §23.7/§23.8、R-L3/R-C2/R-T6/R-P2）。

### D-M6-6 Q6/Q7 定案与错误/幂等契约

- **决策**：① **Q6 = A**：首版 127.0.0.1 单机、无鉴权；监听非回环地址必须强制鉴权（C 中间态）作为部署门，公告口径不得早于该门；② **Q7 = A**：首版整屏返回（不引入 SSE/流式；复评在 M6-v1 后，若做 SSE 不得改变第 4/9 章提交顺序）；③ 错误契约沿用第 6 章 §6.7 与 D-005-1：业务失败 HTTP 200 + `TurnResult(ok=false)`，协议错误 4xx/5xx 统一失败信封，8 码→HTTP 映射不变；429 + `Retry-After` 仅用于限流端点；④ 幂等：前端每个逻辑动作生成一次 UUID `Idempotency-Key`，网络失败重试复用；`CONFLICT` 刷新 `version` 后换新键；**022 关闭 D-M4-15 之前 UI 不做自动重试**；⑤ 版本链：只认服务端返回的 `version`/`expected_version`，禁止本地自增。

## 3. 交付顺序（M6 后端前置切片，均配 pytest，离线可验收）

1. `view` 端点（D-M6-4）——**本轮已交付**（并入玩家安全子集）。
2. `replay` 端点（D-M6-3）——验收 `uv run pytest tests/integration/test_replay_endpoint.py -q`。
3. `character-cards` 8 端点 + `card_service` + 卡片持久化（Alembic 0004；若 022 先占 0003 则顺延）——验收 `uv run pytest tests/integration/test_character_card_api.py -q`（含 R-L3/R-C2：大小/类型/字段白名单/禁 HTML/限流/成本归集）。
4. 静态托管/SPA fallback + `web-build` CI（D-M6-5）——验收 `uv run pytest tests/integration/test_static_spa.py -q` + CI job。
5. 前端 025 骨架 → 027-lite（现有端点对局回路）→ 027 完整（replay）→ 026（卡导入）→ 028（LLM 生成卡，依赖 M5 接线）——验收按第 17 章 §17.2 M6 表 + 契约测试 `uv run pytest tests/integration/test_frontend_contract.py -q`（OpenAPI ↔ 前端类型快照）。

## 4. 影响面与回写落点

- 第 6 章 §6.7：补 `GET /games/{id}/view`、`GET /games/{id}/replay`、`POST /games` 的 `card_id` 加法语义、`character-cards` 端点清单与错误映射（M6 前置交付时逐项回写）。
- 第 23 章 §23.3：以本 ADR 为准修正已漂移契约（`POST /games` 6 字段、view/replay DTO）；§23.9 注明 M6 后端前置不新增编号。
- 第 16 章 §16.1：静态托管/SPA fallback 与 `web/` 目录、`card_service` 文件清单；§16.5 环境变量（`TEXTGAME_LLM_CONFIG` 已在 020 落地）。
- 第 17 章 §17.2：M6 出口附加项一行（不新增编号）+ M6 完成度口径。
- 第 20 章 §20.3/§20.4：Q6=A、Q7=A 定案记录（关闭）。
- `docs/PROGRESS.md` §5：新增 D-005-5（`POST /games` 契约漂移 → D-M6-2 加法兼容）。

## 5. 验收判据（M6 出口时必须同时满足）

1. 上述 M6 后端前置 1–4 的验收测试全绿，且 live `/api/openapi.json` 与第 23 章契约表逐项一致（契约测试钉住）；
2. `uv run pytest -q` 全绿、`uv run pytest tests/replay -q` **61 例逐值不变**（M2/M3/M4 golden 零回归）；
3. 前端 `pnpm --dir web build` 产出 `web/dist`，FastAPI 托管可访问，SPA 深链刷新不 404；
4. `web-build` CI job 阻断发布；
5. XSS/上传/限流/a11y 用例（R-L3/R-C2/R-T2/R-P2）全绿，且无真实网络依赖。

## 6. 不做什么与变更纪律

- 本轮（04:50 会话）不实现 card/replay 后端、不改 `main.py`/`api/app.py` 以外既有装配、不发布 PyPI、不做 Docker、不把 Node/pnpm 变成运行前置。
- 只增不改：契约变更须新增 ADR（`supersedes: D-M6-x`）→ 回写章节 → 重跑全量与 replay → 更新 PROGRESS §5 与索引；禁止在章节复制本 ADR 正文。
