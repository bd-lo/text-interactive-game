# ADR · 反代信任与访问控制（D-R6-1）

- 状态：**已定案**｜决策人：lead｜整理：docs-release｜日期：2026-11-12｜基线：`f937e48` + R6 未提交工作树
- 关联：R5 §4 第 5 条（R-25 同机反代 loopback 绕过）；[R6 进度文档](../progress/R6-2026-11-12-leftovers.md)；第 6/16 章
- 关联编号：R-25（技术风险审查）、QA-N4-处理项

## 1. 背景与红线

R-25 已加 `TrustedHostMiddleware` + 非 loopback token 鉴权，但**同机反向代理**（nginx/caddy 等监听本机并把请求转发到 127.0.0.1）会让服务端看到的客户端地址始终是 loopback，从而绕过「非 loopback 需 `TEXTGAME_API_TOKEN`」的判定。红线：默认部署（不回环暴露）不得因新增代理支持而变宽松；代理场景必须显式开启且 fail-closed。

## 2. 决策

1. 新增 `TEXTGAME_TRUST_PROXY`（默认 **false**）。**未启用**时：请求带 `X-Forwarded-For` 或 `Forwarded` 头 → 直接 **403 统一失败信封**，提示启用 `TEXTGAME_TRUST_PROXY`（防止客户端伪造头绕过本机判定）。
2. **启用后**：取 `X-Forwarded-For` **第一跳**为客户端地址（`Forwarded` 仅支持基础 `for=`，非法值 fail-closed；XFF 与 Forwarded 同时存在时 **XFF 优先**）。
3. 判定仍复用既有口径：解析出的客户端为 loopback → 免 token；非 loopback → 必须携带 `Authorization: Bearer <TEXTGAME_API_TOKEN>` 或 `X-API-Token`；未配置 token 时非 loopback 一律 403。
4. 单进程部署建议不变：默认只绑定 `127.0.0.1`；需要反代时显式启用本开关并配置 token，且反代侧保证覆盖/清洗客户端传入的转发头。

## 3. 理由与被否选项

- 理由：默认 fail-closed 保持「本机单机」安全边界不变；开关 + 首跳解析让真实部署可控；403 而非静默忽略，避免「以为有 token 保护」的错觉。
- 被否：① 始终信任 XFF（本机客户端可伪造 → 绕过 token）；② 只提供文档建议不改代码（同机反代绕过仍可触发）；③ 改为强制 token（破坏默认单机零配置体验，且 loopback 模式无需 token）。

## 4. 影响面与回写落点

- 实现：`src/textgame/api/app.py`（转发头检测/首跳解析/与 token 中间件串联）；`config/settings.py` 新增 `trust_proxy`。
- 测试：`tests/integration/test_healthz.py` 新增 6 组（未启用带 XFF→403、启用后 XFF loopback/非 loopback、Forwarded 基础/非法 fail-closed、XFF 优先）。
- 文档：第 6 章 §6.x 访问控制、第 16 章部署/运行期策略；`.env.example` 注释（实现 owner 同批）。
- 无迁移、无契约破坏；默认装配行为与 R5 口径逐字等价。

## 5. 已知问题归属

| 编号 | 摘要 | 归属 | 关闭条件 |
| --- | --- | --- | --- |
| — | 仅支持 XFF/Forwarded 基础语义，不支持多级代理链信任边界配置 | 保持简单（单机反代场景足够）；出现多级代理需求另立 ADR | — |
| — | 同机恶意进程可绕过（本机本就可信） | 接受（单机单用户威胁模型） | 用户要求多租户/公网部署时整体重评 |

## 6. 出口验收判据

- 默认 `TRUST_PROXY=false` 时带转发头请求 403；启用后 loopback 放行、非 loopback 无 token 403、带正确 token 200；`pytest tests/integration/test_healthz.py` 全绿；默认离线/本机 e2e 零回归。

## 7. 复审与变更纪律

- 触发复审：用户要求公网/多用户部署、引入 CDN/多级代理、或 OAuth/账号体系时。
- 本 ADR 只增不改；推翻结论 = 新 ADR + `supersedes: D-R6-1` + 回写第 6/16 章与 `.env.example`。
