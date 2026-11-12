# ADR · m3 叙事受控重基线（N4-054；D-N4-1）

- 状态：已定案（2026-11-12；**本轮只交付工具与流程，不改变任何 golden/叙事文本**） ｜ 决策人：lead ｜
  复核人：release-tech（0-diff dry-run + 受控 apply 临时副本演练 + `tests/replay` 69 全绿，`.work` 证据不入库） ｜
  日期：2026-11-12 ｜ 基线：`553d0a0`
- 关联：第 17 章 **§17.9.2 N4-054**（「m3 叙事受控重基线 … 只改叙事层；状态/事件结构不变」）与 §17.9.3 硬不变量
  （「golden 重基线必须单独立 ADR，仅 033–034 与 054 两条路径」）；[D-N6](2026-11-11-post-m6-roadmap.md)（唯一允许新增
  `EventType` 的切片）；`tests/replay/test_golden_provenance.py`（3 个 golden 的 sha256 pin 门禁）；
  R2 进度 §5/§6 遗留「m3 建模重构（角色/NPC 统一可见性）需一次性受控重基线」；H4 进度 §5「m3 首屏硬切/拾荒者建模未处理」。

## 1. 背景与红线

- **现状**：`tests/fixtures/golden/m3_golden.json` 自 M3-014 冻结（`save_seed=save-42`、10 回合/13 事件/16 审计行、
  turn 2 冲突演示、`final_state_hash=df58e51d…`），`tests/replay/test_m3_golden_replay.py` 逐回合断言
  `event id/rng_seed/state_hash/text_len` 与 golden 精确相等；`test_golden_provenance.py` 把 fixture 的
  **LF 规范化 sha256 钉在代码里**——任何人改 golden（含脚本/编辑器保存）都会失败，静默改基线不可行。
- **待触发需求**：R2/H4 遗留的 m3 体验问题（首屏「与此同时」句中硬切、拾荒者建模与「角色/NPC 统一可见性」）
  一旦重构，**模板叙事文本与 `text_len` 必然变化**，会打破 m3 golden 的逐字/长度断言。这不是回归，而是
  **叙事层的有意变更**，需要一次受控重基线；否则只能永远不做 m3 建模重构。
- **本切片红线**：不改变任何叙事文本/事件/`GameState`/`hash`、不改既有 golden、不改运行时代码、不新增依赖、
  不写生产库；只交付「受控工具 + ADR + 流程证据」。若发现必须改叙事层才能交付，**停止并写触发条件**（本 ADR §6）。
- **流程红线**：禁止 `--snapshot-update`、禁止手工编辑 golden、禁止把「重基线」当作事件结构/状态变化的放行通道。

## 2. 决策

### D-N4-1-1 受控重基线定义：何时允许 / 审批人 / 影响文件

- **允许触发**（满足其一且经 Lead 审批）：
  1. **m3 建模重构批次**（R2 §6 / H4 §5 遗留：角色/NPC 统一可见性、首屏硬切等）导致 m3 叙事文本或 `text_len` 变化；
  2. **叙事层受控文案变更**（`TemplateNarrator`/叙事链路的文案或 80–150 字收敛口径变化，且只影响叙事渲染）；
  3. 其它由 Lead 明确拍板的 m3 叙事变更。
- **不允许**：`EventType`/事件载荷/`GameState` 字段/状态哈希语义/`rng_seed` 派生规则的变化——那属契约变更，
  必须另立 ADR（D-N6 路径）并单独评审；**重基线不得用于放行非叙事变化**。非 m3 golden 不在本 ADR 范围。
- **审批人**：Lead（本仓库决策人）。执行者可以准备 dry-run/报告与候选变更，但 **apply 与提交由 Lead 执行或复核**；
  054 不需要用户拍板（用户拍板仅用于 D-N6 的 `EventType` 例外）。
- **影响文件（受控集合，同一 commit）**：`tests/fixtures/golden/m3_golden.json` +
  `tests/replay/test_golden_provenance.py::_GOLDEN_SHA256`（更新为工具输出的新 sha256）+ 进度文档/PROGRESS 登记；
  运行时代码/事件/状态/golden 生成逻辑的变更必须在**前序批次**完成，本流程只重钉基线。

### D-N4-1-2 唯一流程与工具：`scripts/rebaseline_golden.py`（默认 dry-run）

- **dry-run（默认，只读）**：`--golden m3 [--diff-report] [--out <json>]` → 用冻结 golden 的 `create_game`+`script`
  在**生产同构内存装配**（`InMemoryRepository` + M3 多角色链路/Resolver/Audit + `TemplateNarrator` +
  `DisabledLLMClient`）重放，逐字段 diff：turn 文本长度/`state_hash`/事件 id/`rng_seed`/审计行/final 字段
  （`_comment` 不参与数据 diff，由 Lead 维护）→ 0 diff 即证明「当前代码与冻结基线一致」。
- **apply（受控写入）**：必须 `--apply --confirm m3`；先写备份
  `.work/rebaseline/<UTC>-m3/m3_golden.json.bak` + `.bak.sha256`（LF 规范化 sha256 + 原始字节 sha256），
  再原子写目标文件并做**写后哈希自检**；0 diff 时不写文件（避免无意义改动）。工具**只写目标 golden 一个文件**，
  不碰 provenance/测试/运行时代码/生产库；未知 golden/未确认 apply 一律拒绝（退出码 2）。
- **双跑对比（证据格式）**：① 工具重生成观测 vs 冻结 golden 的逐字段 JSON 报告（`schema_version`/`mode`/`result`/
  `diff_field_count`/`diff_turn_count`/`changed_fields`/`old_sha256`/`new_sha256`/`backup`/`write`/`checks`，
  apply 时含 `diffs` 的 old/new）；② `pytest tests/replay -q` 全绿（69）；apply 后另需更新 provenance pin 并复跑
  replay。证据路径/数字写进进度文档，不接受只写「已重生成」。
- **禁止**：`--snapshot-update`、手工编辑 fixture、用脚本批量改 golden 绕过本工具、在 apply 中夹带叙事层之外的改动。

### D-N4-1-3 为什么只限 m3（工具 registry 白名单）

- 只有 m3 golden 的 `text_len`/逐字叙事断言与**即将进行的 m3 建模重构**直接冲突；m2/m4 golden 的断言口径与
  触发需求不同、当前无叙事变更需求；把 registry 扩到其它 golden 必须另立 ADR 按同一流程评审。
- 工具内置 registry（当前仅 `m3`），未知 golden **拒绝**而不是「猜一个文件改」——受控范围可审计。

### D-N4-1-4 与 D-N6 的边界

- D-N6 规定「新增 `EventType`」是 033–034 跑团模组的唯一例外，其 golden 重基线走 D-N6 的 ADR 与用户拍板流程；
  本 ADR/工具**不承担 EventType 变更的放行**。若 m3 建模重构需要新增事件/改事件结构 → 立即停止本流程，
  改走 D-N6/新 ADR，并把叙事变化拆到不依赖新事件的部分。

### D-N4-1-5 回滚

- apply 前自动备份（`.bak` + 双 sha256）；回滚 = `git revert` 含「golden + provenance pin + 进度文档」的同一 commit，
  或用备份文件覆盖恢复；工具永不自动提交、不改 git 状态。

- 影响面：`tests/fixtures/golden/m3_golden.json`、`tests/replay/test_golden_provenance.py`（pin）、第 18 章 §18.3
  （流程注记）、进度文档/PROGRESS；不涉及 `src/`/`web/`/依赖/CI。
- 回写落点：本 ADR + 进度文档 054；`docs/decisions/README.md` 索引由 Lead 统一追加（本切片不改索引）。
- 验收断言（本轮已实测，见 §5）：dry-run 0 diff 且 `old_sha256 == new_sha256 == 438d8bed…`（= provenance pin）；
  `tests/replay` 69 passed；临时副本 apply 演练：`diff_field_count=2` → 恢复为 pin sha，备份 `.bak`/`.sha256` 齐备、
  `checks` 全 true、只改目标文件；tracked golden 与 `src/` 零改动。

## 3. 契约→切片映射

| 事项 | 落点 | 本轮状态 |
| --- | --- | --- |
| 受控重基线定义/审批/回滚 | 本 ADR D-N4-1-1/4/5 | ✅ 定案 |
| 重基线工具（dry-run 默认 / apply 受控） | `scripts/rebaseline_golden.py` | ✅ 交付 |
| 工具安全边界测试 | `tests/unit/test_rebaseline_golden.py`（5 例） | ✅ 5 passed |
| 0-diff 证据（工具 vs 冻结基线） | `.work/n-batch/rebase-054.json`（`.work` 不入库） | ✅ 0 diff，sha 与 pin 一致 |
| 受控 apply 演练（临时副本） | `.work/n-batch/rebase-054-demo/` | ✅ 2 diff → 恢复 pin，备份/哈希校验全过 |
| 回放不回归 | `tests/replay` | ✅ 69 passed |
| provenance pin 更新流程 | `test_golden_provenance.py` + 本文 §D-N4-1-2 | 流程就绪（本轮无需更新） |
| 测试策略章回写 | 第 18 章 §18.3 加「受控重基线」注记 | ✅（`docs/开发文档.md` 由 Lead 重建） |

## 4. 已知问题归属

| 编号 | 摘要 | 归属 | 关闭条件 |
| --- | --- | --- | --- |
| — | m3 首屏「与此同时」句中硬切、拾荒者不可交互（重基线真实触发点） | 未来 **m3 建模重构批次**（R2 §6 / H4 §5 遗留） | 建模重构完成 → 走本 ADR 流程 apply + 更新 pin + replay 全绿 |
| — | `narrate` 纯文本流式（被否方案）若改叙事口径需重基线 | N2 037–040（未选） | 若改选需新 ADR + 本流程 |
| — | provenance pin 与 golden 必须同 commit 更新，漏更会红 | 每次 apply 的执行者（Lead） | 复核清单：golden + pin + 进度/PROGRESS + replay 69 |
| `R-T9` | AI 测试自证循环（改断言过测） | 全部批次 | 本工具只重生成「数据基线」，不改任何测试断言；apply 需 Lead 审批 |

## 5. 出口验收判据与到期承诺处置

- **054 出口（本轮已达成）**：ADR D-N4-1 定案 + 工具 + 单测 + **dry-run 0 diff 证据** + replay 69 全绿 +
  tracked golden/`src/` 零改动 + 进度文档落盘。实测数字见 054 进度文档 §3 与本 ADR §2 验收断言。
- **本轮结论**：**未改变任何叙事文本/事件/哈希**；工具与流程就绪，**重基线触发点 = m3 建模重构批次**
  （R2 进度 §6「m3 建模重构需一次性受控重基线」与本 ADR 对接）。
- **到期承诺处置**：R2 §6/H4 §5 的「m3 建模重构需受控重基线」从「无流程」变为「有 ADR + 工具 + 证据口径」，
  待建模重构批次执行 apply；PROGRESS §4 的「054 需 ADR D-N4-1」在本 ADR 关闭。

## 6. 后移与复审（触发条件）

出现以下任一情况才执行 apply（否则不放行 golden 变更）：
1. **m3 建模重构批次**启动并完成代码变更（角色/NPC 统一可见性、首屏断句等），dry-run 出现预期 diff；
2. 叙事层文案/长度口径受控变更（须在代码批次验收通过后）；
3. 用户/Lead 明确要求做 m3 叙事重钉。
复审点：apply 后必须复跑 `tests/replay` 69 + 全量（Lead），并在进度文档记录 old/new sha256 与 diff 字段；
若 diff 触及 `event_*`/`state_hash` 语义（非文本长度类），**停止重基线**并转 D-N6/新 ADR。

## 7. 变更纪律

- 本 ADR 只增不改；放宽「允许触发/影响文件/禁止项」必须新 ADR + `supersedes` + 回写第 17 章 §17.9.2 与第 18 章 §18.3；
- 工具改动（registry/写入路径/备份格式）必须同步单测并在本 ADR 追加注记；不得削弱「dry-run 默认、apply 需确认、
  备份 + sha256、只写目标文件」四条安全边界；
- golden 与 provenance pin 必须同一 commit 更新；任何绕过本流程的 golden 变更按回归处理（provenance 门禁必红）。
