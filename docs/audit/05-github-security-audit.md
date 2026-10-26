# 05 · GitHub 入库安全审查

> 本阶段仅盘点与提出建议，**不执行**任何 git init/add/commit/push 操作（任务书禁止）。
> 当前状态：项目目录**未初始化 Git**（无 `.git`），**无 `.gitignore`**，无历史提交风险。

---

## 一、现状盘点

| 检查项 | 结果 |
|---|---|
| 是否已初始化 Git | ❌ 否（无 `.git` 目录） |
| 现有分支/未提交修改/历史版本 | 无（非 git 仓库） |
| 敏感信息是否已进历史版本 | 无（从未提交），但需防**首次误提交** |
| 有无 `.gitignore` | ❌ 无（需新建） |
| 有无 `.env.example` | ✅ 有（`/Users/zhangtao/Downloads/autosql/zhishu-ai/.env.example`） |
| 有无 README | ⚠️ 无独立 README（有 `DESIGN.md`、`deploy.md` 可充当） |
| 有无依赖锁定 | ❌ 无（`requirements.txt` 裸版本） |

---

## 二、三个文件清单

### A. 建议入库文件（源码与文档）

| 文件/目录 | 原因 |
|---|---|
| 所有 `*.py`（除遗留脚本，见 C） | 核心业务源码 |
| `requirements.txt`、`Dockerfile`、`.dockerignore`、`.env.example` | 依赖/构建/模板 |
| `serve.sh` | 启动脚本（含个人无关逻辑） |
| `DESIGN.md`、`deploy.md`、`docs/*.md` | 设计/部署/文档（**deploy.md 需先脱敏**，见 B） |
| `frontend/`（index.html、logo） | 前端源码 |
| `ck_demo_setup.sql` | 演示库授权脚本（需确认不含真实账号） |
| `festivals.json`、`metrics.json` | 静态配置数据 |
| `evaluation/`、`eval/` | 评测框架 + 数据集 |
| `test_*.py`、`run_tests.py` | 测试 |
| `.workbuddy/memory/` | **视仓库性质**：公司内网私有仓 ✅ 可入（项目开发约定与记忆，利于协作）；**公共 GitHub ❌ 不入**（含公司业务口径、产品决策、表名，见 B） |

### B. 需脱敏 / 人工确认 / 单独处理

| 文件 | 风险 | 处理 |
|---|---|---|
| `deploy.md` | 含内网 CK 地址 `<内网CK_HOST>`、账号 `operation`、跳板机信息 | 用占位符 `<CK_HOST>`/`<CK_USER>` 替换后入库 |
| `tunnel.sh` | 硬编码公网跳板机 IP `<跳板机IP>` | IP 改为从 `.env` 读，或脱敏为占位符 |
| `com.zhishuai.server.plist` | 含个人绝对路径 `/Users/zhangtao/...` | 改相对路径/占位符，或移出仓库到 `deploy/` 示例 |
| `.workbuddy/memory/` | 含公司业务口径、DataEase 评估、产品构想、表名 | 私有仓可入；公共仓排除 |
| `产品文档_知数AI.md/.docx`、`架构升级方案_知数AI.md`、`日报_*.md` | 公司内部产品资料 | 人工确认是否公开 |
| `zhishu-ai-code-bundle/`、`dist/*.zip`、`zhishu-ai-code-bundle.zip/.md` | 历史打包副本，含旧源码 | 不入库（可由 git 历史替代），建议归档删除 |

### C. 必须排除的文件（`.gitignore`）

| 文件/模式 | 原因 |
|---|---|
| `.env` | **真实密钥**（`DEEPSEEK_API_KEY`/`CK_PASSWORD`/`SSH_PASSWORD`） |
| `*.db`、`*.db-*`、`*.db-journal/wal/shm` | 知识库+账号（含密码哈希）+ 演示数据 + 备份 |
| `.venv/`、`__pycache__/`、`*.pyc`、`*.pyo` | 虚拟环境与缓存 |
| `models/` | embedding 模型缓存（约 91MB，可由 requirements 重下） |
| `.idea/`、`.DS_Store`、`.vscode/` | IDE/系统文件 |
| `*.log` | 日志 |
| `dist/`、`zhishu-ai-code-bundle*` | 打包产物 |
| `agent_config.db.bak-*` | 配置库备份（含账号哈希） |
| 遗留脚本 `check1.py`、`check2.py`、`_*.py` | 一次性脚本（可先归档至 `archive/` 或直接排除） |

---

## 三、重点文件逐项核查（任务书要求）

- **`*.db`/`*.sqlite`/数据库备份**：`agent_config.db`（含 2 账号、知识、反馈）+ 3 个 `.bak-*` + `demo_shop.db` + 空 `config.db` → **全部排除**。迁移靠 `deploy.md` 的 `sqlite3 .backup` 流程，不入库。
- **`.env`/配置/部署文档**：`.env` 排除；`.env.example` 入库；`deploy.md` 脱敏后入库。
- **日志/缓存/上传文件**：无用户上传目录；`*.log` 排除。
- **`.workbuddy/memory/`**：经检查，内容为**项目开发约定 + 业务口径 + 产品决策**（文案约定、DataEase 评估、会话式分析构想、表名与指标名），**不含密钥**。判定：**私有公司仓库可入、公共仓库不可入**。
- **README/DESIGN/docs**：`DESIGN.md`、`docs/` 建议入库（`docs/` 中历史整改报告含表名/口径，私有仓可入）。
- **依赖清单/Dockerfile/启动脚本/测试数据**：`requirements.txt`（裸版本，需锁定）、`Dockerfile`、`serve.sh`、`ck_demo_setup.sql`（需确认无真实账号）入库。

---

## 四、安全入库操作方案（本次不执行）

> 以下为方案，供 owner 在确认后手动或授权执行。

1. **准备 `.gitignore`**：按上面 C 清单 + B 清单写入（`.env`、`*.db*`、`.venv/`、`models/`、`__pycache__/`、`.idea/`、`dist/`、`*code-bundle*`、`*.log`、`.workbuddy/memory/`[公共仓]）。
2. **脱敏**：`deploy.md`、`tunnel.sh`、`com.zhishuai.server.plist` 按 B 清单处理。
3. **锁定依赖**：`pip freeze > requirements.lock.txt`（或 `==` 固定版本）。
4. **补 README**：从 `DESIGN.md` 提炼项目简介、启动方式、架构图、安全须知。
5. **初始化**：`git init` → `git add`（严格按 .gitignore）→ **提交前审查**：`git status` + `git diff --cached` 人工确认无 `.env`/真实 IP/密钥。
6. **密钥泄露自查**：`git grep -nE 'sk-[A-Za-z0-9]|124\.221\.151\.187|172\.17\.0\.120|SSH_PASSWORD'` 应无命中。
7. **关联远程**：公司私有 Git（工蜂/GitLab）优先；若必须 GitHub，用 **Private 仓库** + 移除 `.workbuddy/memory/` 与产品文档。
8. **首次提交**：单次原子提交，附清晰 commit message。
9. **推送**：`git push -u origin main`。
10. **回滚策略**：若误提交密钥 → ① 立即轮换所有暴露的密钥（DeepSeek key、CK 密码、SSH 密码）；② 用 `git filter-repo` 或删除仓库重建（因无历史，重建成本最低）；③ 不要仅靠"删除提交"——密钥一旦 push 即视为泄露。

---

## 五、许可证与合规提示

- **本项目无第三方代码内嵌**（依赖均为 pip 包，不入库），无许可证冲突。
- **embedding 模型 `BAAI/bge-small-zh-v1.5`**：MIT 许可（models/ 不入库，运行时按需下载），无合规风险。
- **公司内部数据**：表名、指标口径、业务文档属公司资产，**公共仓库必须排除**（B 清单）。
- **SQLite `agent_config.db` 含账号密码哈希**：虽为加盐哈希，仍按敏感数据对待，不入库、备份控权限。

---

## 六、入库前必须处理的 TOP 风险（一句话）

1. `.env` 绝不提交（P0 级阻断项）。
2. `tunnel.sh` 跳板机 IP + `deploy.md` 内网地址脱敏。
3. `agent_config.db` + 备份排除。
4. 确认仓库为**私有**，或移除 `.workbuddy/memory/` 与产品文档。
5. 首次提交前 `git diff --cached` 人工审查。
