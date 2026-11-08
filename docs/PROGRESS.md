# 总体进展总览（PROGRESS）

> 本文件与 `docs/progress/` 下的单次进度文档配合使用；每次新建进度文档必须在同一提交或 PR 内更新本文件。
> 统计基准日：2026-11-08 ｜ 当前里程碑：M1 骨架（已完成） ｜ 下一个可用编号：005（M2 区间 005–009；M6 前端体验 025–028 已预留）

## 1. 里程碑状态

| 里程碑 | 名称 | 编号区间 | 状态 | 已通过测试任务数/总任务数 | 完成度 | 最近进度文档 | 阻塞项 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M1 | 骨架 | 001–004 | 已完成 | 4/4 | 100.0% | [004](progress/004-2026-11-08-ci-license-gate.md) | 无 |
| M2 | 单场景 MVP | 005–009 | 未开始 | 0/5 | 0.0% | - | 无 |
| M3 | 多角色 | 010–014 | 未开始 | 0/5 | 0.0% | - | 无 |
| M4 | 制造 | 015–018 | 未开始 | 0/4 | 0.0% | - | 无 |
| M5 | API 调用治理 | 019–024 | 未开始 | 0/6 | 0.0% | - | 无 |
| M6 | 前端体验 | 025–028 | 未开始 | 0/4 | 0.0% | - | 无 |

## 2. 完成度计算规则

- 公式：已通过测试的任务数 ÷ 里程碑总任务数 × 100%，保留 1 位小数；分子只统计有测试或回放证据的任务。
- 任务清单以《开发文档》第 17 章 §17.2 切片明细为唯一来源，本表“总任务数”必须等于该表条目数（M1=4、M2=5、M3=5、M4=4、M5=6、M6=4）；作废任务经 lead 确认后从分母移除并记入第 4 节。
- 状态判定：0.0% 且无进行中任务 = 未开始；0.0% < 完成度 < 100% = 进行中；100% 且验收测试全绿 = 已完成；存在阻塞项 = 阻塞（优先级最高）。

## 3. 单次进度文档索引

| 编号 | 日期 | 里程碑 | 主题 | 一句话结论 | 链接 |
| --- | --- | --- | --- | --- | --- |
| 001 | 2026-11-08 | M1 | walking-skeleton | uv 环境基线 + 最小 FastAPI（`/healthz` 200）+ L0 质量门全绿，独立验收 PASS | [001](progress/001-2026-11-08-walking-skeleton.md) |
| 002 | 2026-11-08 | M1 | domain-models | 第 5 章 23 类领域模型逐字落地，246 单测全绿、domain 零框架依赖 | [002](progress/002-2026-11-08-domain-models.md) |
| 003 | 2026-11-08 | M1 | eventlog-replay | RandomSource/DerivedRandom + EventLog 盖章/追加/派生重放，golden seed 与第 10 章示例一致 | [003](progress/003-2026-11-08-eventlog-replay.md) |
| 004 | 2026-11-08 | M1 | ci-license-gate | GitHub Actions（3.10/3.12 矩阵）+ 许可证门禁（GPL/AGPL 阻断），deps 三件套本地彩排通过 | [004](progress/004-2026-11-08-ci-license-gate.md) |

## 4. 编号分配与变更记录

| 里程碑 | 区间 | 已用编号 | 下一个可用 | 备注 |
| --- | --- | --- | --- | --- |
| M1 | 001–004 | 001–004 | - | M1 已完成（4/4）；编号区间已用尽，`005` 起进入 M2 |
| M2 | 005–009 | - | 005 | 下一步 005-turn-api |
| M6 | 025–028 | - | 025 | v0.2 新增「前端体验」里程碑（M1–M5 完成后再启动编号分配） |

## 5. 接口偏离登记（对照开发文档第 6、7 章）

| 偏离编号 | 接口/工具 | 文档定义 | 本次实现 | 决策 | 状态 |
| --- | --- | --- | --- | --- | --- |
| - | - | - | M1-001~004 无服务/工具接口实现，无契约变更 | - | - |

## 6. 风险与阻塞摘要（与开发文档第 20 章同一编号）

| 风险编号 | 摘要 | 等级 | 状态 | 负责人 |
| --- | --- | --- | --- | --- |
| R-L1 | 许可证污染（AGPL/NOASSERTION 混入） | 高 | 缓解中（CI deps job + `scripts/check_licenses.py` 门禁；真实环境 157 包扫描 0 阻断；待首次 push 验证 Actions 运行） | lead |
| R-L3 | 上传恶意 JSON / XSS 注入 | 高 | 未开始（M6 范围，尚未进入） | lead |
| R-L4 | 复制粘贴许可证污染（AI 生成代码夹带 GPL 片段） | 高 | 缓解中（M1 无外部代码拷贝；check_licenses 仅扫依赖许可证，代码片段来源仍靠 review 清单） | lead |
| R-L5 | 上下文 / 提交泄漏密钥（.env、token、真实数据） | 高 | 缓解中（`.env` gitignored + `check_secrets.py` pre-commit 门禁 + `Settings.llm_api_key` SecretStr；审计确认密钥零入库） | lead |
| R-T7 | AI 幻觉 API / 依赖 | 高 | 缓解中（uv.lock 锁定 + mypy strict + 单元/回放测试 + import-linter 门禁） | lead |
| R1（M1 新开） | Python 3.10 兼容：CI 矩阵未实跑 | 中 | 缓解中（本地 3.10.22 实跑 402 passed，含中文路径绕过方式；GitHub Actions 端待首次 push 确认） | lead |
| R2（M1 新开） | 项目自身许可证未定（`textgame` 在 pip-licenses 中为 UNKNOWN） | 低（发布前） | 打开（许可证门禁已默认忽略自身；发布前需决策） | lead |
| R3（M1 新开） | MPL-2.0 传递依赖（orjson/certifi/pathspec/tqdm/hypothesis）文件级义务未登记 | 低 | 缓解中（deps job 已生成 CycloneDX SBOM；文件级义务登记随发布流程） | lead |

## 7. 当前状态与下一步

- 当前状态：**M1 骨架已完成（4/4 = 100.0%）**。001 环境/最小服务、002 领域模型（246 单测）、003 事件日志与固定种子回放（28 回放测试，golden seed 一致）、004 CI/许可证门禁（402 全量测试、deps 三件套本地彩排）均已交付；M1 出口验收：`uv run pytest -q` → 402 passed、ruff/mypy/lint-imports 全绿、uvicorn `/healthz` 200、1+ 条事件派生重放一致、许可证扫描 0 阻断。
- 环境追加项（不计入切片完成度）：DeepSeek API（`deepseek-flash`）凭据与连通性自检已配置——密钥仅存 `.env`（gitignored，不入库）；自检命令 `uv run python -m textgame.llm.smoke`。
- 当前阻塞：无。
- 下一步：005 · turn-api（M2 单场景 MVP），验收命令 `uv run pytest tests/integration/test_turn_api.py`；写操作必须带 `Idempotency-Key`。
- 需要 lead 决策：① 项目许可证决策（发布前，对应 R2）；② 是否把当前 4 个 slice commit push 到 `origin/main`（本地已提交，未 push）；③ 第 20 章 Q9 CI 沙箱 / Q10 上下文白名单需在后续里程碑前定案。
