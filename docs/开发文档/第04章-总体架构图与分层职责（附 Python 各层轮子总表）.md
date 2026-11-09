## 第 4 章 总体架构图与分层职责（附 Python 各层轮子总表）

### 本章检索关键词
- `evennia python mud framework`、`text-game-engine optimistic CAS row_version`、`langgraph stateful agents durable execution`、`litellm license enterprise MIT`、`toolregistry json schema permission tags`、`sqlalchemy 2.1 requires-python 3.11`、`pydantic v2 requires-python`、`fastapi python 3.10`、`vue 3 composition api typescript`、`vitejs vite build`、`ajv json schema validation`、`github.com/fastapi/fastapi`、`github.com/vuejs/core`、`github.com/vitejs/vite`、`github.com/ajv-validator/ajv`、`pypi.org/project/sqlalchemy`、`pypi.org/project/langgraph`、`pypi.org/project/litellm`、`pypi.org/project/tenacity`、`pypi.org/project/instructor`、`registry.npmjs.org/vue`、`registry.npmjs.org/vite`、`registry.npmjs.org/ajv`

### 实际打开确认的仓库列表
- `fastapi/fastapi`（https://github.com/fastapi/fastapi）— 打开确认：许可证 MIT、最近提交 2026-11（pushed_at 2026-11-07）、star 约 105k、Python ≥3.10 兼容（PyPI 0.162.2 要求 ≥3.10）、确认要点：`pyproject.toml`。
- `pydantic/pydantic`（https://github.com/pydantic/pydantic）— 打开确认：许可证 MIT、最近提交 2026-11-08、star 约 29.4k、Python 3.10 兼容（PyPI 2.14.3 要求 ≥3.10；仓库 main 分支开发版已要求 ≥3.11）、确认要点：`pyproject.toml`。
- `sqlalchemy/sqlalchemy`（https://github.com/sqlalchemy/sqlalchemy）— 打开确认：许可证 MIT、最近提交 2026-11-08、star 约 12.3k、Python 3.10 兼容性：最新 2.1.8 要求 ≥3.11，2.0.57（2026-11-04 发布）要求 ≥3.7，须锁 `<2.1`；确认要点：`pyproject.toml` + PyPI 各版本元数据。
- `langchain-ai/langgraph`（https://github.com/langchain-ai/langgraph）— 打开确认：许可证 MIT、最近提交 2026-11-08、star 约 44.6k、Python ≥3.10 兼容（PyPI 1.2.20 要求 ≥3.10）、确认要点：README（durable execution、human-in-the-loop、memory）+ `libs/langgraph/pyproject.toml`。
- `BerriAI/litellm`（https://github.com/BerriAI/litellm）— 打开确认：许可证 MIT（`LICENSE` 明确 `enterprise/` 目录内容除外，其余 MIT）、最近提交 2026-11-08、star 约 62.9k、Python ≥3.10 兼容（PyPI 1.108.3 要求 ≥3.10,<3.15）、确认要点：`LICENSE` + `pyproject.toml`。
- `jd/tenacity`（https://github.com/jd/tenacity）— 打开确认：许可证 Apache-2.0、最近提交 2026-11（pushed_at 2026-11-01）、star 约 8.8k、Python ≥3.10 兼容（PyPI 9.2.1 要求 ≥3.10）、确认要点：`pyproject.toml`。
- `567-labs/instructor`（https://github.com/567-labs/instructor）— 打开确认：许可证 MIT、最近提交 2026-11（pushed_at 2026-11-06）、star 约 14.1k、Python ≥3.9 兼容（PyPI 1.17.0 要求 ≥3.9,<4.0）、确认要点：`pyproject.toml`。
- `Oaklight/ToolRegistry`（https://github.com/Oaklight/ToolRegistry）— 打开确认：许可证 MIT、最近提交 2026-11-08、star 约 67、Python ≥3.10 兼容（PyPI 0.20.1 要求 ≥3.10）、确认要点：`README_en.md`（多供应商 JSON Schema 生成、tag 权限策略 `READ_ONLY`/`DESTRUCTIVE`/`NETWORK`、MCP/OpenAPI 适配、核心仅依赖 pydantic + llm-rosetta）。
- `YakmovDenjys/Text-Game-Engine`（https://github.com/YakmovDenjys/Text-Game-Engine）— 打开确认：许可证 MIT、最近提交 2026-09、star 约 20、Python ≥3.10 兼容、确认要点：`SCHEMA.md` + `docs/persistence.md`（三阶段 lease → CAS 提交，冲突整段回滚；rewind 与快照语义）。
- `rmusser01/tldw_server`（https://github.com/rmusser01/tldw_server）— 打开确认：多许可作品（服务器代码 GPL-3.0-only，前端为 PolyForm Perimeter，OpenAPI 文档 Apache-2.0）、默认分支最后提交 2026-10（pushed_at 2026-11）、star 约 1.5k、Python ≥3.12；确认要点：`LICENSE` 全文 + `pyproject.toml`；结论：只能参考设计（见第 11 章），不进依赖清单。
- `vuejs/core`（https://github.com/vuejs/core）— 打开确认：许可证 MIT、最近提交 2026-11（pushed_at 2026-11-06）、star 约 54.9k、TypeScript 实现；npm 包 `vue` 最新 3.5.45（MIT，浏览器运行时，Python 兼容性不适用）；确认要点：仓库元数据 + npm registry 元数据。
- `vitejs/vite`（https://github.com/vitejs/vite）— 打开确认：许可证 MIT、最近提交 2026-11（pushed_at 2026-11-06）、star 约 83.9k；npm 包 `vite` 最新 8.3.6（MIT；`engines.node = ^20.19.0 || >=22.12.0`，**仅构建期**使用）；确认要点：仓库元数据 + npm registry 元数据。
- `ajv-validator/ajv`（https://github.com/ajv-validator/ajv）— 打开确认：许可证 MIT、最近提交 2026-09（pushed_at 2026-09-06）、star 约 14.9k；npm 包 `ajv` 最新 8.20.0（MIT，浏览器端 JSON Schema 校验）；确认要点：仓库元数据 + npm registry 元数据。

### 4.1 总体架构图
```text
       [玩家：Web 客户端（Vue 3 SPA + TypeScript）/ CLI / 测试]
                                  |
                                  v
+--------------------------------------------------------------+
| Web 客户端层 web/（Vue 3 + TS，Node 仅构建期）：SPA 路由、     |
| Pinia store、API client、Ajv 校验；只展示与输入，不持有状态    |
| 构建产物 web/dist 由 FastAPI StaticFiles 托管（M6 025–028）    |
+------------------------------+-------------------------------+
                               | HTTP /api/v1（JSON；写操作带 Idempotency-Key）
                               v
+--------------------------------------------------------------+
| 接入层 api/（FastAPI）：路由 + 鉴权 + DTO + 幂等键透传        |
| 只做协议与校验，不写状态、不调 SDK                             |
+------------------------------+-------------------------------+
                               | run_turn(TurnRequest)
                               v
+--------------------------------------------------------------+
| 编排层 orchestration/                                         |
| TurnOrchestrator 管回合生命周期；ProposalQueue 收提案         |
+------------------------------+-------------------------------+
                               | 构造 PerceptionSlice + 可见工具集
                               v
+--------------------------------------------------------------+
| 解析 Agent（agents/parser）：经 LLMClient 输出 ActionIntent    |
| 只输出意图与参数，不写状态、不做数值计算                       |
+------------------------------+-------------------------------+
                               | ActionIntent
                               v
+--------------------------------------------------------------+
| 规则/判定层（domain/rules/）：RuleEngine + RandomSource       |
| 骰子/难度/伤害/掉落 = 确定性计算（种子记入 Event）             |
+------------------------------+-------------------------------+
                               | 判定结果（Result + Event 草稿）
                               v
+--------------------------------------------------------------+
| 裁决（orchestration/Resolver）：冲突消解、优先级、覆盖理由审计 |
+------------------------------+-------------------------------+
                               | Resolution
                               v
+--------------------------------------------------------------+
| 叙事 Agent（agents/narrator）：80~150 字候选文本               |
| 字数/Schema 校验失败 -> 模板兜底（source=template）            |
+------------------------------+-------------------------------+
                               | 屏文本（已校验）
                               v
+--------------------------------------------------------------+
| 状态提交层 GameEngine（唯一写入者）：CAS 校验 + 原子提交       |
| 位置 services/game_engine.py（见第 16 章）                    |
| 返回 Result(ok, data, error_code, message, events)            |
+-------------------+------------------------------------------+
                    |                          |
                    v                          v
+----------------------------+   +-----------------------------+
| 持久化 persistence/        |   | 事件层 EventLog             |
| SQLAlchemy 仓储/快照/回放  |   | 事件溯源、回放、审计        |
+----------------------------+   +-----------------------------+
                    |
                    v
        [一屏文本 + 状态行 -> 玩家；Event 已落盘，可回放]

横切关注点（不参与上图调用顺序）：
  llm/      LLMClient：超时、指数退避重试、限流、幂等键、日志、token 计量、供应商回退
  tools/    ToolRegistry：@tool 注册、JSON Schema 自动生成、白名单、actor_scope、side_effect
  services/ 查询/背包/角色/判定/动作/制造/事件日志：返回 Result 与 Event，自己不做提交
  config/   配置与随机种子策略；main.py 组装依赖
```

架构与分层基线一一对应：**Web 客户端层（`web/`，Vue 3 SPA + TypeScript）只做展示与输入，经 HTTP `/api/v1` 调后端，不持有权威状态；Node 仅构建期使用，产物 `web/dist` 为静态文件，由 FastAPI StaticFiles 托管（M6 025–028，见第 23 章）**。接入层（FastAPI）→ 回合编排（TurnOrchestrator）→ 解析 Agent → 规则/判定（RuleEngine、Resolver）→ 叙事 Agent → 状态提交（GameEngine，唯一写入者）→ 持久化（SQLAlchemy 仓储）+ 事件层（EventLog）。**叙事在提交之前**：叙事候选在提交前生成、提交成功后返回，提交失败则丢弃候选；候选文本校验通过后与判定结果一起被 GameEngine 原子提交，Event 里记录 `narrative_source ∈ {llm, template}`。

### 4.2 分层职责与依赖方向
| 层 | 目录 | 职责 | 允许依赖 | 禁止 |
| --- | --- | --- | --- | --- |
| Web 客户端层（v0.2） | `web/` | Vue 3 SPA + TypeScript：SPA 路由、Pinia store、API client（注入 `Idempotency-Key`）、Ajv 客户端校验；只做展示与输入（M6 025–028，见第 23 章） | 后端 `/api/v1` HTTP 契约（第 6 章） | 持有权威状态、参与判定、直接改 GameState |
| 接入层 | `api/` | HTTP 路由、鉴权、DTO、幂等键透传、错误码转 HTTP 状态码；v0.2 起以 `StaticFiles` 托管 `web/dist` 静态产物（`/api` 路由优先） | 编排层接口、domain DTO | 直接调仓储或 SDK；写状态 |
| 编排层 | `orchestration/` | TurnOrchestrator 串起回合；ProposalQueue 排队；Resolver 裁决 | agents 接口、services、domain | 直接操作 SQLAlchemy；调用模型 |
| 智能体层 | `agents/` | 解析/裁判/叙事/记忆/角色/制造六类 Agent；输入只有 PerceptionSlice | `llm/`、`tools/` 白名单、domain DTO | 持有领域对象引用；直接写状态 |
| 规则/判定层 | `domain/rules/`、`domain/crafting.py` | 骰子、判定、伤害、掉落、确定性制造计算 | 纯 Python 标准库 | 任何 IO、任何 LLM 调用 |
| 服务层 | `services/` | 可信查询与计算 API，返回 `Result` + `Event` | domain、仓储接口 | 自己 commit；直接改 GameState |
| 状态提交层 | GameEngine（`services/game_engine.py`，见第 16 章） | 唯一写入者：CAS 校验 + 原子提交 + 事件落盘 | domain、仓储接口、EventLog | 让任何其他组件直接写库 |
| 持久化层 | `persistence/` | SQLAlchemy 仓储、快照、replay 读取 | domain 模型、SQLAlchemy | 承载业务规则 |
| 事件层 | `domain/events.py`、EventLog | 事件结构、顺序、回放、审计查询 | domain | 被绕过写事件（所有写操作必须产事件） |
| 工具层 | `tools/` | @tool 注册、JSON Schema、白名单、actor_scope、side_effect | services 接口、domain DTO | 让 Agent 直接 import 服务实现 |
| LLM 层 | `llm/` | LLMClient 封装、Prompt 模板、Schema 校验与修复重试、成本归集 | 供应商 SDK、LiteLLM 等 | 被业务代码直接调用 SDK |

依赖方向只有一条：**web →（HTTP `/api/v1`）→ api → orchestration → agents / tools → services → domain；persistence 实现 services 声明的仓储接口；domain 不依赖任何外层**。`web/` 不 import 后端代码，后端也不 import `web/` 代码，二者只通过 API 契约交互；任何反向 import 视为架构违规，在 CI 中加一条依赖方向检查（见第 18 章）。

**GameEngine 的代码位置（定稿）**：第 16 章目录定稿采用方案 A `services/game_engine.py`——与仓储接口同层、依赖方向最顺；方案 B（放 `orchestration/`）不采用，原因是 `orchestration/` 只保留流程编排（TurnOrchestrator、ProposalQueue、Resolver），状态提交必须与仓储接口同层，避免编排层直接触达持久化。

### 4.3 一次玩家输入到状态提交（关键数据流）
触发方可以是 Web 客户端（`web/`，见第 23 章）、CLI 或测试客户端：三者都只调用同一组 `/api/v1` 接口（写操作为 `POST /api/v1/turns`，带 `Idempotency-Key` 与 `expected_version`），不参与下列判定与提交。

1. 接入层校验请求（会话、actor、幂等键）；非法 → `INVALID_INPUT`，不产生 Event。
2. TurnOrchestrator 开新回合（turn_id），构造该角色的 PerceptionSlice，并把可见工具集注入解析 Agent。
3. 解析 Agent 经 LLMClient 输出 `ActionIntent`；Schema 校验失败 → 一次修复重试 → 仍失败 → 澄清或确定性兜底（见第 9、12 章）。
4. 工具白名单 + 参数校验：动作不在该 actor 的可见工具集 → `NOT_ALLOWED`（见第 7 章）。
5. RuleEngine 执行确定性判定（骰子用 RandomSource，rng_seed 来自回合初始种子并记入事件）。
6. 需要智能体覆盖或自由制造时，产出必须过 JSON Schema + 状态一致性检查，且 `reason` 必填（见第 10、13 章）。
7. Resolver 裁决多个提案/冲突的优先级，产出 `Resolution`（见第 11 章）。
8. 叙事 Agent 把判定结果写成 80～150 字候选文本；字数/Schema 校验失败 → 模板兜底，标记 `source=template`（见第 8 章）。
9. GameEngine 校验 CAS 版本号后原子提交：更新 GameState + 追加 Event（`seq` 全局单调递增、每存档从 1 起且跨回合连续，`id = evt_<seq:06d>`；记 params、result、rng_seed、narrative_source）；CAS 冲突 → 最多 3 次尝试（首次 + 2 次重试），仍冲突则回滚返回 `CONFLICT`（见第 11、14 章）。
10. 返回屏文本 + 状态行；EventLog 落盘完成，任意时刻可按 seed + 输入序列回放。

失败分支统一原则：**判定失败可兜底、叙事失败可模板、提交失败必须回滚**；任何失败都不得把未校验内容写入状态。

### 4.4 Python 各层轮子总表
下表以 Python 各层为主选；v0.2 新增的 Web 客户端层（Vue 3 + TypeScript）行列在表末，其 npm 安装型依赖按 §4.5 归入 `pip 依赖` 类（Node 仅构建期）。

| 仓库 | 定位 | 可复用部分 | 许可证 | 活跃度 | 建议 |
| --- | --- | --- | --- | --- | --- |
| fastapi/fastapi | 接入层 Web 框架 | 路由、Pydantic DTO 校验、依赖注入、OpenAPI 文档 | MIT | 最近提交 2026-11，star 约 105k | pip 依赖 |
| pydantic/pydantic | 领域模型 / Schema 校验 | BaseModel、字段校验、JSON Schema 生成（工具层复用） | MIT | 最近提交 2026-11，star 约 29.4k | pip 依赖 |
| sqlalchemy/sqlalchemy | 持久化 ORM / 事务 | ORM、UnitOfWork、事务边界；事件表与快照表 | MIT | 最近提交 2026-11，star 约 12.3k | pip 依赖 |
| langchain-ai/langgraph | 编排层状态图 | StateGraph、durable execution、interrupt、checkpoint 思想 | MIT | 最近提交 2026-11，star 约 44.6k | pip 依赖 |
| BerriAI/litellm | 多供应商 LLM 统一调用 | 统一 completion 接口、供应商回退、成本/token 字段 | MIT（`enterprise/` 目录除外） | 最近提交 2026-11，star 约 62.9k | pip 依赖 |
| jd/tenacity | 重试与退避 | retry/stop/wait 组合、指数退避 | Apache-2.0 | 最近提交 2026-11，star 约 8.8k | pip 依赖 |
| 567-labs/instructor | 结构化输出 + 校验修复 | Pydantic 输出解析、一次修复重试模式（仅用于 `llm/` 内部） | MIT | 最近提交 2026-11，star 约 14.1k | pip 依赖 |
| Oaklight/ToolRegistry | 工具注册与权限标签 | 多供应商 JSON Schema 自动生成、tag 权限（READ_ONLY/DESTRUCTIVE/NETWORK）、MCP/OpenAPI 适配 | MIT | 最近提交 2026-11-08，star 约 67 | 参考设计 |
| YakmovDenjys/Text-Game-Engine | 回合引擎 / CAS 参考 | 三阶段 lease + row_version CAS + rewind/snapshot 语义 | MIT | 最近提交 2026-09，star 约 20 | 参考设计 |
| vuejs/core | Web 客户端框架（Vue 3） | Composition API + `<script setup>`、响应式、单文件组件 | MIT | 最近提交 2026-11，star 约 54.9k；npm `vue` 3.5.45 | pip 依赖 |
| vitejs/vite | Web 客户端构建工具 | Vite 构建与 dev server，产物 `web/dist`（Node 仅构建期） | MIT | 最近提交 2026-11，star 约 83.9k；npm `vite` 8.3.6 | pip 依赖 |
| ajv-validator/ajv | 客户端 JSON Schema 校验 | Ajv 严格校验角色卡 JSON（与服务端共享同一份 Schema） | MIT | 最近提交 2026-09，star 约 14.9k；npm `ajv` 8.20.0 | pip 依赖 |

Python 兼容性备注（前 9 行）：上表 Python 侧各版本均可运行在 Python 3.10（FastAPI 0.162.2、Pydantic 2.14.3、SQLAlchemy 2.0.57、LangGraph 1.2.20、LiteLLM 1.108.3、tenacity 9.2.1、instructor 1.17.0、ToolRegistry 0.20.1、Text-Game-Engine 0.1.0）；其中 SQLAlchemy 2.1.8 起要求 ≥3.11，pip 依赖必须锁 `>=2.0.57,<2.1`；Pydantic main 分支已要求 ≥3.11，升级大版本前复核。前端行（v0.2，后 3 行）：npm 包为浏览器运行时/构建期依赖，不涉及 Python 版本；`vite` 8.3.6 要求 Node ^20.19.0 || >=22.12.0，Node 仅构建期、运行时不依赖 Node。活跃度口径（与第 19 章一致）：基准日 2026-11-08，近 6 个月 = 2026-05～2026-11，活跃度写「默认分支最后提交月份（pushed_at 月份）」，上表 12 个仓库在区间内均有提交。候选表的「建议」值仅在本章语境有效：同一仓库在不同章节的判定可能不同（如 Evennia 在体验参考章为「参考设计」、在第 11/19 章为「放弃」），跨章最终裁决以第 4、19 章为准。建议列的判定理由与拼接边界见下三段。

**复用判断与拼接边界（一）：依赖方向与状态权威**。FastAPI 只承担协议，不承载业务；Pydantic 只出现在边界（请求/响应、工具参数、事件结构）与工具 Schema 生成；SQLAlchemy 只允许被 `persistence/` import，业务层看到的是仓储接口；LangGraph 只能在 `orchestration/` 内部使用，StateGraph 的 checkpoint 不能替代 EventLog——**状态权威永远是 GameEngine + EventLog，编排框架只是流程外壳**；LiteLLM、tenacity、instructor 只能出现在 `llm/` 内部，业务代码不得 import 任何供应商 SDK。这也是「协议对接」的使用边界：MCP/OpenAPI 仅用于未来接入外部工具服务，M1 骨架、M2 单场景 MVP 阶段不启用；启用后外部工具同样须经本项目 ToolRegistry 白名单与副作用标签。

**复用判断与拼接边界（二）：计算与生成**。所有随机、数值、掉落、伤害、制造计算在 `domain/rules/` 与 `domain/crafting.py`，是纯函数、可单测、可固定种子复现；智能体只负责理解意图、选工具、填参数、叙事包装。任何模型输出进入系统前必须过「JSON Schema → 状态一致性 → Resolver」校验链（见第 15 章），失败处理顺序为「修复重试一次 → 确定性兜底 → 返回无法处理」，绝不写状态。ToolRegistry 仅参考设计、不列为依赖：只借鉴多供应商 JSON Schema 生成与 tag 权限模型，`actor_scope` 语义在 `tools/` 内自建（见第 7 章）。

**复用判断与拼接边界（三）：版本与许可证**。本轮核实时发现两个真实约束：① SQLAlchemy 2.1 起要求 Python ≥3.11，守 3.10 下限须锁 `SQLAlchemy>=2.0.57,<2.1`；Pydantic 发布版 2.14.3 仍支持 3.10，但 main 分支已抬到 ≥3.11，升级大版本前需复核。② 排除项：tldw_server 服务器代码 GPL-3.0-only（前端另有 PolyForm、API 文档 Apache-2.0）、要求 Python ≥3.12，只能参考其 Resolver 模块划分（第 11 章）；open-tabletop-gm 为 AGPL-3.0、MnesOS 无 LICENSE 文件、Evennia 要求 ≥3.12，均不得作为依赖或拷贝来源。依赖清单按「FastAPI 0.162.x、Pydantic 2.14.x、SQLAlchemy 2.0.x、LangGraph 1.2.x、LiteLLM 1.108.x、tenacity 9.2.x」锁定，CI 同时跑 Python 3.10 与 3.12（见第 18、19 章）。

### 4.5 四种拼接方式定义（全队统一用语）
第 1 章 1.3 的引用落点在此；任何「引入某仓库」的决定必须标注下列方式之一：

- `pip 依赖`：把仓库作为库安装（`pip install <包名>`）后 `import` 使用；须写明版本约束（如 `>=2.0.57,<2.1`）与入口模块（如 `from pydantic import BaseModel`），不修改库源码。
- `拷贝代码`：把源码（片段或模块）拷贝/改造进本仓库；须许可证允许（MIT/Apache-2.0/BSD 可，GPL/AGPL 与未声明许可证不可），写明拷贝范围、改造点与维护成本。
- `协议对接`：运行时经 MCP / OpenAPI / HTTP 等协议对接外部服务，不把其代码装入本仓库；须写明协议与版本、调用边界（能力、鉴权、超时重试）与失败模式/降级路径。
- `参考设计`：只借鉴设计与模式（分层、表结构、算法、接口形状），不引入任何代码，写清借鉴点与本项目的不同点。
- `放弃`：不采用该仓库（许可证不允许、运行时/Python 版本不兼容、活跃度不达标、范围不匹配等），仍保留在检索记录中并说明排除理由。

归类说明（v0.2）：前端 npm 安装型依赖（`vue`、`vite`、`ajv` 等）归入 `pip 依赖` 类——同为安装后引用的安装型依赖，只是包管理器不同；Node 仅用于构建期（Vite/TypeScript/Vitest），构建产物 `web/dist` 为静态文件由 FastAPI StaticFiles 托管，**不引入 Node 运行时核心依赖**。

### 本章结论
1. 分层与依赖方向定稿：`web（Vue 3 SPA，经 HTTP /api/v1）→ api → orchestration → agents/tools → services → domain`，`domain` 零外部依赖；状态权威只有 GameEngine + EventLog，任何框架或前端都不得成为第二写入者（Web 客户端层在 M6 025–028 交付，见第 23 章）。
2. 各层轮子结论：接入 FastAPI、模型 Pydantic v2、持久化 SQLAlchemy、编排**自建显式 loop**（LangGraph 保留可选封装，第 8 章 §8.4 于 2026-11-09 定案）、LLM 治理 LiteLLM + tenacity + instructor（instructor 仅用于 `llm/` 内部的结构化修复重试）、Web 客户端 Vue 3 + Vite + Ajv（npm 安装型依赖，归入 `pip 依赖` 类，Node 仅构建期）；工具层与回合引擎均按「参考设计」自建（ToolRegistry 仅参考设计、不列为依赖，见第 7 章；回合引擎不直接依赖，见第 11 章）。
3. Python 3.10 下限与 SQLAlchemy 2.1（≥3.11）冲突，依赖清单必须锁 `SQLAlchemy<2.1`，并锁定其余版本（可执行建议，见第 19 章）。
4. 许可证红线落到具体项目：`tldw_server`（GPL-3.0-only）、`open-tabletop-gm`（AGPL-3.0）、`MnesOS`（无 LICENSE 文件）只能参考设计，不得依赖或拷贝。
5. 新增依赖须过「依赖方向检查 + 许可证检查 + Python 3.10 兼容检查」三道门，任一不过不进清单。
6. GameEngine 代码位置已定稿为 `services/game_engine.py`（方案 A），见第 16 章；`orchestration/` 只保留 TurnOrchestrator、ProposalQueue、Resolver。
