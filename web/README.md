# textgame-web（M6-025 骨架）

textgame 的 2G 文字 MUD Web 客户端骨架：Vue 3 + TypeScript + Vite + Vue Router + Pinia。
**Node/pnpm 只在构建期参与**；运行期由 FastAPI 托管 `web/dist`（第 23 章、D-M6-5②）。

## 开发

```bash
pnpm --dir web install
pnpm --dir web dev          # http://127.0.0.1:5173，/api 代理到 http://127.0.0.1:8765
```

## 构建与验收

```bash
pnpm --dir web install --frozen-lockfile
pnpm --dir web build        # 产出 web/dist（gitignore 已忽略）
pnpm --dir web test         # vitest run（jsdom）
pnpm --dir web typecheck    # vue-tsc --noEmit
```

## 边界（M6-025）

- 只用现有端点：`POST /api/v1/games`、`POST /api/v1/turns`、`GET /api/v1/games/{id}`、
  `GET /api/v1/games/{id}/view`；`replay`/`character-cards` 等 M6 后端前置交付后再加法扩展。
- 版本号以服务端为准（`version` 只来自响应，禁止本地自增）；`CONFLICT` 先刷新再让用户重提。
- 幂等键：每个逻辑动作一个 UUID，失败**手动**重试复用同一键；`CONFLICT` 换新键。
- **禁自动重试**（D-M4-15 关闭前，D-M6-6④）；无 SSE/流式（Q7=A）。
- 禁 `v-html`/`innerHTML`/`eval`，文本一律插值渲染（第 23 章 §23.7）；不装 Playwright（e2e 后移）。
- `pnpm-lock.yaml` 入库；`node_modules/` 与 `dist/` 不入库；不在 `package.json` 写 `packageManager`
  （本机无 corepack，CI 用 pnpm/action-setup 钉 12.6.0）。
