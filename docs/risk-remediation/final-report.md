# 知数AI核心风险专项整改 · 最终报告（final-report.md）

> 整改时间：2026-10-26 ｜ 依据：《知数AI核心风险专项整改任务书（WB执行版）》
> 原则：先检查、后验证、再修改、最后回归；无证据不认定缺陷；不重复造已有功能。
> 基线文档：`docs/risk-remediation/baseline.md` ｜ 前置审查：`docs/audit/`（2026-10-23）

---

## 1. 执行摘要

本轮共**确认并修复 3 项缺陷、补齐 1 项评测能力缺口**，验证了权限闸门完整性（未发现绕过），完成凭据与敏感文件扫描。全部修改均遵守"先写失败测试复现 → 最小修改 → 全量回归"流程：

| 编号 | 缺陷 | 修复 | 状态 |
|---|---|---|---|
| C-01 | 未登记一对多 JOIN 无拦截 | `_check_p2` 新增启发式告警（warning 不阻断） | ✅ 已修复并通过验证 |
| S-01a | 审计存 SQL 明文 | 复用 `permission.safe_sql_repr` 脱敏 | ✅ 已修复并通过验证 |
| S-01b | request_id 恒空 | 中间件生成并传播 request_id | ✅ 已修复并通过验证 |
| C-02/C-04 | result_accuracy 恒 None + 弱比较 | 判分器结构化增强 + 新建 26 条 demo 结果评测集 | ✅ 已修复并通过验证 |
| C-02（真实业务样本） | 真实业务 Golden 样本需业务方提供 | — | ⛔ 修复未完成（阻塞，见 §10） |
| S-02 | 登录限速/密码强度 | — | ⏸ 按任务书标注"先不做" |
| 任务6 | 权限闸门绕过 | 未发现，无需修改 | ✅ 已验证 |

## 2. 修改前基线

- 测试：unit 10 项 + config 2 项**全部通过**（2026-10-26 10:00 实测，含 test_gate 122/122、test_acl 84/84）；既有失败 = 0。
- `result_accuracy=None` 根因：golden 35 条中 `expected_result` 覆盖 0 条（逻辑已存在，样本缺失）；且 golden 基于真实数仓表，本地无法核算。
- 源码自 2026-10-23 审查后无变化，审查证据行号全部有效。

## 3. 每项问题的原始证据及状态

| 编号 | 原始证据 | 证据等级（改前） | 改后状态 |
|---|---|---|---|
| C-01 | `sql_validator.py:240-261` 仅遍历已登记且 `pre_agg_required=True` 的关系；实测未登记直接 JOIN 明细表返回空 issues | 静态审查发现 → 已运行复现 | 已修复并通过验证 |
| S-01a | `server.py:835` `final_sql=payload["sql"]` 明文；`permission.py:613 _safe_sql_repr` 已存在未用 | 已确认缺陷 | 已修复并通过验证 |
| S-01b | `server.py:826` 读 `request.state.request_id`，中间件（130-157）从未设置 | 已确认缺陷 | 已修复并通过验证 |
| C-02 | `grep -c expected_result golden_v1.jsonl` = 0；golden 用例基于真实数仓表 | 已确认缺陷 | demo 部分已解决；真实样本阻塞 |
| C-04 | `checks.py:161-175` 数字集合包含：不校验行数/列结构、"10"≠"10.0"、期望数字可碰巧命中无关列（实测"少一行"仍通过） | 静态审查发现 → 已运行复现 | 已修复并通过验证 |

## 4. 修改的文件和关键函数

| 文件 | 修改 | 关键函数 |
|---|---|---|
| `sql_validator.py` | 新增 `_DETAIL_TABLE_HINTS` 常量、`_joined_tables()`（sqlglot 提取 JOIN 子句表名）；`_check_p2` 尾部新增未登记关联告警 | `_check_p2` |
| `server.py` | import `secrets`；中间件生成 `request_id`；`_audit_permission.final_sql` 改脱敏 | `_auth_middleware`、`_audit_permission` |
| `permission.py` | 新增公开别名 `safe_sql_repr = _safe_sql_repr`（判定逻辑零改动） | — |
| `evaluation/evaluators/checks.py` | 新增 `_norm_cell()`、`_check_result_structured()`；`check_result` 优先结构化比较，旧格式向后兼容 | `check_result` |
| `run_tests.py` | REGISTRY 注册 4 个新测试（unit 层） | — |

**新增文件**：`test_join_cardinality.py`（15 用例）、`test_audit_safety.py`（9 用例）、`test_result_check.py`（16 用例）、`test_golden_demo_dataset.py`（9 用例）、`evaluation/datasets/golden-demo/golden_demo_v1.jsonl`（26 条）。

**未修改**：agent.py（唯一执行出口/闸门/降级逻辑原样）、guard.py、permission 判定逻辑、config_store.py、db_*、前端、API 契约、数据库结构、生产数据。

## 5. 修复方案及设计取舍

1. **JOIN 告警只 warning 不 error**：任务书要求"不允许无差别阻断所有 JOIN"。表名明细后缀（`_fee_/_detail_/_record_/_history_...`，带边界下划线匹配以兼容 `_all` 结尾真实表名）仅作启发式线索，message 明确写"**关联基数未确认**"，不假装识别一对多；已登记关系的右表不重复告警；预聚合子查询（sqlglot 判定 JOIN 对象是子查询）不告警。
2. **审计脱敏复用而非重写**：`_safe_sql_repr` 已存在且语义正确（字面量→`?`），仅加公开别名；未动 `query_records` 的 SQL 明文——取数历史"可回看 SQL"是业务功能（API 契约），权限审计表才是安全日志，两者区别对待。
3. **判分器增强向后兼容**：旧"数字集合包含"逻辑保留（兼容潜在旧格式数据），结构化格式（`{"rows":[...],"columns":[...],"precision":N}`）优先；行序默认无序比较；NULL 归一兼容 CK 空串与 SQLite NULL；时间按字面文本比较（不引入时区歧义）。
4. **demo 评测集而非伪造真实样本**：golden 现有用例基于真实数仓，本地无数据，按任务书"不得伪造 expected_result"——改用 `demo_shop.db` 固定种子数据人工核算 26 条，每条带 `review_note` 记核算依据，`review_status=approved`。

## 6. 新增测试用例（46 个断言组，全部可重复、隔离、不依赖生产）

| 测试文件 | 用例数 | 覆盖 |
|---|---|---|
| test_join_cardinality.py | 15 | 放大效应实证（COUNT 3→5、SUM 600→1000、状态历史 3→6，人工核算）；已登记拦截/放行回归；未登记告警；1:1 维表不误伤；预聚合不告警 |
| test_audit_safety.py | 9 | request_id 生成/唯一性/传播；审计 SQL 无手机号/身证明文且保留结构；字面量脱敏 ≠ 结果脱敏 |
| test_result_check.py | 16 | 结构化通过/失败（行数/列数/数值）；精度与 NULL 规则；旧格式兼容；弱比较假阳性修复对照 |
| test_golden_demo_dataset.py | 9 | 26 条 expected_sql 在 demo 库执行与 expected_result 全一致；篡改期望被发现；弱/强比较对照 |

## 7. 修改前后的测试结果

| 阶段 | 结果 |
|---|---|
| 修改前复现 | test_join_cardinality 12/15 ❌3；test_audit_safety 4/9 ❌5；test_result_check 10/16 ❌6（失败项即缺陷复现，记录留档） |
| 修改后专项 | 三者 **15/15、9/9、16/16 全绿**；test_golden_demo_dataset **9/9 全绿** |
| 修改后 unit 回归 | **12 项全绿**（含 test_gate 122/122、test_permission、test_guard 等原有测试零回归；新增 2 项已注册） |
| 修改后 config 回归 | **test_acl 84/84、test_knowledge_governance 31/31 全绿** |
| integration | test_gaps 需真实 ClickHouse，未运行（无生产库访问，不据其判定缺陷） |

**新增失败：0**；无与本次改动无关的原有失败被归因于整改。

## 8. Golden Dataset 样本数量及审核状态

- `golden-demo/golden_demo_v1.jsonl`：**26 条**，全部 `review_status=approved`，`source=demo_seed`，每条附 `review_note` 人工核算依据（如"4/7=0.571428，precision=4 归一后 0.5714"）。
- 覆盖场景：计数/金额/比率、时间过滤、状态口径（创建时间×完成状态混用陷阱）、分组、JOIN 防放大（COUNT DISTINCT）、字典枚举（金卡 level=3）、拒答类保留在原 golden 集。
- 原 `golden/golden_v1.jsonl` 35 条（真实数仓）**保持不动**，等待业务方提供标准 SQL+结果后按 `docs/golden_dataset_schema.md` 流程补 `expected_result`。

## 9. 结果准确率的计算方法和实际结果

- **计算方法**：`result_accuracy = 通过 check_result 的用例数 / 提供 expected_result 的用例数`；check_result 结构化规则 = 行数一致 ∧ 列数一致 ∧ 无序逐行匹配（数值按 precision 归一、NULL 归一）。`expected_ok`（可执行）与 `result`（结果正确）是两个独立维度，`overall_accuracy` 为全维度 AND。
- **已验证**：判分器逻辑经 16 项单元断言验证；26 条样本自洽性经 demo 库真实执行复核（机器复核"人工核算"）；篡改期望值可被判分器发现。
- **未声称**：本轮**未运行**端到端 LLM 评测（调用付费模型需授权），故**未产生新的 result_accuracy 数值**，不编造。就绪命令（授权后执行）：
  ```
  .venv/bin/python evaluate.py --dataset evaluation/datasets/golden-demo/golden_demo_v1.jsonl --tag demo-baseline
  ```
  跑完后报告中 result_accuracy 将基于 26 条真实计算（demo 集不依赖真实 CK，仅 demo_shop.db + DeepSeek API）。

## 10. 未修复问题、未覆盖场景和剩余风险

| 项 | 状态 | 说明 |
|---|---|---|
| 真实业务 Golden 20 条 | ⛔ 阻塞 | 需业务方提供标准 SQL 与结果（或授权访问生产 CK 由业务方核算）；阻塞解除前 result_accuracy 只覆盖 demo 场景 |
| S-02 登录限速/密码强度 | ⏸ 未实施 | 任务书标注"先不做"；证据已留档（baseline §四） |
| 端到端 LLM 评测 | 待授权 | 调用付费模型需先说明（约 26 条 × 2~3 次调用，成本极低） |
| 凭据脱敏实施 | 待 owner | `.env`/`tunnel.sh`/`deploy.md`/plist 的脱敏属入库前操作，涉及生产信息修改，按任务书"不擅自修改"仅列出（见 §12） |
| 剩余风险 | — | ① 启发式告警对非明细命名的未登记一对多表仍无法发现（表名不是可靠基数证据，这是刻意取舍）；② `db_sqlite.py` PRAGMA f-string 拼接待验证（baseline §五）；③ result_validator 的 4 类启发式 warning 仍不阻断（设计如此） |

## 11. 需要人工确认的业务口径

1. 真实数仓中未登记的一对多表对清单 → 建议在知识库"关联/Join 规则"补登记（新告警 `join_unregistered_risk` 会在管理侧提示候选）。
2. 真实业务 Golden 样本的标准 SQL 与预期结果（20 条）。
3. `query_records` 保存 SQL 明文是否符合公司合规要求（当前为业务功能设计，若合规要求脱敏需另行评审，会影响"回看 SQL"功能）。

## 12. 凭据与敏感文件检查结果（任务 9，只列位置与类型，不含值）

| 位置 | 风险类型 | 建议 |
|---|---|---|
| `.env`（根目录） | 真实密钥文件存在 | 入库前必须排除；已确认当前**非 git 仓库**、无历史泄露 |
| `.gitignore` | **不存在** | git init 前必须创建（排除 .env/*.db*/.venv/models/dist 等） |
| `tunnel.sh:6` | 硬编码公网跳板机 IP | 改从 .env 读取或占位符 |
| `tunnel.sh:32` | 内网 CK IP 默认值 | 同上（可被 .env 覆盖，风险较低） |
| `deploy.md`、`docs/zhishu-ai-packaging.md`、`.env.example:23`、`ssh_tunnel.py`（注释） | 内网基础设施信息 | 入库前占位符化 |
| `com.zhishuai.server.plist` | 个人绝对路径 | 占位符化或移出示例目录 |
| `agent_config.db` + 4 个备份、`demo_shop.db`、空 `config.db` | 数据/账号（含密码哈希） | 一律不入库；备份控权限 |
| 源码硬编码 API Key（`sk-…`） | **未发现** ✓ | 密钥仅存于 .env |
| `zhishu-ai-code-bundle/`、`dist/`、`*.zip` | 旧源码副本 | 不入库；未发现其中含 .env/.db |
| 历史提交 | 无（非 git 仓库） | 若曾被打包外发，建议评估轮换 DEEPSEEK_API_KEY / CK 密码 / SSH 密码（**未经批准不擅自轮换**） |

## 13. 回滚方式与下一阶段建议

**回滚**：
- 代码：本轮修改集中在 4 个文件，可用基线备份对照还原——`cp agent_config.db.bak-20261026-baseline agent_config.db` 恢复配置库；源码回滚点为各文件中带"整改任务3/4/7"注释的代码块（删除即回到改前行为，`_joined_tables`/`_check_result_structured`/`safe_sql_repr` 为新增函数，删除无副作用）。
- 测试：4 个新测试文件独立，删除即可；`run_tests.py` REGISTRY 移除对应 4 行。
- 数据集：`golden-demo/` 目录独立，删除不影响原 golden 集。

**下一阶段建议**：
1. 授权跑 demo 集端到端评测，得到首个 result_accuracy 实数（§9 命令）。
2. 业务方补真实 Golden 样本 20 条 → `evaluate.py --dataset golden --gate` 冲 93% 门禁。
3. 按任务书第二优先级实施 S-02 登录限速与密码强度（设计要点已在 baseline 留档）。
4. git 入库前按 §12 清单完成脱敏 + `.gitignore`（方案见 `docs/audit/05-github-security-audit.md`）。
5. 工程可靠性清单（下一轮候选）：DB 连接 try/finally 化、噪声表规则收敛单份、账号改删联动清理、logging+trace_id 体系化。
