# 部署与分发评估（Deployment & Distribution）

> 归属：N4 技术/发布轨（051 docker-assessment）；决策依据 [D-N3](../decisions/2026-11-11-post-m6-roadmap.md)；
> 状态：**评估已定案（2026-11-11）**：默认 wheel / 仓库 + `play` 脚本分发，**Docker 镜像暂不提供**（给出最小
> Dockerfile 草案与复评条件）；PyPI 正式发布暂缓（由维护者拍板）。

## 0. 结论先行

1. 目标用户 = **单机单玩家**（本地跑一个进程、浏览器访问 `127.0.0.1`，或纯终端）。数据只有一个 SQLite 文件
   + 一个可选的 LLM 凭据 JSON，**没有多副本/多租户/外部中间件**。
2. 因此分发优先级：**① 仓库克隆 + `scripts/play` 一条命令（已含 `web/dist`，无需 Node）→ ② Python wheel
   （`pip install` 后同样带网页版）→ ③ Docker 镜像**。前两种已交付并通过发布冒烟（RELEASE.md §3）。
3. **Docker 暂不提供**：收益（环境隔离/一条命令跑容器）对「本来就是一条命令」的单机场景增益有限；成本是
   维护镜像、数据卷、凭据文件挂载、端口绑定安全（默认只能绑回环）。等出现「服务器部署/多人共享」需求再评估。
4. **PyPI 暂缓**：wheel 元数据已就绪（MIT、内嵌 `textgame/web_dist`、`play.ps1|sh`），但包名/README 英文版/
   版本承诺需维护者拍板；在有明确用户渠道前不发。

## 1. 三种分发形态对比

| 形态 | 上手成本 | 前提 | 数据/凭据 | 适用场景 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| **A. git clone + `play` 脚本** | 最低（1 条命令） | `uv`（自动装 Python 3.12）+ 网络（首次 sync） | `data/textgame.db`；`.env` 可选 | 本机试玩、开发、演示 | ✅ 主推荐（README/RELEASE 冒烟通过） |
| **B. Python wheel** | 中（需 Python 3.10+ 环境） | `pip install textgame-*.whl` | 同上；静态资源用包内 `textgame/web_dist` | 已装 Python 的玩家/离线机器 | ✅ 构建 + 干净 venv 冒烟通过 |
| **C. Docker 镜像** | 中（需 Docker） | 拉镜像 + `-v` 数据卷 + 端口 | 数据卷 + 可选挂载凭据文件 | 服务器/NAS/统一环境 | 🚫 本次不提供（评估见 §2） |

**共同注意（A/B/C 都适用）**：

- 服务默认只应绑定 `127.0.0.1`（`play` 脚本即如此）；**不要监听公网**——单机版没有账号体系，任何能访问端口的人
  都能操作存档；LLM Key 仅存本机。
- `.env` / `data/llm.credentials.json` **不进任何分发包**（gitignore + 打包排除）；`.env.example` 只给键名。
- 数据备份 = 复制 `data/textgame.db`（SQLite 单文件；停进程后复制最稳）。

## 2. Docker 评估（为什么不默认提供）

| 维度 | 评估 |
| --- | --- |
| 体积 | 基础镜像（python:3.12-slim）+ 依赖 ≈ 300–500MB；而 wheel 方案复用宿主 Python、不额外占空间 |
| 数据持久化 | 必须挂卷保存 `data/`（SQLite + 凭据）；挂载 Windows 路径时 SQLite 锁语义/权限有坑（WAL 与网络盘不兼容） |
| 凭据 | 玩家在网页「设置」页保存的 key 落在容器内 `data/`，重建容器即丢，需卷挂载；`-e TEXTGAME_LLM_API_KEY` 会把密钥暴露在 `docker inspect`/进程环境 |
| 静态资源 | `web/dist` 已随仓库/wheel 分发，无需容器内构建 Node |
| 端口安全 | 容器默认 `-p 8000:8000` 会绑 0.0.0.0；必须显式 `-p 127.0.0.1:8000:8000`，对普通用户是易错点 |
| 维护成本 | 需维护 Dockerfile/`.dockerignore`/多架构构建/镜像发布渠道；本轮无 CI Docker job，无法自动验证 |
| 本机验证 | **本开发机未安装 Docker**，无法实跑镜像（不提交未验证的 Dockerfile） |

**结论**：本轮**不提交** Dockerfile/镜像；文档给出「将来若要提供」的最小草案与复评条件，避免半成品配置误导用户。

### 2.1 若将来提供镜像（草案，未验证）

> 触发条件：出现「不装 uv/Python 也能跑」「服务器/NAS 长期运行」「多人共享同一实例」的真实需求，且 CI 有能力
> 跑 `docker build` + `/healthz` 冒烟。届时新增 ADR 并补 CI job，再提交以下草案（**当前仅存于本文档**）。

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY web/dist ./web/dist
COPY hatch_build.py ./
RUN pip install --no-cache-dir .
ENV TEXTGAME_DB_URL=sqlite:////data/textgame.db \
    TEXTGAME_LLM_CREDENTIALS_FILE=/data/llm.credentials.json \
    TEXTGAME_LLM_ENABLED=false
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
CMD ["python", "-m", "uvicorn", "textgame.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
# 构建与运行（将来）
docker build -t textgame:0.2.0 .
docker run --rm -p 127.0.0.1:8000:8000 -v textgame-data:/data textgame:0.2.0
# 首次打开 http://127.0.0.1:8000 ，在「设置」页填 DeepSeek API Key（不通过 -e 传密钥）
```

**复评时要补的验证**：镜像构建成功；`/healthz`、`/`（SPA）、`/api/v1/games` 冒烟；`data/` 卷持久化（重启后存档仍在）；
凭据文件权限；仅回环端口；`license/SBOM` 复检；镜像内不含 `.env`/`.git`/测试。

## 3. 发布产物与冒烟（已落地，见 RELEASE.md）

- `uv build` 产出 `dist/textgame-0.2.0-py3-none-any.whl` + sdist；wheel 内嵌 `textgame/web_dist`（`hatch_build.py`）；
- 干净 venv 安装 → `python -m textgame.console --check`（退出码 0）→ `python -m textgame.demo --check`（7 屏逐字）；
- 仓库 clone → `scripts/play.ps1 -Check`（`/healthz=200`、`/=200`）。

## 4. 安全与合规注记

- 不监听公网；容器/服务器场景必须在前面加认证代理（属于未来服务器化需求，不在本轮范围）。
- 数据卷里包含存档与（若玩家在网页保存）API Key——按敏感数据对待，不入库、不进镜像层、不随日志导出。
- 分发不含用户内容；用户导入的场景 JSON/世界书/人物卡属于玩家数据，只存本机 `data/` 或数据库。

## 5. 与轨道/切片的对应

| 事项 | 轨道切片 | 状态 |
| --- | --- | --- |
| 版本号/CHANGELOG/README 发布段 | N4 049 | ✅ 2026-11-11（0.2.0） |
| 英文 README 补全 | N4 050 | ✅ |
| Docker 评估（本文档） | N4 051 | ✅（结论：暂不提供 + 草案） |
| 构建产物冒烟 | N4 052 | ✅（RELEASE §3） |
| 发布 checklist / tag 流程 | N4 053 | ✅（RELEASE.md） |
| 镜像实现 + CI Docker job | 未排期（057–060 预留 / 用户拍板） | ⏳ |
| PyPI 正式发布 | 未排期（用户拍板） | ⏳ |
