# textgame-web（Vue 3 网页客户端）

textgame 的 2G 文字 MUD Web 客户端：Vue 3 + TypeScript + Vite + Vue Router + Pinia。
**Node/pnpm 只在构建期参与**；运行期由 FastAPI 托管 `web/dist`（第 23 章、D-M6-5②）。

> **玩家不需要看这个文件**：仓库已完成构建并把 `web/dist` 入库（R2 分发方案 A），
> fresh clone 直接 `.\scripts\play.ps1`（或 `./scripts/play.sh`）即可玩网页版，无需 Node。
> 只有**修改前端源码**时才需要下面的开发流程。

## 开发

```bash
# 1) 后端：仓库根执行。后端端口务必与下面 vite 的 /api 代理 target 一致
#    （当前 web/vite.config.ts 代理到 8765；建议改成 8000，与 play 脚本默认端口统一）
uv run --no-dev uvicorn textgame.main:app --reload --port 8000

# 2) 前端 dev server（另一个终端）：http://127.0.0.1:5173，/api 代理到后端
pnpm --dir web install
pnpm --dir web dev
```

## 构建与验收

```bash
pnpm --dir web install --frozen-lockfile
pnpm --dir web build        # 重新生成 web/dist（已入库；改前端后必须重建并提交产物）
pnpm --dir web test         # vitest run（jsdom）
pnpm --dir web typecheck    # vue-tsc --noEmit
```

改了 `web/src/**` 或 `web/index.html` 后，**必须**重新 `pnpm --dir web build` 并把 `web/dist`
一起提交——否则玩家拿到的网页版仍是旧版本（CI 会对产物一致性做校验）。

## 边界（第 23 章）

- 只用现有端点：`POST /api/v1/games`、`POST /api/v1/turns`、`GET /api/v1/games/{id}`、
  `GET /api/v1/games/{id}/view`、`GET /api/v1/games/{id}/replay`、`/character-cards/*`。
- 版本号以服务端为准（`version` 只来自响应，禁止本地自增）；`CONFLICT` 先刷新再让用户重提。
- 幂等键：每个逻辑动作一个 UUID，失败**手动**重试复用同一键；`CONFLICT` 换新键。
- **禁自动重试**（D-M4-15 关闭前，D-M6-6④）；无 SSE/流式（Q7=A）。
- 禁 `v-html`/`innerHTML`/`eval`，文本一律插值渲染（第 23 章 §23.7）；不装 Playwright（e2e 后移）。
- `pnpm-lock.yaml` 入库；`node_modules/` 不入库；`web/dist` **入库**（R2 变更，见 `.gitattributes`
  `web/dist/** -text -diff`）；不在 `package.json` 写 `packageManager`（CI 用 pnpm/action-setup 钉 12.6.0）。
