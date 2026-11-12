# 058 · 2026-11-12 · N5 · 便携包/安装包评估（不实现）

> 切片目标一句话：按用户 2026-11-11「暂不做安装包」边界，评估便携包/PyInstaller/zip+启动脚本/安装器方案，
> 给出成本、杀软/签名/更新/数据目录风险与复评触发条件——**本轮只写文档，不实现、不新增构建依赖**。｜
> 验收命令：见 §1 ｜ 里程碑 N5（第 17 章 §17.9.2）｜ 编号区间 057–060 ｜ 决策：
> [D-N5-1](../decisions/2026-11-12-portable-package-eval.md) ｜ 执行/验收：release-tech

## 1. 目标与验收命令

- 目标（≤90 分钟）：产出一份决策型评估 ADR——现状分发（仓库/zip + uv + 启动器 + wheel）与 A PyInstaller、
  B 便携 zip（embedded Python）、C MSI/Inno 安装器对比；结论明确「本轮不实现」并保留评估位与复评触发条件。
- 验收命令/断言（先定后写）：
  - `git status --short` → 本切片只新增 ADR + 本进度文档（无 exe/安装器/打包脚本/依赖/CI 改动）
  - `git diff -- pyproject.toml uv.lock .github/workflows/ci.yml` → **空**（零新增构建依赖）
  - `Get-ChildItem dist -File` → 仍只有既有 wheel/sdist（0.1.0/0.2.0/0.3.0），无安装包产物
  - `docs/decisions/2026-11-12-portable-package-eval.md` 存在且含 §3 方案对比/§6 复评触发
- 不做什么：不装 PyInstaller/Nuitka/Inno Setup、不写打包脚本、不建 Windows 打包 CI job、不产出/上传 exe、
  不改运行时代码与前端、不改 `docs/design/deployment-and-distribution.md` 与 `RELEASE.md`（状态回写归 Lead）。

## 2. 改动与提交

- 文件：`docs/decisions/2026-11-12-portable-package-eval.md`、`docs/progress/058-2026-11-12-portable-package-eval.md`
- 提交：未提交（工作区留给 Lead 统一提交）；契约变更：无
- 关键结论：**维持现状分发**（057 启动器 + 仓库/zip + `play` 脚本 + wheel）；不引入便携包/exe/安装器；
  不新增任何构建依赖或 CI 打包 job；用户改口后按 ADR §6 触发条件立项新 ADR 再实现。
- 成本要点（量级估算，ADR §3 有完整表）：现状 wheel 0.5MB / 仓库 tracked 9.5MB；A/B 产物约 100–300MB 量级、
  首次启动/冷启动变慢；未签名 exe 的 SmartScreen/杀软误报风险高、签名证书需预算；自动更新/数据目录（只读
  `_MEIPASS` vs `data/`）需额外设计；均新增构建期依赖，违反零新增依赖纪律。
- 护栏检查：零代码/依赖/CI 改动；未生成/提交任何 exe、安装器或打包脚本；未改 `pyproject.toml`/`uv.lock`/CI。

## 3. 验证结果（文档评估证据）

- 现状核对：`RELEASE.md` §3 已交付 `uv build`→wheel/sdist 冒烟；`docs/design/deployment-and-distribution.md`
  §1 列三种分发（启动器/wheel/Docker 暂不提供）；057 启动器依赖 uv + 首次联网 sync（已中文提示）；用户
  2026-11-11「暂不做安装包」由 D-LA-1 被否选项②与第 17 章 §17.9.2 N5-058 两处记录可追溯。
- 体积核对（本机）：`dist/textgame-0.3.0-py3-none-any.whl` **0.5MB**、sdist **0.9MB**、仓库 tracked **9.5MB**、
  `web/dist` **0.32MB/36 files**；`.venv`（含 dev 工具）389MB 仅作「依赖体量」参考。
- 依赖/产物零改动：`pyproject.toml`、`uv.lock`、`.github/workflows/ci.yml` 无 diff；`dist/` 无新增安装包产物。
- 全量 pytest/四门：本切片纯文档、零代码改动，**未跑全量**（按任务约束由 Lead 集成时统一跑）。
- 失败与回滚：无（纯文档；回滚 = 删除本切片新增两文件）。

## 4. 下一步与风险

- 下一步：N5 058–060 保留评估位（059/060 未排期），Lead 集成时回填 PROGRESS §3/§4 与
  `docs/decisions/README.md` 索引；deployment 文档 §5、RELEASE §5/§6 的 058 状态行建议 Lead 回写为「已评估：不实现（D-N5-1）」。
- 风险/开放问题：普通玩家首次启动仍需 uv + 联网 sync（057 已中文引导/文档 FAQ，属已缓解）；若玩家反馈
  uv 是主要门槛，按 ADR §6 触发条件复评；打包方案的杀软误报/签名/更新风险已在 ADR §3 列出，实现前必须逐项验证。
- 需要人工决策：**用户是否需要在 0.4.0 提供便携包/安装包**——当前结论：不（沿用户 2026-11-11 决定）；
  用户改口时按 ADR §6 重新立项。
