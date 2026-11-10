# 安全策略（SECURITY）

感谢你帮助让《文字交互游戏》更安全。本项目默认**离线单机**运行，但涉及 HTTP API、LLM 调用与
人物卡/存档数据，仍请按下面的方式私下报告问题。

## 支持范围

- 当前仅维护 `main` 分支（开发版 `0.1.x`）。
- 覆盖：API 鉴权/越权（`actor_scope`、工具白名单）、路径遍历/静态托管、XSS（前端渲染）、
  密钥泄漏、存档/人物卡数据完整性、依赖漏洞。
- 不覆盖：默认部署在 `127.0.0.1`、无身份认证的单机模式下的「本地用户可读写本地文件」类问题。

## 如何报告

**请不要在公开 Issue 中贴出可被利用的细节、真实密钥或玩家私有数据。**

- 首选：GitHub 仓库 **Security → Report a vulnerability**（私有安全建议）。
- 或邮件联系维护者（GitHub 个人页 `bd-lo` 的公开邮箱），标题注明 `[SECURITY] textgame`。
- 报告请附：复现步骤/命令、影响范围、期望与实际、`uv run python -VV` 与操作系统信息；
  如涉及密钥，请只给前缀（如 `sk-xxxx`），不要给完整值。

## 响应约定

- 收到后 7 天内确认；修复后在 `CHANGELOG.md` 记录（不公开利用细节）。
- 修复前请勿公开 PoC；如已公开，请立即告知以便评估缓解措施。

## 已知的本地开发注意事项（非漏洞）

- `.env`、`data/`、`*.db`、`artifacts/` 已在 `.gitignore` 中；提交前 `secret-scan` hook 会拦截
  `sk-` 长串。
- 真实 LLM Key 只从环境/`.env` 注入；默认 `TEXTGAME_LLM_ENABLED=false`，零联网。
- 静态托管只服务 `web/dist`（或 `TEXTGAME_STATIC_DIR`）目录内普通文件，拒绝 `..`/目录列表；
  API 路由优先，未知 API 路径返回 JSON 信封。
- 前端禁用 `v-html`/`innerHTML`/`eval`，文本一律插值渲染；静态/SPA 响应带同源 CSP。
