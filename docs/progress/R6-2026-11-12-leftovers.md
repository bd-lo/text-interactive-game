# R6 · 遗留项收口批次（R5 §4 + N 系列 QA/评估尾巴）

> 编号：`R6`（范围外遗留项收口批次，不占 001–028 与 029–060 编号；按 `docs/progress/README.md` 例外条款登记）
> 日期：2026-11-12（Asia/Shanghai）
> 前置：HEAD `f937e48`（N 系列 4 个提交本地未推送）+ R6 收口改动（未提交工作树，Lead 即将集成）。
> 依据：[R5 §4 遗留清单](R5-2026-11-12-aurora-glass-and-pitfalls-review.md)、[N 系列 QA 红队报告](QA-2026-11-12-n-series-redteam.md)、[054](054-2026-11-12-m3-narrative-rebaseline.md)/[055](055-2026-11-12-playwright-a11y-eval.md)/[056](056-2026-11-12-long-session-perf.md)/[058](058-2026-11-12-portable-package-eval.md) 的「下一步/风险」。
> 决策：[D-R6-1 反代信任与访问控制](../decisions/2026-11-12-proxy-trust-and-access.md)、[D-R6-2 单进程/多 worker 与启动迁移](../decisions/2026-11-12-single-process-and-migration-lock.md)、[D-R6-3 R-19 pre-queue 迁移](../decisions/2026-11-12-r19-pre-queue-migration.md)。
> 发布口径：**不 bump 版本**（`0.4.0` 待发布、RELEASE.md checklist 未执行），与 N 系列批次一致。

## 1. 前置与来源（要收口什么）

R5 §4 的 20 条遗留 + N 系列 QA 红队的 8 条 P2 + 054/055/056/058 的「下一步/风险」+ 阶段一已发现的文档漂移。可独立完成的纯文档项在阶段一已回写（见 §2 文档列），实现类在本阶段完成。

## 2. 目标与判定口径

1. **安全/部署策略落 ADR**：R-04/R-05（单进程/多 worker）、R-25（反代信任）、R-19（pre-queue）各出一篇 ADR 并回写章节，改动可审计。
2. **行为不回归**：默认装配/离线/旧 golden 逐字不变（m4 golden 5 例零改动、demo/console `--check` exit 0、e2e 断言不放宽）。
3. **安全边界不降**：反代默认 fail-closed、多 worker 默认拒绝、Resolver 内零 LLM 调用。
4. **数字只写实测**（见 §4）；发布口径不变（0.4.0 未发布）。

## 3. 交付内容

| 方向 | 交付 | 关键位置 |
| --- | --- | --- |
| **R-19 pre-queue 迁移**（D-R6-3） | 新增 `orchestration/pre_queue.py`；玩家提案入队/裁决前预生成一次 `CraftDraft`（含一次 Schema 修复）；`resolve(..., prepared_crafts=...)` 按 `proposal_id` 透传载荷（不塞 `Proposal.args`）；`CraftService._execute_prepared_free_craft` 纯校验/`calculate_craft_result` 重算；`ctx.caller=="resolver"` 无载荷→`INVALID_INPUT` 且永不回落生成器；CAS 重试复用同一载荷；旧「服务内生成」只保留 M2/直接 Gate（caller=player）；`TEXTGAME_ALLOW_RESOLVER_LLM`/网页 risk 二次确认保留为历史兼容（文案更新） | `orchestration/pre_queue.py`、`tools/gate.py`、`services/craft_service.py`、`orchestration/resolver.py`、`orchestration/turn_orchestrator.py`；web 设置页 `SettingsView`/`freeCraft`/`client.ts` |
| **R-25 反代信任**（D-R6-1） | `TEXTGAME_TRUST_PROXY` 默认 false；未启用时带 `X-Forwarded-For`/`Forwarded` → 403（防同机反代绕过）；启用后 XFF 首跳判定、Forwarded 基础 `for=` 支持（非法 fail-closed、XFF 优先）；非 loopback 仍要求 `TEXTGAME_API_TOKEN` | `api/app.py`、`config/settings.py` |
| **R-04/R-05 单进程策略**（D-R6-2） | `WEB_CONCURRENCY`/`UVICORN_WORKERS`/`GUNICORN_WORKERS` >1 且未 `TEXTGAME_ALLOW_MULTI_WORKER=true` → 启动 `RuntimeError`（中文说明单进程假设）；`ensure_schema` 升级路径加 `*.migrate.lock` 跨进程文件锁（Windows `msvcrt` / POSIX `flock`，10s 超时、锁内重判；健康/新库不建锁） | `main.py`、`persistence/database.py`、`config/settings.py` |
| 配置/凭据/smoke | `TEXTGAME_FREE_CRAFT_SETTINGS_FILE` 等 3 个 Path 字段空串→None（validator）；凭据文件 Windows 尽力 `icacls` 收紧（失败仅 WARNING；POSIX 仍 0o600）；smoke `max_tokens=128` + `content_len` 输出、空 content 仅 WARN 不误判 | `config/settings.py`、`llm/credentials.py`、`llm/smoke.py` |
| 健壮性/QA P2 | `list_games` 最后叙事改单条有界 SQL（`read_last_narrative_event`，>200 事件边界测试）；`_verified_games` 有界 LRU(4096)；R-16 `/view.lore` 与下回合注入 turn 一致性测试；chunked 超限 JSON 端点 413 修复（`_BodyTooLargeError(BaseException)` 穿透 FastAPI body 解析）；v2 条件 `StrictBool`/`StrictInt`（v1 lax 冻结）；终端空库提示去字面 "JSON"；`GET /games/{id}/export` 加 `response_model=SavePackage`（OpenAPI 断言 + 响应逐字不变） | `persistence/sqlite_repository.py`、`services/query_service.py`、`api/app.py`、`api/routes/saves.py`、`domain/user_scenario.py`、`console.py` |
| 前端两项 | 夜间暗色 token 提亮（`--fg-dim #a9b0cf→#c6cce2`、`--accent #9db4ff→#c8d3ff`、`--ambient-1..4` 0.55/0.45/0.34/0.32→0.34/0.28/0.22/0.20）；375/320px「视觉设置」菜单改全宽下拉；e2e 新增 `mobile.visual-menu` 步 | `web/src/styles/theme.css`、`web/src/components/VisualSettings.vue`、`scripts/e2e-web.mjs` |
| 文档与 ADR | 新增 ADR D-R6-1/2/3 + `docs/decisions/README.md` 索引；第 12/13/16/18/23 章回写；R6/PROGRESS/CHANGELOG/RELEASE；阶段一已修文档漂移（e2e 已接入 CI、第 18 章 Playwright/axe 评估结论、RELEASE 031 过期、流式 `start.turn_id`、分享导出重序列化、8MB 精确匹配） | `docs/decisions/*`、`docs/开发文档.md` 与分章、`docs/PROGRESS.md`、`CHANGELOG.md`、`RELEASE.md` |

## 4. 验证结果（实跑，当前工作树）

- **后端**：`uv run pytest -q` → **2521 passed**；`tests/replay -q` → **69 passed**（旧 golden 零改动，m4 golden 5 例零改动）。
- **静态门**：`ruff format --check .` → 281 files already formatted；`ruff check .` 全过；`mypy src/textgame` → **119 files** 无问题；`lint-imports` → 2 kept；`uv run python scripts/check_docs.py` → **PASS**。
- **前端**：`pnpm --dir web test` → **69 files / 481 tests**；`typecheck`/`build` 通过；`web/dist` **38 文件**，连续两次 build 逐文件 SHA256 一致（无 drift）。
- **真实浏览器 e2e**：**33/33 步、211/211 断言 PASS**（原 32 步/205 断言；新增 `mobile.visual-menu`）；离线 + headless Chrome。
- **确定性基线**：`demo --check` 7/7、`console --check` 4/4 exit 0，逐字不变。
- **新增测试差分**：全量 2456 → 2521（**+65**）：pre-queue **+16**（`test_free_craft_pre_queue.py` 6 + `test_craft_pre_queue.py` 10）、反代/多 worker/迁移锁/健壮性等 **+49**（含 chunked 413 JSON 端点 +2、v2 strict、OpenAPI、console、R-16/R-21 边界等）。
- **关键行为证据**：FakeLLM 探针 pre-queue 1 次生成 → 两次裁决（1 次 CAS 冲突重试）LLM 调用恒 1，`resolver.py` 无 `complete_json`/generator 调用；默认关闭/离线装配与 golden 逐字不变。

## 5. R5 §4 20 条最终状态

| 状态 | 条目 |
| --- | --- |
| **已修/已收口（10 条）** | 1 R-04 单进程防护+迁移锁；2 R-05 多 worker 拒绝/迁移锁；4 R-19 pre-queue 迁移；5 R-25 反代信任；7 R-16/R-18/R-21 测试与实现；8 `TEXTGAME_FREE_CRAFT_SETTINGS_FILE` 空串校验；9 夜间接管对比度（CDP 字形像素实测最差 accent 2.66→**4.89**、fg-dim 2.93→**5.34**、渐变标题 5.82→**7.48**，夜间全部 ≥4.5；新 token 纯底 dim/bg **12.47**、accent/bg **13.49**）；10 375/320 视觉设置菜单；18 Windows 凭据 icacls 尽力；19 smoke max_tokens/`content_len` |
| **已接受/仍保留（10 条）** | 3 R-12 非完整 RE2（启发式+长度上限，接受）；6 R-08 scheduler 未注入生产（latent，接受）；11 Playwright/axe 后移（已评估 D-N4-2 不引入）；12 RELEASE/PyPI 未发布（维护者拍板）；13 m3 每回合 4 次调用超设计预算（本批不改设计，记遗留）；15 judge 生产不可达（机制就绪）；16 0.6 置信阈值（建议下一批评估）；17 参数名≠实体 ID（建议下一批）；20 零事件 `narrative_source=null`（L3 设计口径）；14 R-23 回归已在 R5 修复（本表不重复计） |
| 说明 | 14 条为 R5 已修复记录；16/17 属自由度改进（需设计决策），13 需 M6 预算口径决策，均不阻塞本批发布口径。 |

## 6. N 系列 QA P2 最终状态

- **已修（3）**：P2-6 OpenAPI 响应模型（`GET /games/{id}/export` `response_model=SavePackage`，import 原本已有）；P2-7 v2 条件 `StrictBool`/`StrictInt`（v1 lax 冻结）；P2-8 终端空库提示去字面 "JSON"。
- **已回写文档（4）**：P2-1 流式 `start.turn_id` 语义（ADR D-N2-2 + 第 12 章 §12.11）；P2-2 checksum 非签名（第 6 章 §6.8「已知限制」+ design §6，复核已有→接受）；P2-3 分享导出重序列化（design §10 + ADR §4）；P2-4 8MB 例外仅 `SAVE_IMPORT_PATH` 精确匹配（第 6 章 §6.8 + design §5）。
- **保留（1）**：P2-5 存档装配读私有属性 `orchestrator._repo/_engine`（建议后续显式注入；本批不改 `api/app.py` 装配面以避免扩大改动）。
- **N 系列余项**：N1 033–034 跑团世界模组 P0（待用户拍板新增 EventType，D-N6）；N3 046 世界书条目编辑（可选未排期）；网页场景包/存档多文件 UI（后端端点已就绪）。

## 7. 遗留与后续

1. **发布**：`0.4.0` 未发布（不 bump 版本、未 tag）；发布前按 `RELEASE.md` §2 checklist 复跑并维护者拍板 tag/Release/PyPI。
2. **兼容开关简化**：`TEXTGAME_ALLOW_RESOLVER_LLM`/网页 risk 二次确认技术风险已消除，保留为历史兼容，M5 后评估移除并回写第 12/13/23 章与 `.env.example`。
3. **NPC 自由制造**：内置确定性 Agent 不产生该提案（零 LLM 安全拒绝）；需要时扩 pre-queue + 新 ADR。
4. **多 worker**：默认拒绝/需 opt-in；SQLite 仍建议单 worker（跨进程幂等需 DB 唯一键，另立 ADR）。
5. **下一批建议**：0.6 置信阈值与「参数名≠实体 ID」友好映射（自由度改进，需设计决策）；P2-5 私有属性显式注入；m3 LLM 调用预算口径。
