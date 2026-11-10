# M6B-2 · 2026-11-10 · M6 后端前置（不占编号）· static-spa

> 切片目标一句话：交付 `create_app(static_dir=…)` 静态托管 + SPA fallback（D-M6-5①）——默认托管 `web/dist`、目录缺失时跳过（API 仍可用），API 路由优先、未命中静态的 GET 返回 `index.html`，带同源 CSP/nosniff 与路径遍历防护 ｜ 验收命令：`uv run pytest tests/integration/test_static_spa.py -q` ｜ 里程碑 M6（后端前置 item 4 前半，不占编号）｜ 决策：D-M6-5、D-TEAM-3/4

## 1. 目标与验收命令

- 目标（第 16 章、D-M6-5①）：① `create_app(static_dir=Path|None)`，缺省 `web/dist`；目录不存在时**跳过挂载**、API 与 `/healthz` 正常（`/` 按 404）；② API 路由优先（`/api/*`/`/healthz` 不被 SPA 吞），未命中的 API 路径仍返回 JSON 信封；③ 其余未命中静态文件的 GET 返回 `index.html`（SPA 深链刷新 200），无 `index.html` 时 404；④ 静态/SPA 响应统一带安全头（同源 CSP、`X-Content-Type-Options: nosniff`，第 23 章 §23.7/R-L3）；⑤ 只托管目录内普通文件，拒绝 `..`/目录/越界路径，不返回目录列表；⑥ 运行期零 Node/pnpm（`web/dist` 为构建期产物）。
- 验收命令与真实输出：
  - `uv run pytest tests/integration/test_static_spa.py -q` → **10 passed**（合成夹具 `tests/fixtures/web_dist/`：`index.html` + `assets/app.js`）
  - `uv run pytest -q` → **1249 passed in 18.51s**；`uv run pytest tests/replay -q` → **61 passed**
  - 四门：ruff check/format、mypy strict（84 files）、lint-imports 全绿
- 不做什么：不提交真实 `web/dist`（gitignore）；不做 web-build job（本批在 025 切片加 CI job，远端未验证）；不做鉴权（Q6=A，首版仅 127.0.0.1 单机）。

## 2. 改动与提交

- 提交：`9a0ec5f slice: M6 后端前置（replay 端点 + 静态托管/SPA + /view 背包数量）`
- 文件：`src/textgame/api/app.py`（+97：`DEFAULT_STATIC_DIR=web/dist`、`SPA_SECURITY_HEADERS`、`_register_spa_fallback`、路径安全校验；`create_app(static_dir=…)`）、`tests/fixtures/web_dist/index.html`、`tests/fixtures/web_dist/assets/app.js`（合成夹具）、`tests/integration/test_static_spa.py`（142 行，10 例）。
- 另：同一提交把 `/view` 的 `inventory[]` 增 `qty` 加法字段（`InventoryItemResponse`，同模板实例聚合计数；房间物品 `ItemRefResponse` 契约不变，见 §23.3/§6.7）。
- 关键 diff 摘要：`create_app` 末尾 `if dist.is_dir(): _register_spa_fallback(app, dist)`；fallback 内先判定 API 前缀 → 再查静态文件（`FileResponse` + 安全头）→ 否则 `index.html`；全部响应带 CSP/nosniff；路径经 `resolve()` 越界检查。
- 护栏检查：未改既有 API 路由行为/事件结构/golden；无状态写入；无新增 Python 依赖；Node 不入运行期。
- 契约变更：`/view` inventory 加法字段 `qty`（前端类型按加法扩展，旧客户端忽略未知字段仍可用）；静态托管为 D-M6-5① 落地，无接口破坏。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 本片测试 | `test_static_spa.py` → 10 passed：缺目录跳过/API 优先/SPA 深链 200/无 index 404/安全头/路径遍历拒绝/目录不列表/未知 API 仍 JSON 404/`/healthz` 不受影响/静态资源 200 |
| `/view qty` | `test_game_view.py` 扩展（同模板实例聚合、普通物品 qty=1、房间物品契约不变） |
| 全量 | 1249 passed；replay 61 零回归 |
| 四门 | ruff check/format、mypy strict、lint-imports 全绿 |
| 失败与回滚 | 无 |

## 4. 下一步与风险

- 下一步：M6 后端前置剩余 **character-cards 8 端点 + `card_service` + Alembic 0003**；前端静态托管消费方为 025 骨架/027-lite（`web/dist` 由 `pnpm --dir web build` 生成）。
- 风险：① **web-build CI job 仅本地验证，远端 Actions 未跑**（本机无 runner）；GH runner 若暂不支持 Node 26，按 `.github/workflows/ci.yml` 注释降 24（vite 8/vitest 5 engines 接受 24/26）；② 真实 `web/dist` 的生产分发形态（发布物内嵌 vs 源码自建）在 M6-v1 前定（D-M6-5③，未决）。
- 需要人工决策：无（本轮）。
