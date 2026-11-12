# 场景包批量导入/导出（N3-045，终端 CLI 部分；2026-11-12）

> 切片：N3 功能轨 045 · 目标：把「场景库导入/导出」从网页单文件操作扩展到终端**批量**操作——`--import-scenarios <目录>` 一次导入整个目录的场景文件、`--export-scenarios <目录>` 把场景库一次性导出为可再导入的单文件集合（分享 = 文件交换，无云端/市场，D-N5）。
> 范围：本批只做**终端 CLI + 复用既有 API**；网页多文件 UI 由后续前端批次在同一 045 切片内补（后端端点无需新增，见 §6）。
> 代码：`src/textgame/scenario_pack.py`（纯逻辑：扫描/上限/幂等键/文件名/错误文案）、`src/textgame/console.py`（`--import-scenarios`/`--export-scenarios` 两个入口 + 帮助文本）、`tests/integration/test_console_scenario_pack.py`（21 例）。

## 1. CLI 语义

| 命令 | 说明 |
| --- | --- |
| `python -m textgame.console --import-scenarios <目录> [--import-limit N]` | 扫描目录（**不递归**）下 `*.json` 场景文件，逐个走既有 `POST /api/v1/scenarios` 导入；`N` 默认 50、上限 100 |
| `python -m textgame.console --export-scenarios <目录> [--export-limit N]` | 只读导出场景库：先 `GET /api/v1/scenarios` 列表，再逐个 `GET /api/v1/scenarios/{id}` 取原始 `definition`，写 `<slug>.json`；`N` 默认 50、上限 100 |

- 扫描顺序：文件名 `casefold()` 稳定排序（重跑输出稳定）；目录不存在/不是目录 → 中文可读错误（退出码 1）；空目录 → 友好提示（退出码 0）。
- 目录内文件数超过限制时只处理前 N 个，并提示「共 X 个，本次处理前 N 个；可用 `--import-limit`/`--export-limit` 调整，上限 100」；截断本身不算失败。
- `--import-limit`/`--export-limit` 只能与对应目录参数同用，数值 1..100；`--import-scenarios` 与 `--export-scenarios` 互斥；批量操作不能与 `--list…`/`--card-*`/`--scenario-id`/`--card-id`/`--continue`/`--new`/显式 `--scenario` 同用（清晰中文报错）。

## 2. 导入：逐文件预检 + 稳定幂等

1. 逐文件预检（在发请求前，给玩家更快的逐文件原因）：
   - 大小 ≤ **256KB**（与 `POST /scenarios` 上限一致）；
   - UTF-8 解码（容忍 BOM；Windows 编辑器常见）；
   - 合法 JSON，顶层必须是对象（场景定义是 JSON 对象，数组/标量直接拒）。
2. 通过预检后：`POST /api/v1/scenarios`，body = 文件原始字节，`Idempotency-Key = console-scenario-pack-<sha256(文件内容)>`。
   - **稳定可重放**：同内容必得同一幂等键；重跑同一目录时服务端回放首次 201 响应（同 `scenario_id`、零新增写入），CLI 报告 `[skip]`。
   - 服务端仍是**权威校验**（Schema v1/v2、白名单、内容安全、256KB 双重上限、幂等异参 409）；CLI 只做预检与友好文案，不执行用户 JSON。
3. 跳过判定（不重复建档）：导入前先只读分页拉取场景库现有场景 ID 集合；响应 `scenario_id` 命中「导入前已存在」或「本次运行已创建」→ `[skip]`，否则 `[ok]`。
4. 逐文件报告 + 汇总：
   - `[ok] <文件名> → <scenario_id>`
   - `[skip] <文件名> → 已存在（同内容）· <scenario_id>`
   - `[error] <文件名> → <中文原因>`（如「文件不是合法的场景内容」「场景文件顶层必须是对象」「文件超过 256KB 上限」「场景内容未通过校验」）
   - `汇总：成功 X / 跳过 Y / 失败 Z`；**有失败退出码 1，全成/全跳过退出码 0**。
5. 失败隔离：单个文件失败不影响其它文件（批量不是事务，逐文件尽力而为）；错误文案不显示端点路径/异常堆栈/服务端内部消息。

## 3. 导出：只读、roundtrip 可再导入

1. `GET /api/v1/scenarios?limit=N`（单次上限 100）取列表（按创建时间倒序）；空库 → 友好提示、退出码 0。
2. 逐个 `GET /api/v1/scenarios/{scenario_id}` 取原始 `definition` 对象（**只读，不写库/不写事件**；未知/已删 404 → 该文件 `[error]`，其余继续）。
3. 文件名：
   - 正常写 `<slug>.json`（服务端 slug 约束 `^[a-z0-9-]{2,32}$`，ASCII 安全、无路径分隔符）；
   - slug 缺失/非法 → 回退 `scenario-<scenario_id 后 8 位>.json`；
   - 同一批内 slug 重名（服务端允许同名 slug）→ 第二个起追加 `-<scenario_id 后 8 位>`，不互相覆盖。
4. 文件内容 = 原始定义序列化（pretty 优先，超过 256KB 自动回退紧凑；仍超限则 `[error]` 不导出不可再导入的文件）；不含 summary/时间戳/服务器路径/凭据/scenario_id。
5. 逐文件 `[ok] <scenario_id> → <文件名>` + 汇总 `成功 X / 失败 Y`；有失败退出码 1。导出文件可直接作为 `POST /scenarios` 的 body 或 `--import-scenarios` 输入再导入（roundtrip 测试逐字段比对 `definition` 一致）。

## 4. 错误口径与安全

- 复用第 6 章 8 码失败信封：CLI 按 HTTP 状态 + `error_code` 翻译为玩家可读中文（413/422/403/404/409/5xx 分别给不同原因），**不打印**端点路径、`error_code`、异常类名或 Traceback。
- 用户 JSON 永不执行：终端只搬运字节与展示标题/场景 ID；解析/校验/裁剪全部在服务端 `ScenarioService`。
- 只读导出：不建局、不写事件/状态/golden；导入只写场景资料库（场景库不参与事件溯源）。
- 零新增运行时依赖；不改既有 `--list-scenarios`/`--scenario-id` 行为；`console --check`/`demo --check` 输出逐字不变（SHA256 对照见进度文档）。

## 5. 已知限制

1. 目录扫描不递归（子目录需分别导入）；`.json` 后缀大小写不敏感。
2. 数量上限 100/次：超大目录需分批或多次执行（重复内容因稳定幂等键自动跳过）。
3. 批量非事务：部分文件失败时已成功的文件保留（逐文件报告可重跑；重跑会跳过已成功项）。
4. 导入的标题/作者等来自文件内容；同 slug 可重复建档（与既有单文件导入一致，不做去重策略）。
5. 网页多文件拖拽/批量按钮尚未接入（本批范围外，后端 `POST /scenarios` 已兼容 multipart `file` 部件，前端可复用）。

## 6. 后续（同切片 web 批次）与复跑

- 网页多文件 UI：多选/拖拽 → 逐个 `POST /api/v1/scenarios`（multipart/form-data 的 `file` 部件，前端现状已支持）→ 逐文件结果徽章；导出按钮直接走 `GET /api/v1/scenarios/{id}/export`（`Content-Disposition: attachment`，N1-032 已交付单文件分享端点）。
- 复跑命令：

```powershell
.venv\Scripts\python.exe -m pytest -q tests/integration/test_console_scenario_pack.py
.venv\Scripts\python.exe -m textgame.console --db data/textgame.db --export-scenarios .work/scenario-pack
.venv\Scripts\python.exe -m textgame.console --db data/textgame.db --import-scenarios .work/scenario-pack
.venv\Scripts\python.exe -m textgame.console --check   # 4 轮逐字不变
.venv\Scripts\python.exe -m textgame.demo --check      # 7 屏逐字不变
```
