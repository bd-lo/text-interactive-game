## 第 3 章 MVP 范围与非目标

### 本章检索关键词
- `text-game-engine turn engine CAS row_version`、`MnesOS stateless event-sourced agentic RPG LangGraph`、`evennia python mud framework`、`github.com/YakmovDenjys/Text-Game-Engine`、`github.com/neolaw84/MnesOS`

### 实际打开确认的仓库列表
- `YakmovDenjys/Text-Game-Engine`（https://github.com/YakmovDenjys/Text-Game-Engine）— 打开确认：许可证 MIT、最近提交 2026-09、star 约 20、Python ≥3.10 兼容、确认要点：`SCHEMA.md` 与 `docs/persistence.md`（`tge_campaigns.row_version` 乐观 CAS、三阶段 lease、CAS 冲突回滚、rewind 后按 `memory_visible_max_turn_id` 过滤记忆），是 MVP 回合模型的主要参考。
- `neolaw84/MnesOS`（https://github.com/neolaw84/MnesOS）— 打开确认：许可证未声明（根目录无 LICENSE，元数据称 MIT）、默认分支最后提交 2026-05（pushed_at 2026-07）、star 0、Python ≥3.12、确认要点：README（无状态服务端、树形事件源、分支时间线），作为「事件溯源 + 无状态回合」的参考，不引入。
- `evennia/evennia`（https://github.com/evennia/evennia）— 打开确认：许可证 BSD-3-Clause、默认分支最后提交 2026-10（pushed_at 2026-11）、star 约 2.1k、Python ≥3.12 不兼容 3.10、确认要点：README 与 `pyproject.toml`，用于确认「MUD 服务端框架不适合作为 MVP 底座」。

### 3.1 MVP 目标
MVP 对应 **M2 单场景 MVP**（编号 005–009，见第 17 章）。定义：玩家在一个房间里完成「看、走、拿、用、说」五类动作，系统做确定性判定、把每个写操作记成事件，并能在任意时刻用固定种子重放到当前状态。目标是先证明「可信状态机」成立，再谈多角色与生成式内容。Web 前端不在 MVP 范围内：**Web 前端在 M6（025–028）交付；M2 以 API 可玩为验收，前端不阻塞 M2**（见第 23 章）。

### 3.2 验收标准
| 编号 | 验收项 | 可执行验收条件 |
| --- | --- | --- |
| A1 | 单场景 | 1 个 Room、3 个 Item、1 个 NPC；look/move/take/use/talk 经 HTTP 或 CLI 可通；集成测试每类 ≥1 条 |
| A2 | 单角色 | Character 字段按第 5 章生效；hp/背包/位置变化正确；非法动作返回 `Result(ok=False, error_code ∈ {INVALID_INPUT, NOT_FOUND, NOT_ALLOWED})` 且状态不变 |
| A3 | 基础判定 | RuleEngine 对观察/移动/对话给出确定性结果；骰子走 RandomSource 并记 rng_seed；同种子同输入结果一致 |
| A4 | 事件溯源 | 每个写操作产生 Event（`turn_id`、`seq`、`actor_id`、`method`、`params`、`result`、`rng_seed`，完整字段见第 5 章）；`seq` 全局单调递增（每存档从 1 起、跨回合连续），`id = evt_<seq:06d>`；EventLog 可按 seq 顺序完整回放 |
| A5 | 固定种子回放 | 给定 seed + 同一输入序列，replay 得到的 GameState 与原状态逐字段相等（state hash 一致） |
| A6 | 进度文档制度 | `docs/progress/005`～`009` 共 5 篇进度文档 + `docs/PROGRESS.md` 的 M2 单场景 MVP 状态与完成度更新；完成度 = 已通过测试的任务数 / M2 总任务数 |

A5 的回放方式是**待定**项：A 状态哈希对比（实现简单、定位粗）；B 事件序列逐条 diff（能指出第一条不一致事件，定位更准）。建议先做 B 的最小版。

**切片验收原则（v0.3 vibe coding）**：每个 vibe slice（30–90 分钟，见第 17 章）以**可运行 demo + 自动验证**为准——lint、类型检查、相关单测，涉及状态/回放的切片必须跑固定种子回放；文档为活文档，里程碑结束时用 1 次会话轻量回写偏差（见第 17、22 章）。

### 3.3 MVP 用户故事
- 作为玩家，我输入「检查木箱」，系统返回 ≤150 字结果，且这次检查在事件日志里可查。
- 作为玩家，我攻击 NPC 时得到世界内的拒绝叙事而非报错崩溃，状态不受影响。
- 作为开发者，我能用事件日志把异常状态重放到出问题的回合。

### 3.4 非目标（MVP 明确不做）
多角色并发与 ProposalQueue/Resolver 全链路（M3 多角色）｜制造系统与自由制造（M4 制造）｜多供应商回退、成本归集与批量合并（M5 API 调用治理）｜内容规模（多场景、任务链、NPC 日程）｜图形或完整 Web 前端（MVP 仅最小 CLI / HTML 外壳；Web 前端见 M6 025–028，M2 以 API 可玩验收，不阻塞 M2）｜本地模型训练与 SFT/DPO/GRPO｜多人联网、实时战斗、多语言。设计见第 8～15 章，MVP 只留占位。

### 3.5 交付物清单
1. `src/textgame/` 骨架：`api/`、`domain/`、`services/`、`tools/`、`agents/`、`orchestration/`、`llm/`、`persistence/`、`config/`、`main.py`（第 16 章给出完整目录）。
2. 可运行 demo：一条 HTTP 端点或 CLI 入口，跑通「输入一句话 → 一屏输出」。
3. 测试：五类动作集成测试、固定种子回放测试、非法输入与越权输入用例。
4. 文档：5 篇进度文档、`docs/PROGRESS.md` 更新、README 中的启动命令。
5. 数据：一个房间的最小内容包（房间描述、物品、NPC 对话模板），不接 LLM 也能跑。

### 3.6 待定：MVP 是否接真实 LLM
| 方案 | 做法 | 取舍 | 建议 |
| --- | --- | --- | --- |
| A | 纯确定性：解析用关键词/指令正则，叙事用模板 | 最快跑通状态机与回放，零 API 成本；体验差距大 | MVP 前段采用 |
| B | 解析 + 叙事各一次模型调用，输出均过 Schema 校验 | 最接近目标体验；引入延迟与成本不确定性 | MVP 后段切换 |
| C | 仅解析接模型，叙事用模板 | 折中；但玩家最容易感到「AI 味」缺失 | 备选 |

建议 M2 前 3 篇进度文档走 A、后 2 篇切到 B，并在进度文档记录切换的失败率与延迟（规则见第 22 章）。

### 3.7 候选轮子与复用判断
本章为范围定义类章节，不新增选型。已按 `text-game-engine turn engine CAS row_version`、`MnesOS stateless event-sourced agentic RPG LangGraph` 等关键词检索并打开仓库，方向为「回合引擎/CAS」「事件溯源/无状态服务端」「MUD 框架」；结论：**检索后未找到**同时满足「单场景 + 固定种子回放 + 进度文档验收」的现成 MVP 脚手架，故按第 4 章分层自建骨架；回合模型参考 Text-Game-Engine（见 3.2 的 A4/A5），不引入其代码。

### 本章结论
1. MVP 的唯一目标是证明「可信状态机」：五类动作可用、事件完整、固定种子可回放，其他一律后置。
2. 验收以 A1～A6 为准，每条有可执行验证方式；A6 把进度文档制度纳入验收，缺一不算完成。
3. 回合模型参考 Text-Game-Engine 的 CAS + 快照语义，但不引入其代码；Evennia、MnesOS 因 Python ≥3.12 与许可证问题排除。
4. MVP 先跑纯确定性版本（方案 A），再切「解析 + 叙事各一次调用」（方案 B），避免一上来被 API 成本与不稳定性拖住工期。
5. 非目标清单同时是接口占位清单：第 8～15 章设计的多角色、制造、治理能力，MVP 代码里只留空实现与 TODO。
