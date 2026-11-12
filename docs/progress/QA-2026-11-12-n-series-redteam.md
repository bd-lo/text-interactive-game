# QA-2026-11-12 · N 系列独立 QA 红队与门禁预检（只读）

> 范围：N1 029/030/032、N2 038、N3 047/048 + 054–058 文档 + 全量门禁预检 ｜ 方式：只读探针（证据仅 `.work/qa-n/`）｜ 任务 task-13 ｜ 结论：**无 P0；当前不可直接进入最终提交**（4 项 P1 门禁阻塞，其中 2 项属并行前端切片在途，见 §3）。

## 1. 目标与执行命令（实测数字）

- 全量 pytest：`-p no:randomly` 固定顺序 **2455 passed**；随机顺序 #2 **2455 passed**；随机 #3 与 `seed=1` **2453 passed + 2 failed**（均仅 `test_frontend_contract.py` 两例，属在途前端，见 P1-1）；**首轮随机顺序曾 5 failed（4 console clean-output + 1 streaming disconnect）→ 后续 5 次运行均未复现（P1-4）**。
- `tests/replay` **69 passed**；`tests/security` **172 passed**。
- 四门：`ruff format --check .` **✗ 1 file**（`tests/integration/test_frontend_contract.py`，在途前端切片）；`ruff check .` ✓；`mypy src/textgame` ✓ 118 files；`lint-imports` ✓ 2 kept/0 broken；`check_docs.py` **✗ 25 FAIL**（7 章拼接不一致待重建 + 18 篇进度文档未进 PROGRESS §3，含本 QA 篇）。
- web：`pnpm --dir web test` **64 files / 443 tests passed**（按指令不跑 build）；`pnpm --dir web typecheck` **exit 0**。
- 硬基线：`demo --check` **sha256=f36d3112…2e72**、`console --check` **sha256=c1be0085…cb8f**，与 R5/PROGRESS 记录值逐字一致（exit 0，7/7、4/4）。
- 红队探针（只读脚本）：DSL v2 **27/27**、场景分享 **22/22**、流式 038 **13/13**、存档 048 **29 项核对无阻塞缺陷**、终端 047 参数边界 **16 例**可读且退出码符合。
- 8MB 例外扩散验证：`/games`、`/turns`、`/turns/stream`、`/scenarios`、`/settings/llm` 1.2MB 均 **413**（1MB 不变）；`/saves/import` 1.2MB → **422 到服务层**、>8MB → **413**；`/saves/import/`（尾斜杠）1.2MB → **413**（不在放宽范围，安全）。

## 2. 覆盖与证据路径（`.work/qa-n/`）

| 领域 | 结论 | 证据 |
| --- | --- | --- |
| DSL v2 029/030 | 无 eval/exec/compile/动态 import；环/自引用/跨类型环/未知谓词/引用/超限/深嵌套(1200层)/`__proto__`/表达式字符串全部结构化拒绝；v1 读取兼容与初始状态确定性通过 | `probe_dsl_v2.py`、`dsl-v2-evidence.json`、`probe-dsl-v2.log` |
| 分享 032 | v1/v2 导出→再导入逐字段一致、导出体再次 validate、确定性/零写入/404/软删 404/无内部字段/无密钥路径/再导入幂等与同键冲突 | `probe_share.py`、`share-evidence.json`、`probe-share.log` |
| 流式 038 | 离线 start/delta/done、done.result 与 /turns 等价、NDJSON 注入（换行+伪 done 行）安全分帧、业务失败 error 事件零写入、开关 404、协议 4xx、无密钥泄漏 | `probe_stream.py`、`stream-evidence.json`、`probe-stream.log` |
| 存档 048 | 导出无凭据/路径、checksum 正确、只读；导入 view/哈希/seq-rng 一致、可续玩、源局不变；checksum/状态篡改/未知字段/版本/缺幂等键拒绝且零写入；幂等回放/同键异包 409/失败后同键重试；8MB 例外仅导入路径（其它端点 1MB 413、1.2MB 到服务层 422、>8MB 413） | `probe_saves.py`、`saves-evidence.json`、`probe-saves.log` |
| 终端 047 | 参数边界/互斥/非法 ID/路径穿越/长查询：中文可读、退出码 1/2 正确、无 traceback/内部路径 | `probe_console_047.ps1`、`console-047-results.json`、`console-cases/` |
| 门禁/文档 | 见 §3 P1-3；章节重建清单、进度索引缺口、ADR 索引缺口、进度文档规范全部列出 | `docs-audit.py`、`docs-audit.json`、`gate-*.log`、`full-pytest*.log`、`baseline-hashes.json`、`web-vitest.log` |

## 3. 问题清单（P0/P1/P2）

**P0：无**（已验证切片未发现可执行用户内容、凭据泄漏、越权写入或零写入/回放回归）。

- **P1-1 前端契约测试 2 failed（在途前端切片，非已验证后端）**：`pytest -q tests/integration/test_frontend_contract.py` → ① `web/src/views/ScenarioEditorView.vue` 注释中出现字面 `v-html`，命中安全扫描（无真实 `v-html=` 指令，属扫描误报，需改写注释或收紧正则）；② `web/src/api/types.ts` 新增 `exportScenario: /api/v1/scenarios/{scenario_id}/export`（032 前端接入），但 `tests/integration/test_frontend_contract.py` 的 `EXPECTED_FRONTEND_PATHS` 未同步 → `frontend_paths == expected` 失败。期望：两测全绿；实际 2 failed。归属：031/039 前端 owner 收口。
- **P1-2 `ruff format --check .` ✗**：`tests/integration/test_frontend_contract.py` 需 reformat（在途前端改动）；期望 276 files formatted。
- **P1-3 `check_docs.py` ✗ 25 FAIL（Lead 回写项）**：第 **06/08/12/17/18/20/23** 章拼接不一致（需重建 `docs/开发文档.md`）；**18** 篇进度文档未进 `PROGRESS` §3（029/030/032/035/036/038/039/040/041/042/045/047/048/054/055/056/058 + 本 QA 篇，QA 篇是否入索引由 Lead 定）；ADR 索引缺 **4** 篇（m3-narrative-rebaseline、playwright-a11y-eval、portable-package-eval、scenario-dsl-v2）。
- **P1-4 全量 pytest 首轮 5 failed（后续 5 次运行未复现）**：首轮失败为 `test_console_story.py`×2 / `test_console_scenario.py`×2（均 `console.assert_clean_output`）＋`test_streaming_acceptance.py::test_disconnect_then_gate_release_commits_after_disconnect`；固定顺序与 2 次随机顺序全绿，`disconnect` 用例单独复跑 8/8 通过 → 判定为并发编辑/顺序污染的瞬态 flake。建议最终门禁用固定顺序或固定 seed 复跑两次，并排查 console 测试的全局状态隔离（settings 缓存/env）。证据：`full-pytest.log` vs `full-pytest-2/-fixedorder/-seed1/-3.log`。
- **P2-1 流式零写入回合 `start.turn_id` 语义歧义**：`src/textgame/api/routes/turns.py:130` 用 `expected_version+1`；只读动作（如「环顾四周」）不提交回合时 `start.turn_id=2` 而 `done.result.turn_id=1`（`/turns` 同样 1）。建议 ADR 明示「start 为拟提交回合号」或改取实际 turn。
- **P2-2 存档 checksum 非签名**：重签后仅改 `result.narrative` 的包可导入（048 已注明接受）；状态类篡改被 state_hash/回放拒绝（已验证）。
- **P2-3 分享导出重序列化**：导出与作者原文件键序/空白不逐字一致（字段/值一致）；字节级原样需存 raw（迁移，未做）。
- **P2-4 8MB 例外为 `SAVE_IMPORT_PATH` 精确匹配**：已验证不放宽其它端点；trailing-slash/编码路径不在放宽范围（安全，行为宜在 6.8 注明）。
- **P2-5 存档装配耦合私有属性**：`api/app.py:388`、`api/routes/saves.py:55` 读 `orchestrator._repo/_engine`，建议后续显式注入。
- **P2-6 新增端点 OpenAPI 未声明响应模型**（saves export 返回 `dict`），前端类型暂未接（048 已注明）。
- **P2-7 v2 条件宽松强转**：`{"always": "true"}` 被 Pydantic lax 转 True（非执行面）；可考虑 strict 化。
- **P2-8 终端 `--list-scenarios` 空库提示含字面 "JSON"**（HEAD 既有，非本批）：与 `FORBIDDEN_OUTPUT_TERMS` 口径不一致，未来若对 CLI 输出跑 `assert_clean_output` 会失败。

## 4. 门禁风险与最终提交判断

- **是否可进入最终提交：暂否**。阻塞项：P1-1/P1-2（在途前端切片收口后应消失）、P1-3（Lead 文档回写/重建）、P1-4（全量顺序稳定性复跑确认）。
- 建议 Lead 收口顺序：前端 031/039 收尾 → 重建 `docs/开发文档.md` + `PROGRESS` §3/§4 + ADR 索引 → 复跑「四门 + 全量 pytest（`-p no:randomly`）+ 随机 seed 一次 + replay/security + demo/console --check 哈希 + web vitest/typecheck」。
- 硬不变量复核：旧 4 场景 golden 与 demo/console `--check` 输出哈希与记录值一致（无回归）；用户 JSON 不执行（DSL/分享/存档三路均验证）；8MB 例外未扩散；无凭据进导出包。
- 未验证（按约束不做/不适用）：`web build`（前端在改）、浏览器 e2e（未跑）、真实 LLM 联网路径（离线环境无 key）。
