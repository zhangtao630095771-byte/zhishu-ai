# 知数AI（Zhishu AI）

企业级 AI 取数 / Text-to-SQL 平台：用自然语言提问，系统检索元数据与业务口径 → 生成 SQL → 过安全/业务校验闸门 → 在 ClickHouse（生产）/ SQLite（演示）执行 → 结果校验 → 自然语言回答。

> ⚠️ **仓库安全须知**：本项目面向企业内部数据场景。请**务必使用 Private 仓库**；真实密钥只放 `.env`（已被 `.gitignore` 排除，模板见 `.env.example`）。

## 核心特性

- **确定性取数流水线**：查询理解 → 分层检索（P0 系统规则 / P1 指标口径 / P2 实体关系 / P3 Schema / P4 文档）→ SQL 生成 → 五维校验 → 唯一执行出口，全程 fail-closed。
- **多层安全护栏**：只读校验、表白名单（sqlglot AST 提取 + fail-closed）、字段级 ALLOW/MASK/DENY（含 SELECT * 展开、表达式/过滤/分组分位置判定）、行级权限 AST 改写、结果脱敏、审计留痕（SQL 字面量脱敏 + request_id 追踪）。
- **业务口径硬约束**：指标元数据声明 `required_predicates` / `forbidden_predicates`，AI 编不出口径错误；一对多 JOIN 强制预聚合（未登记关联给保守告警）。
- **枚举输出中文化**：命中数据字典的枚举字段，输出列自动用 CASE WHEN 转中文（过滤/分组仍用编码）。
- **时间口径感知**：节假日 / 相对时间（"中秋节前 7 天"）解析为明确公历区间。
- **完整评测门禁**：8 类数据集 + Release/Regression Gate + 线上反馈回流（`evaluation/`）。

## 项目结构

```
server.py            FastAPI 服务（鉴权中间件 + 60+ API）
agent.py             编排核心（V2 流水线 + Legacy 兜底 + 唯一执行出口）
semantic_layer.py    语义层（P0~P4 分层检索与冲突解决）
rag.py               检索引擎（关键词+向量混合、字典召回、点名表识别）
sql_validator.py     SQL 五维校验（P0 规则/P1 口径/P2 JOIN/P3 Schema/比率）
guard.py             安全护栏（清洗、只读、表级权限）
permission.py        字段级/行级权限引擎 + 脱敏 + 审计
config_store.py      配置库（30 张表：知识/账号/权限/反馈/审计）
db.py                数据源门面（db_clickhouse / db_sqlite 双实现）
evaluation/          评测框架与数据集
docs/                设计与审查文档（audit/ 与 risk-remediation/ 为审查与整改报告）
```

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # 或 requirements.lock.txt（锁定版本）
cp .env.example .env                              # 填入 DEEPSEEK_API_KEY 等（不入库）

# 演示模式（本地 SQLite，开箱即用）
.venv/bin/python main.py                          # CLI 跑 8 条演示问题

# Web 服务
./serve.sh                                        # http://127.0.0.1:8000
HOST=0.0.0.0 ./serve.sh -d                        # 供内网同事访问
```

生产部署（Docker + ClickHouse + nginx）见 [deploy.md](deploy.md)；开发机 ClickHouse 隧道见 `tunnel.sh`（地址均从 `.env` 读取）。

## 测试与评测

```bash
.venv/bin/python run_tests.py           # unit + config 层回归（约 2 分钟）
.venv/bin/python run_tests.py unit      # 纯逻辑层（CI 友好）
.venv/bin/python evaluate.py --help     # 评测 CLI（--gate 发布门禁 / --regression 回归门禁）
```

## 首次初始化

1. 启动后用初始管理员账号登录（首次强制改密）。
2. 「表与权限」登记可见表（生产建议白名单模式 `ACL_DEFAULT_VISIBLE=false`）。
3. 「知识管理」补指标口径 / 实体关系 / 数据字典（支持 AI 起草 + 人工审核）。
4. 改完知识后「重建索引」生效。

详细设计见 [DESIGN.md](DESIGN.md)；历史审查与整改记录见 `docs/audit/`、`docs/risk-remediation/`。
