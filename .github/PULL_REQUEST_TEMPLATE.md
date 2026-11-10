## 变更内容

<!-- 一句话说明这个 PR 解决什么问题；关联 Issue：Closes #___ -->

## 影响面（勾选）

- [ ] 玩法/叙事/场景内容
- [ ] 网页版（`web/src/**`，需要重建并提交 `web/dist`）
- [ ] 终端版（`console.py` / `demo.py`）
- [ ] API / 引擎 / 持久化（可能影响 golden 回放）
- [ ] 文档 / 分发 / 工程（README、scripts、CI）
- [ ] 行为/文档变更已更新 `CHANGELOG.md`

## 自查（PR 合并前必须全绿）

- [ ] `uv run ruff format --check . && uv run ruff check .`
- [ ] `uv run mypy src/textgame`
- [ ] `uv run lint-imports`
- [ ] `uv run pytest -q`（未修改既有断言、未使用 `--snapshot-update`）
- [ ] 若改了 `web/src/**` 或 `web/index.html`：已 `pnpm --dir web build`，并把 `web/dist` 一起提交
- [ ] 新增行为有对应测试；修复类 PR 有回归用例
- [ ] 未提交 `.env`、`data/`、`*.db`、`artifacts/` 或任何真实密钥

## golden 回放纪律

- [ ] 未触碰 `tests/fixtures/golden/` 的已冻结值；确需变更时已按 CONTRIBUTING 提 ADR 并在描述里贴证据

## 证据（可选）

<!-- 验收命令输出摘要、截图、关键日志；网页改动请附前后对比 -->
