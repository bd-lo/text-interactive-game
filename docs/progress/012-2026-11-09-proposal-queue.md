# 012 · 2026-11-09 · M3 多角色 · proposal-queue

> 切片目标一句话：实现纯内存 `ProposalQueue`（提案入队、同 actor 去重、优先级排序、过期迁移），为「多角色只提案、Resolver 统一裁决」提供队列层 ｜ 验收命令：`uv run pytest tests/unit/test_proposal_queue.py` ｜ 里程碑 M3 ｜ 编号区间 010–014 ｜ 执行/验收：multi-actor-dev（AI）/ lead（复核、集成、提交）

## 1. 目标与验收命令

- 目标（第 11 章 §11.2/§11.3、第 21 章 §21.2.7）：`proposal_id = p_{turn_id:06d}_{enqueue_seq:03d}`；同 actor + 同 tool + canonical args 回合内去重；`drain` 按 `(priority desc, enqueue_seq asc, actor_id asc)` 排序并置 `resolved`；`expire_pending` 把未裁决置 `expired`；纯内存、零 IO、无随机/时钟。
- 验收命令与结果：`uv run pytest tests/unit/test_proposal_queue.py -q` → **44 passed**；全量 **960 passed**。

## 2. 改动与提交

- 新增 `src/textgame/orchestration/proposal_queue.py`：`ProposalSource`（player/npc/rule/system）、`QueueEntry`（frozen/extra=forbid，含 proposal_id/enqueue_seq/source/proposal/status）、`ProposalQueue`（构造绑定 turn_id、`entries`/`pending` 返回防御性副本）。
- 新增 `tests/unit/test_proposal_queue.py`（44 用例：排序/去重/proposal_id 格式与单调/drain 截断/过期/resolved 不重复 drain/边界失败分支）。
- 提交：`a5f2be3`。

## 3. 验证结果

- 排序键与第 11 章 §11.3 规则 1 逐字一致；入队序为队列内部计数器（非 `Event.seq`）；重复提案返回既有 entry 且不消耗序号。
- 生命周期固定 `pending → resolved / expired`；状态词与 `Resolution.verdict` 分离（第 11 章 §11.4）。
- 质量门全绿（ruff / mypy strict / lint-imports；import-linter 分层未被破坏）。

## 4. 下一步与风险

- 下一步：013 resolver-cas（Resolver 纯函数裁决 + 确定性 CharacterAgent + CAS 提交集成）。
- 口径说明：`max_items` 仅作为 `drain()` 默认裁决批量，不是入队硬容量（M3 每回合最多 3 条提案，无实际影响；若未来提案来源增多再评估硬上限）。
