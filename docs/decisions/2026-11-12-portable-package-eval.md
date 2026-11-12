# ADR · 便携包 / PyInstaller / 安装包评估（N5-058；D-N5-1）

- 状态：已定案（评估结论：本轮**不实现**） ｜ 决策人：lead ｜ 复核人：release-tech（现有分发产物/文档核对 + 方案成本评估，`.work` 证据不入库） ｜ 日期：2026-11-12 ｜ 基线：`553d0a0`
- 关联：第 17 章 **§17.9.2 N5-058**（「用户 2026-11-11 表示暂不做安装包，评估只写文档、不实现」）与 §17.9.5；
  [D-LA-1..N6](2026-11-11-player-launcher-and-exit.md)（057 启动器已交付；D-LA-1 被否选项即 PyInstaller 打包，留本评估）；
  [D-N3](2026-11-11-post-m6-roadmap.md)（发布就绪=zip/wheel + `play` 脚本；PyPI 暂缓）；
  `docs/design/deployment-and-distribution.md`（分发形态现状）、`RELEASE.md` §2/§3/§5；
  风险 `R-L4`（密钥/数据卫生）与 `R-T6`（构建链供应链）的延伸评估。

## 1. 背景与红线

- **当前玩家分发形态（0.3.0 已交付）**：① Windows 双击 `启动游戏.bat` → `scripts/play.ps1 -Launcher`
  （WinForms 启动器 + 隐藏 uvicorn + 自动开浏览器 + 游戏内/关窗/空闲退出；**前置仍需要 `uv`**，首次运行
  `uv sync --frozen --no-dev` 需联网，显式提示「首次运行需几分钟」）；② 仓库 clone + `scripts/play.ps1|sh`
  （仓库 tracked 约 9.5MB，含 `web/dist`，运行期不需要 Node）；③ `uv build` wheel 0.5MB / sdist 0.9MB
  （含内嵌 `textgame/web_dist`，需宿主 Python 3.10–3.12 + `pip install`）。启动器不自动安装 uv（供应链红线）。
- **用户决策（2026-11-11）**：「暂不做安装包」——不要求本轮交付 exe/安装器；058 只保留评估位与复评触发条件。
- 本轮边界：**只写文档，不实现**——不新增构建依赖（PyInstaller/Nuitka/Inno Setup 等一律不入
  `pyproject.toml`/`uv.lock`/CI）、不提交打包脚本/CI 打包 job、不产出 exe/安装器、不改运行时代码与前端。
- 红线：零新增依赖（含构建期）；不把未验证的打包产物当发布物；分发物不含 `.env`/`data/`/凭据/日志；
  不自动下载执行外部脚本（D-LA-1 的 uv 纪律不变）；Windows 未签名 exe 不承诺「普通用户双击即玩」体验。

## 2. 决策

### D-N5-1 本轮不实现任何便携包/安装包，维持「仓库/zip + uv + 启动器 + wheel」分发

- 决策：
  1. **不实现**便携包（embedded Python/`python-build-standalone` zip）、PyInstaller/Nuitka exe、MSI/Inno Setup 安装器；
  2. **不新增构建依赖**、不新增 CI 打包 job、不产出/不上传 Windows 构建产物；`pyproject.toml`/`uv.lock`/CI 零变化；
  3. **保留评估位**：本 ADR 记录 4 类方案的成本/风险对比与复评触发条件，作为用户改口后**唯一**的评估起点；
  4. 玩家侧「免 uv」诉求继续由 057 启动器承担：uv 缺失给中文安装指引、首次 sync 显式提示、已有实例复用；
     文档（README/RELEASE/deployment 文档）如实写明前置条件，不承诺未实现能力。
- 理由：
  - **需求未验证**：用户明确「暂不做」；当前没有「玩家因缺 uv 放弃」的证据。exe 化解决的是「免 uv」，
    但引入签名/杀软/自动更新/构建依赖四类新运维，收益尚未被需求证明；
  - **成本量级明显**（与现有形态对比，估算口径见 §3）：PyInstaller onefile 产物约 150–300MB 量级、
    首次启动需解压到临时目录（冷启动慢数秒）、**无代码签名时 SmartScreen/杀软误报概率显著上升**；
    embedded-Python zip 约 100MB+ 量级且仍需处理启动器/数据目录/升级；MSI 额外引入卸载/注册表/安装权限；
  - **零新增依赖 + 离线可验收纪律**（第 4/19 章、D-N7）：PyInstaller 属构建期依赖，且 Windows 打包产物
    需要新的 CI runner/签名/杀软验证流程，超出单机开发仓库的验证能力；
  - **现有形态已覆盖目标用户**：开发者/技术玩家用仓库 + `play`；只有 Python 的用户用 wheel；
    普通玩家用 057 启动器（只需装 uv 一次）；发布形态的下一优先级应是「0.4.0 发布 checklist」而非打包。
- 被否选项：
  - **A. PyInstaller onefile/onedir（最直接）**：免 uv，但产物大、冷启动慢、未签名易被 SmartScreen/杀软拦截、
    升级需整包替换；新增构建依赖与 Windows 打包 CI；**本轮否**（用户暂不做 + 成本未证明）。
  - **B. 便携 zip = python-build-standalone/embedded Python + 依赖 wheels + 启动脚本**：比 onefile 可控
    （onedir 形态、无 PyInstaller bootloader 误报源），但体积仍 100MB+、需自管「Python 运行时 + 依赖」版本与升级、
    首次解压/启动仍需指引；**本轮否**（维护面大于收益）。
  - **C. MSI/Inno Setup 安装器**：普通 Windows 软件体验最好（开始菜单/卸载/升级），但除 A/B 的成本外还需
    签名证书、安装权限、卸载残留/数据目录策略；**本轮否**。
  - **D. 现在先加 CI 打包 job 但不发布**：无用户需求且占用 CI 时间/维护构建链，产出未经验证；**否**。
- 影响面：无代码/依赖/CI 改动；`docs/design/deployment-and-distribution.md` §5 状态表、`RELEASE.md` §5/§6
  的「058 便携包/安装包评估」行建议由 Lead 在集成时回写为「已评估：不实现（本 ADR）」；README 限制段保持现状。
- 回写落点：本 ADR + 进度文档 058；`docs/decisions/README.md` 索引由 lead 统一追加（本切片不改索引）；
  `docs/design/deployment-and-distribution.md` 与 `RELEASE.md` 的状态回写归属 Lead（不在本切片写入范围）。
- 验收断言（本切片实测/核对）：
  1. 工作区无新增打包依赖：`pyproject.toml`、`uv.lock`、`.github/workflows/ci.yml` 零 diff；
  2. 无 exe/安装器/打包脚本新增（`git status --short` 中本切片只含 ADR/进度文档）；
  3. 本 ADR 含 4 类方案对比、成本表、杀软/签名/更新/数据目录风险、复评触发条件与回滚路径；
  4. 用户 2026-11-11「暂不做安装包」的边界在原文档（§17.9.2 N5-058）与本 ADR 中一致、可追溯。

## 3. 方案对比与成本（评估口径）

| 维度 | 现状：仓库/zip + uv + 启动器 + wheel（选定） | A PyInstaller exe | B 便携 zip（embedded Python） | C MSI/Inno 安装器 |
| --- | --- | --- | --- | --- |
| 玩家前置 | 需 uv（仅 Windows 双击需一次安装） | 无（双击 exe） | 无（解压后双击 bat/exe） | 无（运行安装器） |
| 体积（量级估算） | wheel 0.5MB；仓库 tracked 9.5MB；`.venv`（含 dev）389MB，运行期依赖另计 | 150–300MB（onefile 解压后更大） | 100–200MB（Python 运行时 + 依赖） | 同 B + 安装器开销 |
| 首次启动 | `uv sync` 需联网数分钟（启动器已提示） | onefile 首次解压数秒–十数秒 | 解压后直接启动（秒级） | 安装 + 首次启动 |
| 杀软/SmartScreen | 低（uv 官方分发 + Python） | **高**（PyInstaller bootloader 常见误报；未签名必警告） | 中（内嵌 python.exe/未签名入口） | 中–高 |
| 代码签名 | 不需要 | 建议需要（证书成本/审批） | 建议需要 | 建议需要 |
| 自动更新 | 手动 git pull/重解压；启动器已有实例复用 | 需自建 updater/整包替换 | 需自建版本检查/覆盖解压 | 安装器升级链或自建 |
| 数据目录 | `data/`（仓库/工作目录，gitignore） | 需明确 `_MEIPASS` 只读 → 数据放 `%APPDATA%` 或 exe 旁 | 便携目录 `data/`（需可写） | `%APPDATA%`/ProgramData 策略 |
| 构建依赖/CI | 无（`uv build` 已入 RELEASE 流程） | 新增 PyInstaller + Windows 打包 CI/签名步骤 | 新增运行时获取/组装脚本 + CI | 新增 Inno Setup + 签名 + CI |
| 维护面 | 低 | 中–高（AV 误报矩阵/每次依赖升级重打包） | 中–高（Python 运行时升级/依赖同步） | 高 |
| 与零新增依赖纪律 | ✅ | ❌（构建期依赖） | ⚠️（工具/运行时获取） | ❌ |

**风险明细（若将来做，必须先验证）**：① 杀软误报矩阵（Windows Defender/SmartScreen + 至少 2 家第三方引擎；
未签名产物不得直接对外）；② 代码签名证书成本与私钥管理（不进仓库）；③ 自动更新来源鉴权（HTTPS + 哈希/签名，
禁止静默执行下载脚本）；④ 数据/凭据目录（`data/` 与 `llm.credentials.json` 不得随包分发、不得写只读安装目录）；
⑤ 构建链供应链（PyInstaller/内嵌运行时版本 pin + 许可证/漏洞审计，接第 19 章门禁）；⑥ 回归证据
（干净机首发/冷启动/升级/demo/console/`play -Check`/LauncherCheck 全跑）。

## 4. 已知问题归属

| 编号 | 摘要 | 归属 | 关闭条件 |
| --- | --- | --- | --- |
| — | 玩家首次启动仍需安装 uv + 联网 sync | N5 057（启动器 + README FAQ，已缓解） | 用户改口做便携包时由本 ADR 复评接管 |
| — | 无单文件下载渠道（exe/安装器） | N5 058（本 ADR，保留评估位） | 出现真实分发需求 + 签名/AV/CI 条件具备 → 新 ADR |
| R-T6 | 前端构建链供应链 | 若引入打包工具则新增打包链供应链面 | 依赖/版本 pin + 许可证/漏洞门禁 |
| — | deployment/RELEASE 文档状态行仍写「058 未排期」 | Lead 集成时统一回写 | 两文档标注「已评估：不实现（D-N5-1）」 |

## 5. 出口验收判据与到期承诺处置

- 058 出口：本 ADR 定案（结论=本轮不实现、不新增构建依赖）+ 进度文档 058 落盘 + 第 17 章 §17.9.2 N5-058
  「评估只写文档、不实现」履行；实测无依赖/CI/产物改动（验收断言见 §2 末）。
- **到期承诺处置**：§17.9.2 N5-058 行「（未排期）便携包/PyInstaller/单机安装包评估」——本 ADR 关闭该评估项；
  **不**关闭「用户将来要做」的可能性（见 §6 触发条件）。D-LA-1 被否选项②（打包 exe）的后续=本 ADR，仍维持被否。

## 6. 后移与复审（触发条件）

用户改口或出现以下任一信号时，**先立项新 ADR**（`supersedes` 本 ADR 或新增）再实现，不在本 ADR 上直接改结论：
1. 用户明确要求「不装 uv/Python，双击即玩」或要求单文件下载（GitHub Release asset / 下载站）；
2. 有可观测证据表明 uv 安装是玩家最大流失点（工单/反馈/试用数据）；
3. 有代码签名证书预算与发布流程（否则不对外分发未签名 exe）；
4. CI 具备 Windows runner + 杀软误报验证流程，且打包产物能进 RELEASE checklist 自动验证；
5. 发布渠道（PyPI/GitHub Release）策略调整，需要「免依赖安装包」作为主分发形态。

**回滚条件与路径**：本轮无实现，回滚 = 无动作；若将来实现，回滚 = 删除打包脚本/CI job/构建依赖、恢复
「仓库/zip + uv + 启动器 + wheel」为唯一分发形态（本 ADR 与 deployment 文档保留历史结论）。

## 7. 变更纪律

- 本 ADR 只增不改；推翻结论 = 新 ADR + `supersedes: D-N5-1` + 回写第 17 章 §17.9.2/§17.9.5、deployment 文档与 RELEASE；
- 实现前必须先完成 §3 的 6 项验证（AV 矩阵/签名/更新鉴权/数据目录/供应链/回归证据），缺一项不得进入发布；
- 任何「顺手」把打包脚本或构建依赖提交进仓库的改动，评审可依本 ADR + D-N7 拒绝。
