# 054 · 2026-11-12 · N4 · m3 叙事受控重基线工具与流程

> 切片目标一句话：为「m3 建模重构会破 golden text_len」建立**受控重基线工具与流程**（ADR D-N4-1）——
> 本轮只交付工具/ADR/证据，**不改变任何叙事文本/事件/哈希**。｜ 验收命令：见 §1 ｜ 里程碑 N4（第 17 章
> §17.9.2）｜ 编号区间 049–056 ｜ 决策：[D-N4-1](../decisions/2026-11-12-m3-narrative-rebaseline.md) ｜
> 执行/验收：release-tech（Lead 审批）

## 1. 目标与验收命令

- 目标（≤90 分钟）：交付「m3 叙事受控重基线」的唯一工具（默认 dry-run；apply 需 `--confirm` + 备份 + sha256 +
  逐字段 diff）、安全边界单测、ADR 与 0-diff 证据；明确真实触发点 = 未来 **m3 建模重构批次**（R2 §6/H4 §5 遗留）。
- 验收命令/断言（先定后写）：
  - `.venv\Scripts\python.exe -m pytest -q tests/unit/test_rebaseline_golden.py tests/replay` → **74 passed in 1.38s**
  - `.venv\Scripts\python.exe scripts/rebaseline_golden.py --golden m3 --dry-run --diff-report --out .work/n-batch/rebase-054.json`
    → **0 diff**、`old_sha256 == new_sha256 == 438d8bed…`（= provenance pin）
  - `ruff format --check` / `ruff check`（2 个变更 py 文件）→ 通过；`mypy src/textgame` → 不回归（本切片零 `src/` 改动）
  - `git status --porcelain -- tests/fixtures/golden` → **空**（tracked golden 零改动）；`src/` 由并行批次改动、非本切片
- 不做什么：不改运行时代码/事件/`GameState`/既有 golden/叙事文本、不 `--snapshot-update`、不新增依赖、不写生产库、
  不 commit、不改 PROGRESS/CHANGELOG/`docs/开发文档.md`/`docs/decisions/README.md`（Lead 统一回写）。

## 2. 改动与提交

- 文件：`docs/decisions/2026-11-12-m3-narrative-rebaseline.md`（新，D-N4-1）、`scripts/rebaseline_golden.py`（新）、
  `tests/unit/test_rebaseline_golden.py`（新，5 例）、`docs/progress/054-2026-11-12-m3-narrative-rebaseline.md`（本文件）、
  `docs/开发文档/第18章-测试策略.md`（§18.3 增补「受控重基线」注记，2 段；`docs/开发文档.md` 由 Lead 重建）
- 提交：未提交（工作区留给 Lead 统一提交）；契约变更：无
- 关键实现：工具按冻结 golden 的 `create_game`+`script` 用**生产同构内存装配**（M3 多角色 + Resolver/Audit +
  `TemplateNarrator` + `DisabledLLMClient`）重放 10 回合，逐字段 diff（turn text_len/state_hash/事件 id/rng_seed/
  审计行/final 字段）；`--apply` 需 `--confirm m3`，先写 `.work/rebaseline/…/*.bak` + `.bak.sha256`（LF 规范化 +
  原始字节双哈希）再原子写目标文件并做写后哈希自检；0 diff 不写、未知 golden/未确认 apply 拒绝（exit 2）。
- 护栏检查：`git status` 证明 `tests/fixtures/golden/`、本切片 `src/` 零改动；工具只写目标 golden 一个文件；
  未改事件结构/GameState/回放语义；未新增依赖。

## 3. 验证结果（2026-11-12 本机，HEAD `553d0a0`）

- **dry-run 0 diff（核心证据）**：`--golden m3 --dry-run --diff-report --out .work/n-batch/rebase-054.json` →
  `result=no-diff`、`diff_field_count=0`、`diff_turn_count=0`、`turns_compared=10`、
  `old_sha256 = new_sha256 = 438d8bed1213ec5f0526a55e941e851bc2f6f0d3e06aea8d15b892433e48c48b`（与
  `test_golden_provenance.py` 的 m3 pin 完全一致）、`hashes_match=true`、checks 全 true、exit 0；
  dry-run 前后 m3 golden 原始 sha256 不变。
- **受控 apply 演练（临时副本，不改 tracked golden）**：先污染副本（`turns[5].text_len +1`、`final_state_hash=0…0`）→
  `--apply --confirm m3 --golden-dir .work/n-batch/rebase-054-demo/golden --backup-dir …/backup --diff-report`
  → `diff_field_count=2`、`diff_turn_count=1`、`changed_fields=["final_state_hash","turns[5].text_len"]`、
  `old_sha256=5059254c…` → `new_sha256=438d8bed…`、写入 10501B、备份 `.bak` 的规范化 sha256=旧值、
  `.bak.sha256` 含 normalized/raw 双哈希、`checks` 全 true；apply 后同副本 dry-run → **0 diff**（幂等）；
  `git status --porcelain -- tests/fixtures/golden` **为空**（tracked golden 未变）。
- **回放/单测**：`pytest -q tests/unit/test_rebaseline_golden.py tests/replay` → **74 passed in 1.38–1.54s**
  （5 新例 + replay 69）；单测覆盖 dry-run 零写入、未知 golden 拒绝、apply 未确认拒绝、apply 只改目标 + 备份/哈希、
  apply 后幂等 + `--fail-on-diff` exit 1。
- **质量门**：`ruff format --check` / `ruff check`（2 个 py 文件）通过；`mypy src/textgame` → **Success: no issues found in
  117 source files**（本切片零 `src/` 改动）。失败与回滚：无；回滚 = 删除新增 4 个文件（tracked golden 从未被改）。

## 4. 下一步与风险

- 下一步：N4 054–056 全部交付 → N4 轨道 8/8 收官；Lead 集成时回填 PROGRESS §3/§4、`docs/decisions/README.md` 索引、
  重建 `docs/开发文档.md`（第 18 章 §18.3/§18.11），并跑全量 pytest。
- 风险/开放问题：① 真实重基线**触发点 = m3 建模重构批次**（R2 §6/H4 §5：首屏硬切、拾荒者建模/统一可见性），
  届时走 ADR §6：dry-run 出预期 diff → apply → **golden 与 provenance pin 同一 commit** → replay 69 + 全量；
  ② 工具 registry 当前只含 m3（m2/m4 扩展需另立 ADR）；③ 若 diff 触及 `event_*`/`state_hash` 语义（非文本长度类）→ 停止并转 D-N6/新 ADR（重基线不得放行契约变更）。
- 需要人工决策：无（本轮不改变行为；apply 是未来批次动作，由 Lead 审批执行）。
