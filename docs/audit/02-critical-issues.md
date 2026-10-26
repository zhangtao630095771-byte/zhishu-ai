# 02 · 关键问题清单（取数正确性 / 安全 / 稳定性 / 工程质量）

> 严重程度定义：**P0** 已确认的严重安全/数据泄露/破坏性/核心阻断；**P1** 明显影响取数正确性、权限或核心链路稳定；**P2** 功能缺口、可靠性/维护问题；**P3** 一般优化。
> 结论：**本次审查未发现已确认的 P0 问题**（不强行凑数）。以下按 P1→P3 列出，均附源码证据与可执行验证方法。

---

## 一、P1 级问题（高优先级，建议优先修复）

### C-01　未登记关系的「一对多 JOIN」无笛卡尔放大拦截

| 字段 | 内容 |
|---|---|
| 问题编号 | C-01 |
| 问题描述 | JOIN 防放大校验只对**已在 `relationships` 表登记且 `pre_agg_required=True`** 的关系生效。真实数仓大量表未登记关系，模型直接 `JOIN` 一对多明细表时无任何拦截，聚合指标被静默放大。 |
| 严重程度 | P1 |
| 源码证据 | `sql_validator.py:240-261`（`_check_p2` 仅遍历 `ctx.p2_relationships` 且要求 `rel.get("pre_agg_required")`）；`semantic_layer.py:346-363`（关系仅来自已登记 `relationships` 表）；`config_store.py:627`（`pre_agg_required` 字段存在但依赖人工配置） |
| 触发条件 | 用户问及的业务表对存在 1:N 关系，但管理员尚未在知识库登记该关系或未勾选 `pre_agg_required` |
| 业务影响 | 订单量、金额等聚合指标被放大（如订单×费用明细），返回"看着有数、实际全错"的结果——最危险的静默算错 |
| 复现方式 | 用 `test_legacy_validation.py` 思路：构造一条 `JOIN` 未登记一对多表的 SQL，走 `sql_validator.validate_sql_ex`，观察 P2 不报 error |
| 修复建议 | ① 在 `_check_p2` 增加**基于表名/外键启发式的兜底检测**（如存在 `*_fee`/`*_detail`/`*_record` 明细表被直接 JOIN 且无 GROUP BY 预聚合子查询时告警）；② 推动关系登记覆盖率，未登记时降级为 warning 而非静默 |
| 验收标准 | 未登记一对多表直接 JOIN 时，校验器产出至少 warning 级提示；已登记 `pre_agg_required` 的仍 error 阻断 |
| 回归测试 | 在 `test_legacy_validation.py` / `test_gate.py` 新增"未登记一对多 JOIN 产生告警"用例 |

### C-02　「执行成功但业务结果错误」无标准答案对比机制

| 字段 | 内容 |
|---|---|
| 问题编号 | C-02 |
| 问题描述 | 结果正确性目前只靠启发式 `result_validator`（比率越界/全 NULL/行数/全 0）与"仅诊断不改 SQL"的 LLM 逻辑自审。没有"标准答案对比"（`result_accuracy` 评测维度未实现），无法确定性地发现口径算错。 |
| 严重程度 | P1 |
| 源码证据 | `result_validator.py:34-94`（仅 4 类启发式 warning）；`agent.py:257-271`（`_review_sql` 只诊断不改 SQL）；`evaluation/evaluators/checks.py` 中 `result_accuracy` 恒为 None（`evaluation/reports/baseline.json` 显示 `result_accuracy=None`） |
| 触发条件 | 模型生成"语法正确、执行成功、但口径错误"的 SQL（如该用完成时间却用创建时间、该用 status=6 却用 status=7） |
| 业务影响 | 用户拿到错误数字且无提示，业务决策基于错数 |
| 复现方式 | 查看 `evaluation/reports/baseline.json`：`result_accuracy=None`、`field_retrieval_recall=None`，证明两维度未实现 |
| 修复建议 | ① 短期：把 `golden` 数据集的 `expected_result` 补齐（当前 schema 文档标注均为"未提供"），让 `result_accuracy` 可计算；② 中期：对高频指标配置"合理区间/同比环比"护栏，越界即降级为"待人工复核" |
| 验收标准 | `result_accuracy` 指标有非 None 数值；至少 20 条用例具备 `expected_result` |
| 回归测试 | `evaluate.py --dataset golden` 报告中出现 result_accuracy 分数 |

### S-01　审计日志记录 SQL 明文 + request_id 缺失

| 字段 | 内容 |
|---|---|
| 问题编号 | S-01 |
| 问题描述 | 权限审计与取数记录落库的 SQL **未脱敏**，可能含手机号等字符串字面量；且中间件**未生成 request_id**，审计追踪链断裂。 |
| 严重程度 | P1 |
| 源码证据 | `server.py:835`（`_audit_permission` 传 `payload["sql"]` 明文）；`config_store.py:3365`（`final_sql` 落库）；`permission.py:613-618`（`_safe_sql_repr` 脱敏工具已存在但**未被 server 调用**）；`server.py:826`（引用 `request.state.request_id`，但中间件 `server.py:130-157` 从未设置该字段） |
| 触发条件 | 用户查询含手机号/身份证等字面量的 SQL（如"查询 138**** 的订单"），落库后审计表含明文 |
| 业务影响 | 敏感数据二次泄露面；审计日志无法按 request_id 串联定位 |
| 复现方式 | 提交一条含字符串字面量的取数，检查 `permission_audit_logs.final_sql` 是否含明文字面量 |
| 修复建议 | ① `_audit_permission` 改用 `permission._safe_sql_repr` 存储 SQL 结构（字面量替换为 `?`）；② 中间件生成 `request_id`（`uuid4`）并注入 `request.state` |
| 验收标准 | 审计表 `final_sql` 不再含单/双引号包裹的字面量；`permission_audit_logs.request_id` 非空且可串联 |
| 回归测试 | `test_permission.py` 增加"审计 SQL 脱敏"与"request_id 非空"断言 |

### S-02　登录无速率限制 + 密码强度过弱

| 字段 | 内容 |
|---|---|
| 问题编号 | S-02 |
| 问题描述 | 登录接口无失败次数计数与账户锁定；密码仅校验"≥6 位"，无复杂度要求。 |
| 严重程度 | P1 |
| 源码证据 | `server.py:427`（`api_login` 无重试计数/锁定）；`config_store.py:2310/2428/2498`（`create_user`/`update_user`/`change_own_password` 仅 `len < 6` 校验） |
| 触发条件 | 服务暴露到内网/公网（形态 A/B）后，攻击者可对弱密码账号暴力破解 |
| 业务影响 | 账号接管 → 越权取数/改知识库 |
| 复现方式 | 连续多次错误口令调用 `POST /api/auth/login`，观察无锁定、无退避 |
| 修复建议 | ① 登录失败计数 + 5 次锁定/指数退避；② 密码复杂度（长度≥8 + 至少两类字符）；③ 可选：登录验证码/IP 白名单 |
| 验收标准 | 连续 5 次错密后账号锁定（或触发退避）；弱密码被拒绝 |
| 回归测试 | `test_acl.py` / 新增登录安全测试断言锁定逻辑 |

### S-03　密钥文件与跳板机信息裸露（GitHub 入库前必须处理）

| 字段 | 内容 |
|---|---|
| 问题编号 | S-03 |
| 问题描述 | 项目根目录存在 `.env`（含真实 `DEEPSEEK_API_KEY`、`CK_PASSWORD`、`SSH_PASSWORD` 等）；`tunnel.sh` 硬编码**公网跳板机 IP**（`<跳板机IP>`）与 `SSH_USER`；`deploy.md` 含内网 CK 地址与账号。当前目录**非 git 仓库**，一旦误提交即泄露。 |
| 严重程度 | P1（入库阻断项） |
| 源码证据 | `tunnel.sh:6`（跳板机 IP）、`tunnel.sh:28-29`（从 .env 读 SSH_PASSWORD）、`tunnel.sh:81-103`（expect 明文密码交互）；`.env.example:26`（注释说明"真实密码只放 .env"）；`deploy.md:10/78`（内网 IP/账号） |
| 触发条件 | 将项目纳入 Git 且未排除 `.env`/`tunnel.sh` 中的敏感信息 |
| 业务影响 | 生产 ClickHouse 只读账号、LLM 密钥、跳板机 SSH 凭据泄露 |
| 复现方式 | `cat .env`、`cat tunnel.sh`（本报告不输出内容，由 owner 核对） |
| 修复建议 | 入库前：① `.env` 加入 `.gitignore`（绝不提交）；② `tunnel.sh` 的 IP/账号改为从 `.env` 读取（当前已读 .env，但 IP 仍硬编码）；③ 用 `.env.example` 占位符替代 `deploy.md` 中的真实 IP/账号；④ 若历史已提交，需轮换全部密钥 |
| 验收标准 | `git grep` 无真实 IP/密钥；`.env` 被 `.gitignore` 拦截 |
| 回归测试 | 入库前 `git status` 确认 `.env` 不被跟踪 |

---

## 二、P2 级问题（功能缺口 / 可靠性 / 维护）

### C-03　指标硬约束依赖人工元数据配置完备性

- **描述**：`required_predicates`/`forbidden_predicates` 硬约束（`sql_validator.py:155-234`）只能拦截**已声明**的指标。未声明这些约束的指标，模型仍可静默算错。当前 28 个指标中仅部分配置了约束（`_seed_metric_constraints.py` 为一次性补种）。
- **修复建议**：指标录入时强制要求填写 `required_predicates` 或标注"无硬约束"（显式化），并在知识缺口页提醒未配置约束的指标。
- **验收标准**：所有 enabled 指标均有 `required_predicates` 或显式"无约束"标记。

### C-04　启发式阈值样本有限

- **描述**：`SATELLITE_KEEP_THRESHOLD=0.60`、`NAMED_TABLE_MIN_CHARS=4` 等阈值基于有限样本拍定（`config.py:142/156` 已自注"样本有限"）。
- **修复建议**：随评测集扩充定期重校准（详见 `08-evaluation-plan.md`）；将阈值纳入评测报告对比项。
- **验收标准**：阈值调整有评测数据支撑记录。

### C-05　JOIN 基数建议与校验断层

- **描述**：`join_suggest.py` 的 AI cardinality 建议（`join_suggest.py:136-175`）不自动落库、不联动 `pre_agg_required`，需人工二次确认；确定性候选（同名字段/FK）不产出 cardinality。
- **修复建议**：AI 推断 1:N 时自动回填 `pre_agg_required` 草稿（待审核），减少人工步骤。
- **验收标准**：从 join_suggest 确认一条 1:N 关系后，`relationships.pre_agg_required` 被正确置位。

### R-01　数据库连接无 try/finally（连接泄漏）

- **描述**：`db_clickhouse.py:419-424`（`execute_sql`）、`db_sqlite.py:364-372`、`config_store.py` 全文件均为"手动 close 无 finally"，`client.query` 抛异常时连接不释放。
- **影响**：生产长跑 + 异常频率升高时连接累积。
- **修复建议**：统一改为 `with` / `try-finally` 上下文管理；`config_store` 抽一个 `_connect` 上下文管理器。
- **验收标准**：任一执行抛异常后连接数不增长。

### S-04　初始管理员密码打印 stdout + 默认口令残留

- **描述**：随机初始密码打印到标准输出一次（`config_store.py:2258-2260`）；硬编码公开默认口令 `admin123` 残留于源码（`config_store.py:2285` 迁移检测）。
- **修复建议**：打印改为"仅提示去日志/环境变量查看"；清理残留字符串。
- **验收标准**：进程日志无明文初始密码。

### E-01　`config_store.py` 上帝文件

- **描述**：3530 行承载 30 表 CRUD + 账号 + 权限 + 反馈 + 快照（见 01 报告）。
- **修复建议**：权限体系（约 2980-3530 行）独立为 `permission_store.py`；JSON 工具去重。
- **验收标准**：权限相关函数移至独立模块且测试通过。

### E-02　噪声表过滤规则三处重复

- **描述**：`knowledge.py:121`、`check_consistency.py:41`、`list_tables.py:44` 三份 `NOISE_TABLE_MARKERS` 逻辑重复，`knowledge.py:118` 注释要求"必须只有一份"却已违反。
- **修复建议**：统一收敛到 `knowledge.business_tables()`，其余两处改为调用。
- **验收标准**：全项目仅一处噪声表规则定义。

### E-03　账号改删的孤儿引用

- **描述**：`update_user`（`config_store.py:2404-2418`）改登录名只同步 `sessions`/`query_records`，不同步 `user_role_bindings`/`favorites`/`permission_policies`/`permission_audit_logs`；`delete_user`（`2453-2467`）只删 `users` 表。
- **影响**：改名/删号后权限错位、孤儿数据。
- **修复建议**：联动更新/清理所有含 username 的表，或外键级联 + 统一工具函数。
- **验收标准**：改名后各表 username 一致；删号后无孤儿记录。

### E-04　依赖未锁定 + 无 CI

- **描述**：`requirements.txt` 全裸包名无版本；无 `.github/`、无 CI，回归靠手动 `run_tests.py`。
- **修复建议**：`pip freeze` 锁定版本 + `requirements-dev.txt`；接入 CI 跑 `run_tests.py`（unit/config 两层）。
- **验收标准**：CI 绿钩子，依赖可复现安装。

### E-05　字段注释生成两条路径标准不一致

- **描述**：`suggest.py:579`（`generate_column_comment`）有"覆盖全部取值/防编造/超 8 值拒绝"校验；`ai_ingest.py` 的 column_comment（`_apply` 约 183-198 行）直接写库无同等校验。
- **修复建议**：统一走 `suggest.generate_column_comment`，`ai_ingest` 复用同一校验。
- **验收标准**：两条路径产出的字段注释均通过取值覆盖校验。

---

## 三、P3 级问题（一般优化）

1. **遗留脚本清理**：`check1.py`、`check2.py`、`_fix_record_type_join.py`、`_probe_driver_13.py`、`_seed_*`、空 `config.db` 归档或删除。
2. **日志 `print` 化**：全程 `print()` 无级别/trace_id，建议引入 `logging` + 结构化日志 + request_id。
3. **`admin.py` 无鉴权/审计**：命令行直接改库无留痕，建议加操作日志或仅限本地运维。
4. **`_user_row_to_dict` 硬编码列顺序**（`config_store.py:2202-2216`）：依赖 SELECT 列顺序，建议改 `dict(zip(cur.description, row))` 与 `_record_row_to_dict` 一致。
5. **`upsert_sensitive_resource` 的 `raw_table`/`raw_column` 形参失效**（`config_store.py:3164-3192`）：INSERT 时未使用，需修正或删除形参。

---

## 四、已确认做得好的关键设计（供保留）

1. **唯一执行出口** `_exec_sql`（`agent.py:114-132`）+ 拒答结构判定，从根上杜绝"边拒答边执行"。
2. **自愈后重新过完整闸门**（`agent.py:786-813`），杜绝"修语法时写未授权表"绕过。
3. **fail-closed 贯穿**：表解析失败拒绝（`guard.py:411-457`）、校验器异常阻断（`sql_validator.py:480-491`）、字段级权限解析失败拒绝（`permission.py:278-319`）。
4. **密码安全**：加盐哈希 + `hmac.compare_digest` 恒时比较 + 防账号枚举抹平（`config_store.py:2195/2474/2481`）。
5. **指标级权限**：停用指标"问题级拦截"（`rag.py:413-463` `match_disabled_metric`），防模型用字段硬算。
6. **字段级 AST 权限**：SELECT * 展开、表达式/过滤/分组分位置判定、未登记敏感字段名兜底 MASK（`permission.py:127-205`）。
7. **V2 异常 fail-closed 降级白名单**（`agent.py:875-941`），禁止"校验失败静默降级为不校验执行"。
