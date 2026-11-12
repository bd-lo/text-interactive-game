# 039 · 2026-11-12 · N2 · 流式叙事前端（fetch ReadableStream / 增量显示 / 降级一次 / 可取消）

> 切片目标一句话：对局页在 LLM 已启用时用 `POST /api/v1/turns/stream`（NDJSON 5 事件）增量显示叙事，
> 失败降级普通 `/turns` **一次**，可取消且不残留半截文本。｜ 验收命令：见 §1 ｜ 里程碑 N2（第 17 章 §17.9.2）｜
> 编号区间 037–044 ｜ 依赖：038 后端已交付 ｜ 权威边界：ADR D-N2-1..N2-8、`design/narrative-streaming.md`
> §9（11 条实施偏差）｜ 执行/验收：web-a11y

## 1. 目标与验收命令

- 目标（≤90 分钟）：`streamTurn` NDJSON 客户端 + store 流式状态机（delta 追加 / replace 覆盖 / done 权威 /
  失败降级一次 / 取消不卡 busy / 非 LLM 走同步）+ 对局页流式 UI + 四处契约同步；**零新增依赖、禁用 SSE 客户端**。
- 验收命令/断言（先定后写）：
  - `pnpm --dir web typecheck` → 通过
  - `pnpm --dir web test` → 64 files / 443 tests 全绿（含新增流式 client/store/UI 用例）
  - `pnpm --dir web build` → 通过且 `web/dist` 可复现
  - `pytest tests/integration/test_frontend_contract.py -q` → 26 passed（新增 3 例流式契约）
  - `node scripts/e2e-web.mjs --base-url http://127.0.0.1:8791` → 29/29 步、157/157 断言 PASS
- 不做什么：不改后端契约/`TurnResult`/事件/状态；不做 SSE/`EventSource`；不做自动重试；阶段 2（取消恢复/
  重连补增量/全量默认流式）不排期。

## 2. 改动与提交

- 文件（新增 3 + 修改 10）：
  - `web/src/api/types.ts`（`TurnStream*` 5 事件 + 联合类型 + `API_PATHS.streamTurn`）、`web/src/api/client.ts`
    （`streamTurn`：fetch+ReadableStream、逐事件运行时校验、幂等键/AbortSignal/超时、`TurnStreamBusinessError`）
  - `web/src/utils/llm.ts`（`shouldStreamNarrative` 判定）、`web/src/stores/game.ts`（流式状态机：仅在 LLM
    enabled 时尝试；delta/replace/done；失败降级 `/turns` 一次；取消；重试（同步））
  - `web/src/views/GameView.vue`（流式面板/取消按钮/中性提示/重试文案/挂载探测）、
    `web/src/components/TurnTextStream.vue`（`streaming` 模式：aria-live off + 光标）、`web/src/styles/theme.css`
  - 测试：新增 `web/tests/unit/{streamTurnClient,gameStore.streaming}.test.ts`、扩展 `gameView.test.ts`
  - 契约：`tests/integration/test_frontend_contract.py`（路径集合 + 事件模型↔TS + OpenAPI/client 静态 +
    离线 live 实测）；e2e：`scripts/e2e-web.mjs`（+`stream.offline` 步、网络请求流水）；`web/dist` 重建
- 提交：未提交（工作区留给 Lead 统一提交）；契约变更：**无**（只新增端点客户端；`TurnResult` 未变）。
- 护栏检查：无自动重试/无 SSE 客户端/幂等键不旁路；`security-scan` 既有禁令未放宽；`package.json`/lockfile
  零改动；未改后端 Python（只动契约测试）；未改 docs/PROGRESS/CHANGELOG/开发文档.md。

## 3. 验证结果

- 单测：`pnpm --dir web test` → **64 files / 443 tests 全绿**（039 前 62/419；+2 文件/+24 用例）。
  覆盖：5 事件解析与逐事件回调、事件跨 chunk + UTF-8 多字节被切断、未知 type 忽略、replace、error→
  `TurnStreamBusinessError`、404/非 NDJSON/字段漂移、无 done→`STREAM_INCOMPLETE`、超时、外部取消→`CANCELLED`；
  store：delta 增量可见、done.result.text 权威覆盖、404 降级同步恰好一次且同幂等键、协议中断降级、error 事件
  不降级（CONFLICT 自愈）、取消释放 busy 且手动「重试（同步）」不再流式、非 LLM 完全不走流式。
- typecheck/build：通过；`web/dist` 重建（`index-*.js` 60.25kB / gzip 23.53kB），重复构建产物 SHA256 一致。
- 契约测试：`pytest tests/integration/test_frontend_contract.py` → **26 passed**（原 23；+3）。
- 真实浏览器 e2e：**29/29 步、157/157 断言**（新增 `stream.offline` 步 12 条断言）——离线模板 NDJSON 实测
  `start→delta→done` 且「恰好 1 条完整 delta 文本 = done.result.text」；浏览器在 LLM 未配置时提交只发
  `POST /api/v1/turns`、**不请求** `/turns/stream`，全程 console 0 错误。
- 失败与回滚：无。默认离线路径（e2e/golden/demo/console）未触碰；开关关闭时前端按 404 降级（vitest 覆盖）。

## 4. 下一步与风险

- 下一步：040 · 流式验收（真实 LLM 手跑首字/总耗时 + 离线双路径 + 文档回写第 12/23 章/README FAQ/
  R-T8 状态），验收命令 `node scripts/e2e-web.mjs` + `.work` 手跑证据。
- 风险/开放问题：① 真实 LLM 路径无自动化（Q9 禁网），仅 Fake transport + 离线 e2e，真实首字数字由 040 手跑；
  ② 取消语义 = 只停显示、服务端仍可能提交（已有中性提示 + 幂等键重试兜底，D-N2-5）；③ 流式中途失败可能已
  消耗一次 LLM 成本（038 已定首 chunk 后不重试）；④ 本批与 041/042 同工作区叠加，`docs/开发文档.md` 聚合重建
  与 PROGRESS §3 索引由 Lead 统一处理。
- 需要人工决策：无（阶段 2 是否全量默认流式/SSE 待发布期复评，见 ADR §6）。
