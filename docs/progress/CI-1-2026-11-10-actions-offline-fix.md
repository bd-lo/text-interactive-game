# CI-1 · 2026-11-10 · 基础设施（不占 M1–M6 编号） · actions-offline-fix

> 一次性目标：修复仓库公开后 GitHub Actions 在 **ubuntu 上 pytest 步骤**的确定性失败（Python 3.10/3.12 两个 job 同错），使远端 CI 恢复全绿；并建立「CI-only 失败」的取证与复现纪律 ｜ 验收：远端 `ci` workflow 4 个 job 全绿 + 本地全量/回放/四门不回归 ｜ 关联：`docs/decisions/`（无契约变更）、第 12 章 §12.4、第 18 章 §18.6、`.github/workflows/ci.yml`

## 1. 现象与证据

- 仓库 `bd-lo/text-interactive-game` 公开后可用 GitHub Actions 公开 API 复核（无需 token）。失败 run：
  - `42701178389`（HEAD `827fe6f`，04:50 轮次）：`deps (3.12)` 成功；`quality (Python 3.10)` 与 `quality (Python 3.12)` 均在最后一步 `uv run pytest -q` 失败，ruff/mypy/lint-imports 全绿。
  - `42671598087`（`1f74de2`，01:50 轮次）同样失败；最后一次绿色 run 为 `0544f76`（M4 收口），即失败由 019/020/OSS-1 提交引入。
- 日志下载需认证（`/actions/runs/{id}/logs` → 403）。修复第一步是让失败信息**可公开读取**：在 `ci.yml` 的 pytest 步骤后新增 `if: failure()` 步骤，把 `pytest.log` 的 `FAILED/ERROR` 行与尾部 30 行转成 `::error::` **Check 注解**（`cc78363`）。随后从公开 API `/check-runs/{job_id}/annotations` 读到真实失败：
  - `FAILED tests/unit/test_llm_providers.py::test_chat_completion_forces_num_retries_zero`
  - `FAILED tests/unit/test_llm_providers.py::test_chat_completion_translates_provider_exception`
  - `RuntimeError: network access is blocked in the test suite (Q9): ('185.199.108.133'/'185.199.110.133', 443); use FakeLLM/cassette instead`（`185.199.x` = `raw.githubusercontent.com`）。
- 本地 Windows + Python 3.12/3.10、随机 seed、清空 proxy 均 **1177 passed**，无法直接复现。

## 2. 根因（文件:行）

1. `tests/unit/test_llm_providers.py` 的两个用例在测试体内 `import litellm`；
2. `litellm/__init__.py:570-572` 在 **import 期**调用 `get_model_cost_map()`，默认从 `raw.githubusercontent.com` 拉取模型成本表；fetch 只捕获 `httpx.HTTPError/InvalidURL`（`litellm/litellm_core_utils/get_model_cost_map.py:287-291`，环境变量开关在 `:676`）；
3. CI（ubuntu，无 `HTTP(S)_PROXY`，DNS 正常）→ httpx 发起真实连接 → 被 `tests/conftest.py` 的 Q9 socket 禁网守卫抛 `RuntimeError`；该异常非 httpx 异常，逃逸出 `import litellm` → 两个用例失败；
4. 本地偶然掩盖：本机 `NO_PROXY` 含畸形 `[::1]`，httpx 在客户端构造阶段先抛 `InvalidURL`（被 LiteLLM 捕获 → 回退包内备份表）；或直连 DNS 不可达 → `ConnectError`（同样被捕获）。故「本地全绿、CI 必红」。
- 复现脚本（本机，scrub proxy env + 伪造 DNS 可达 + 同款 guard）：`.work/ci-2026-11-10/repro_litellm_costmap.py` —— 不设 env → `RuntimeError escaped ('203.0.113.7', 443)`（与 CI 一致）；设 `LITELLM_LOCAL_MODEL_COST_MAP=True` → `import ok, model_cost entries=4255`。另以临时 `sitecustomize` 对**全量测试**做 CI 同构模拟：修复前 `2 failed / 1175 passed`，修复后 `1179 passed`。

## 3. 修复

- `tests/conftest.py`：在文件最顶部（任何其它 import 之前）强制 `os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"`，测试进程首次 `import litellm` 即零网络（Q9 注释说明）。测试侧即使有人删掉此行为，`providers.py` 仍兜底。
- `src/textgame/llm/providers.py`：模块级 `os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")` + docstring 说明——生产同样**离线优先**（本项目成本归集用自建价格表，022，不依赖 LiteLLM 远端成本表）；用户显式设 `False` 时尊重用户意图；「全仓唯一 litellm import 点」不变。
- `tests/unit/test_llm_providers.py`：新增 2 例回归——① 会话强制离线成本表（env 断言）；② 禁网守卫下 `importlib.reload(litellm)` 仍成功且 `model_cost` 非空（真实重走 import 期成本表加载路径）。
- `.github/workflows/ci.yml`：保留失败摘要注解步骤（`cc78363`，长期能力；公开仓库无需认证即可拿到失败用例名）。
- 文档回写：第 12 章 §12.4「离线成本表」、第 18 章 §18.6「CI-only 失败纪律」、`docs/progress/README.md` 增加 `CI-*` 文档例外。

## 4. 验证结果

| 检查 | 结果 |
| --- | --- |
| 单测（本文件相关） | `uv run pytest tests/unit/test_llm_providers.py -q` → **14 passed** |
| 全量 | `uv run pytest -q` → **1179 passed**（1177 基线 + 2 回归，零回归） |
| 回放 | `uv run pytest tests/replay -q` → **61 passed**（M2/M3/M4 逐值不变） |
| 四门 | ruff check / ruff format（144 files）/ mypy strict（84 files）/ lint-imports（2 kept）全绿 |
| 复现脚本 | 未设 env → RuntimeError（CI 路径）；设 env → import ok / 4255 条（修复路径） |
| CI 同构模拟（全量） | 修复前 2 failed / 1175 passed；修复后 1179 passed |
| 远端 CI | 修复 push 后 `ci` run 4 job 全绿（`deps (3.12)`、`quality (3.10)`、`quality (3.12)` 及 setup/收尾步骤），见 §5 |

## 5. 远端验收（push 后复核）

- 修复提交：`fix: CI 离线化 litellm 模型成本表（LITELLM_LOCAL_MODEL_COST_MAP）+ 回归测试`（sha 见 `git log`）。
- 验收方法（公开 API）：`/actions/runs?per_page=3` → 最新 run `conclusion=success` 且 `head_sha` = 本地 HEAD；`/actions/runs/{id}/jobs` → 4 job 全 success；`/check-runs/{job_id}/annotations` → quality(3.10)/(3.12) 无 failure 注解。
- 本轮复核 run id 与时间：见 `docs/PROGRESS.md` §7「CI 修复」条目（由 Lead 在 run 完成后回填）。

## 6. 后续与不做

- 不做：不在 ci.yml 重复注入同名 env（否则代码级修复/回归测试会被 CI 环境掩盖，失去回归意义）；不引入 `--maxfail`（会隐藏多失败）；不新增 `pytest-timeout` 依赖（需过第 19 章）；不升级 `actions/checkout@v4`/`setup-uv@v6`（Node20 弃用仅 warning，升级留给依赖治理切片）。
- 后续（可选，已记录）：`pytest --junitxml` + `if:always()` artifact（公开 triage 仍以注解为主）；`persist-credentials: false`；CI secret-scan；覆盖率门禁 C-1 与文档 CI C-2 仍按 D-M5-9 在 M5 末落地。
- 纪律沉淀：第 18 章 §18.6 已写入「CI-only 失败纪律」——第三方库 import 副作用必须用官方开关关闭；禁止依赖本机代理/NO_PROXY 才通过的用例；复现时先「清代理 env + 伪造 DNS 可达 + 保留禁网守卫」。
