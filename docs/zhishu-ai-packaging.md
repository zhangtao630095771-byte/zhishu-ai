# 知数AI · 系统架构、代码整合与打包部署指南

> 适用范围：公司级 AI 取数系统「知数AI」。本文档用于把项目当前状态一次性交代清楚，可直接作为资料库条目。
> 最后整理：2026-10-19。代码形态：单仓库、单进程、前后端一体。

---

## 一、项目定位

知数AI 是一个**公司级 AI 取数系统**：用自然语言问业务问题，系统自动生成 SQL 并在 ClickHouse 上执行，把结果返回给业务同学。它把"取数"从写 SQL 变成"说话"，同时配套一套在线知识治理（指标口径、表/字段注释、数据字典、权限）让结果口径可控、可审计。

核心能力：
- **自然语言取数**：检索 → 生成 SQL → 安全护栏 → 执行 → 自愈重试，流式返回处理过程。
- **在线知识治理**：Prompt 规则、指标知识（RAG 核心）、表/字段业务注释、数据字典、实体与 JOIN 规则，全部在线维护并一键重建索引。
- **数据权限**：表级白名单 + 指标级停用拦截 + 数据库只读账号，三道防线。
- **账号管理**：账号增删改查（含**改登录名、重设密码、启停、角色**），RBAC（管理员 / 普通用户）。
- **取数历史 + 审核 + 反馈闭环**：每条取数自动留痕，支持提交管理员审核。

---

## 二、代码整合形态（最关键的三条结论）

1. **单仓库、单进程、前后端一体**
   - 后端 `server.py`（FastAPI）既是 API 服务，也通过 `FileResponse` 直接把 `frontend/index.html` 返回给浏览器。
   - 前端是**纯原生 HTML/JS**，**没有 Vite/Webpack、没有构建步骤**。
     （2026-10-29 前是单个 7118 行 HTML；现已拆为 `index.html` 骨架 + `static/{css,js}`，
     由 `server.py` 的 `/static` 挂载提供 —— 仍然零构建，仍是同一进程。）
   - 不存在独立前端服务器、不存在微服务拆分。

2. **核心逻辑与数据源解耦**
   - `db.py` 是数据源门面（Facade），按 `DB_TYPE` 分发到 `db_sqlite.py`（开发/演示）或 `db_clickhouse.py`（生产）。
   - **换数据源只改 `.env` 一行，代码零改动**。

3. **配置与代码分离 + 鉴权后置到服务端**
   - 所有可变项（模型名、阈值、方言、CK 连接、监听地址）都在 `config.py` / `.env`，不写死在代码。
   - 鉴权是 token 会话制（`sessions` 表）+ RBAC，由**中间件统一闸门**；前端只持有随机 token，身份每次回服务端校验，普通用户改 JS 变不成管理员。

---

## 三、系统架构（请求链路）

```
浏览器 (frontend/index.html + static/css+js，纯静态、零构建)
   │  HTTP / SSE（流式）
   ▼
server.py  ── FastAPI：路由 / 鉴权中间件 / 参数校验 / 直接提供静态页
   │
   ├─ agent.py         编排：检索 → 生成 → 清洗 → 护栏 → 执行 → 自愈
   ├─ rag.py           混合检索（关键词长词优先 + 向量语义）
   ├─ guard.py         SQL 清洗 + 只读护栏（用 SQL 解析器精确提取表名，比对白名单）
   ├─ prompt.py        组装 System Prompt（含业务规则与方言）
   ├─ llm.py           大模型调用（OpenAI 兼容接口，对接 DeepSeek）
   ├─ knowledge.py     业务知识访问入口（表清单等）
   ├─ config_store.py  配置/知识/账号/会话存储（底层：agent_config.db，SQLite）
   └─ db.py ──┬─ db_sqlite.py      开发/演示，本地文件库
              └─ db_clickhouse.py 生产，只读账号 + 表级授权
                       │
                  ClickHouse 数仓（业务数据，强制只读）
```

---

## 四、目录与模块职责（精简版）

| 文件 | 职责 |
|---|---|
| `server.py` | Web 入口：HTTP API + 鉴权中间件 + 静态页；**生产用这个** |
| `main.py` | CLI / 交互式取数入口（早期形态，单机调试用） |
| `agent.py` | 取数编排核心（检索→生成→校验→执行→自愈） |
| `rag.py` | 混合检索（关键词长词优先 + 向量语义），构建检索索引 |
| `guard.py` | SQL 清洗 + 只读护栏 + 表名精确提取与权限校验 |
| `prompt.py` | System Prompt 组装 |
| `llm.py` | 大模型调用封装 |
| `knowledge.py` | 知识访问入口（可见表清单等） |
| `config_store.py` | 配置库读写：prompts / metrics / 表注释 / 字典 / 实体 / 账号 / 会话 |
| `config.py` | 所有可变配置（读 `.env`） |
| `db.py` | 数据源门面，按 `DB_TYPE` 分发 |
| `db_sqlite.py` / `db_clickhouse.py` | 两套数据源实现，接口完全一致 |
| `admin.py` | 配置管理命令行工具（"在线配置"的命令行雏形） |
| `exporter.py` / `ai_ingest.py` / `suggest.py` | 知识导出 / AI 录入 / AI 自动起草注释 |
| `evaluate.py` / `evaluation/` | 评测集与评测运行 |
| `ssh_tunnel.py` / `tunnel.sh` | ClickHouse SSH 隧道（仅开发机用） |
| `frontend/index.html` | 前端骨架页（零构建，直接由 server 提供，约 1330 行） |
| `frontend/static/` | 前端静态资源：`app.css` / `experience.css` + `js/*.js`（12 个业务域模块），由 `/static` 挂载提供 |
| `deploy.md` | 详细上线部署指南（本文件的实操补充） |
| `Dockerfile` / `.dockerignore` | 生产镜像构建 |
| `requirements.txt` / `.env.example` | 依赖清单 / 环境变量模板 |

---

## 五、配置与环境变量

写在 `.env`（不入库）中，由 `config.py` 启动时读取；`.env.example` 是可分享模板。

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | 大模型密钥（必填） |
| `DB_TYPE` | `sqlite`（开发） / `clickhouse`（生产） |
| `CK_HOST` / `CK_PORT` / `CK_USER` / `CK_PASSWORD` / `CK_DATABASE` | ClickHouse 连接；`CK_USER` **必须是只读账号** |
| `CK_MAX_EXECUTION_SEC` / `CK_MAX_RESULT_ROWS` | 大查询保护（超时 + 行数上限） |
| `EMBED_CACHE_DIR` | 本地 embedding 模型目录，默认 `models/` |
| `EMBED_LOCAL_ONLY` | `1`=只用本地缓存不联网（内网推荐） |
| `CONFIG_DB` | 配置库位置，默认当前目录 `agent_config.db`；**容器部署指到挂载卷** |
| `ACL_DEFAULT_VISIBLE` | `false`=白名单模式（真实库建议）；`true`=黑名单模式 |
| `HOST`（serve.sh） | 监听地址，默认 `127.0.0.1`；对外需 `0.0.0.0` |

---

## 六、依赖（requirements.txt）

`openai`、`fastembed`（中文 Embedding，已缓存在项目内 `models/`，约 91M）、`sqlglot`、`fastapi`、`uvicorn[standard]`、`python-multipart`、`clickhouse-connect`、`python-dotenv`。

安装：
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

---

## 七、本地运行（开发 / 试用）

- **前台**：`./serve.sh`
- **后台**：`./serve.sh -d`
- 会自动拉 ClickHouse SSH 隧道（`tunnel.sh`）；无隧道时只起 Web，页面可能取不到数。
- **让同内网同事访问**：`HOST=0.0.0.0 ./serve.sh -d`
- **macOS 常驻**：`com.zhishuai.server.plist`（launchd），仅供开发机顺手常驻，**非生产方案**。

---

## 八、Docker 打包（生产镜像）

**Dockerfile 思路**：
1. 基础镜像 `python:3.13-slim`。
2. 先 `COPY requirements.txt` 装依赖（利用 Docker 层缓存，改代码不必重装）。
3. 再 `COPY . .` 拷代码与 `models/`（embedding 模型）。
4. `EXPOSE 8000`；`HEALTHCHECK` 打**公开**接口 `/api/ping`（探活不能打需要登录的接口，否则容器一直 unhealthy 被重启）。
5. `CMD`：**单 worker** 起 `uvicorn server:app --host 0.0.0.0 --port 8000`。

**`.dockerignore`**：排除 `.venv`、`__pycache__`、`.env`（密钥）、`*.db`（运行数据）、日志、临时脚本；**刻意不排除 `models/`**（随镜像内网零联网启动）。

构建与启动：
```bash
docker build -t zhishu-ai:latest .
docker run -d --name zhishu-ai -p 8000:8000 --restart unless-stopped \
  -e DB_TYPE=clickhouse \
  -e CK_HOST=<内网CK_HOST> -e CK_PORT=8123 \
  -e CK_USER=<只读账号名> -e CK_PASSWORD='只读账号密码' \
  -e CK_DATABASE=ctc_data_warehouse \
  -e DEEPSEEK_API_KEY='sk-...' \
  -e ACL_DEFAULT_VISIBLE=false \
  -e EMBED_LOCAL_ONLY=1 \
  -e CONFIG_DB=/app/data/agent_config.db \
  -v zhishu-data:/app/data \
  zhishu-ai:latest
```

---

## 九、部署两个"生死点"（漏了必出事故）

1. **数据持久化卷**：`CONFIG_DB` 必须挂到 `/app/data`（即 `-v zhishu-data:/app/data`），否则容器重建后**知识库 + 账号全部丢失**。
2. **知识库迁移**：镜像里只有"出厂默认知识"（代码里 `DEFAULT_PROMPTS` / `DEFAULT_METRICS` 等）。你实打实积累的指标口径、字段注释、表权限、账号都在**本地 `agent_config.db`**，不会进镜像。上线必须迁移：
   ```bash
   # 开发机：先停服务，用 sqlite 在线备份导出
   ./serve.sh stop
   sqlite3 agent_config.db ".backup '/tmp/agent_config.db'"
   scp /tmp/agent_config.db user@server:/tmp/
   # 服务器：塞进卷
   docker cp /tmp/agent_config.db zhishu-ai:/app/data/agent_config.db
   docker restart zhishu-ai
   ```

---

## 十、反向代理 / HTTPS

nginx 反代，`proxy_read_timeout` 给到 **300s**（复杂取数要 40~120s，超时即 504）。建议 HTTPS；至少保证内网 / VPN，不要把 8000 暴露公网（token 存在浏览器 localStorage，纯 HTTP 同网段可被嗅探）。

---

## 十一、安全与备份

- **上线第一天**：设置 `ADMIN_INITIAL_PASSWORD`（未设置则看首次启动日志里的随机密码），首次登录会强制改密；给同事逐个建普通用户，不要共用 admin；`CK_USER` 必须是只读账号；密钥走环境变量，绝不进镜像/仓库。
- **备份**：每天 `sqlite3 .backup` 配置库到 NAS / 对象存储（`agent_config.db` 是全系统最值钱的东西）。
- **扩展约束**：单进程 + 本地 SQLite + 进程内模型缓存 → **禁用 `--workers N`**（多 worker 会重复加载模型、内存翻倍）；扩容靠"多实例 + nginx 轮询"。

---

## 十二、常见故障速查

| 现象 | 原因 / 解决 |
|---|---|
| 同事打不开页面 | 服务绑的是 `127.0.0.1` 不是 `0.0.0.0`；或防火墙/安全组未放行；或不在同一内网 |
| 容器起来但取不到数 | 容器里 `CK_HOST` 连不通，或只读账号对目标表无 SELECT 权限 |
| 容器重建后知识库/账号全没 | `CONFIG_DB` 没指到挂载卷（数据写进可写层） |
| 容器一直 unhealthy 被重启 | 健康检查打了需登录的接口（应打 `/api/ping`） |
| 用户"一直转圈"或 504 | 反代读超时太短，给到 300s |
| 内网下不了 embedding 模型 | 有网机器先跑一次下好 `models/` 再打镜像；或构建时 `HF_ENDPOINT=https://hf-mirror.com` |
| 要支持更多人 | 起多个容器实例（共享挂载卷）+ nginx 轮询，**不要加 `--workers`** |

---

## 十三、一句话总结

知数AI = 一个 FastAPI 后端（取数 Agent + 在线知识治理 + 账号/权限，全在单进程内）+ 一个零构建的前端单页 + 一份 SQLite 配置库（知识+账号）+ 可选 ClickHouse 数据源；用 Docker 单镜像打包，靠挂载卷保住知识库，靠"多实例+nginx"水平扩展。
