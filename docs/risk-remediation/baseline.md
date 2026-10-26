# 知数AI核心风险专项整改 · 第一阶段基线（baseline.md）

> 检查时间：2026-10-26 09:54~10:00 ｜ 方式：只读检查 + 测试运行，未修改任何业务源码
> 上次全面审查：2026-10-23（`docs/audit/`）。本次已核实：**10-23 后无任何 `.py` 源码修改**（find newermt 无结果），审查证据仍有效。

---

## 一、环境与保护措施

| 项 | 状态 |
|---|---|
| Git | **非 git 仓库**（无历史泄露风险，但也无版本回滚能力；本轮修改以"修改前备份 + 测试记录"作为回滚依据） |
| Python | 3.13.12（`.venv`） |
| 配置库备份 | `agent_config.db.bak-20261026-baseline`（运行 config 层测试前已备份） |
| 修改文件范围 | 见第六节，全部在白名单内 |

---

## 二、当前真实取数执行链（任务 1）

以当前源码核实（行号与 10-23 审查一致）：

| 步骤 | 函数（位置） | 失败处理 | 绕过风险 |
|---|---|---|---|
| 1 请求入口 | `server.api_ask`（server.py:702） | 401/403 中间件拦截 | 身份取 `request.state.user`（中间件 131-157 服务端会话判定），**无法用请求参数伪造** |
| 2 编排 | `agent.ask`（agent.py:892）→ `ask_v2`（601） | V2 异常按 `LEGACY_FALLBACK_MODE` 白名单降级；**安全/校验类异常永不回退** | — |
| 3 查询理解 | `query_understanding.understand` | 8s 超时降级 `degraded=True` | — |
| 4 分层检索 | `semantic_layer.retrieve_semantic`（244） | 未召回表/指标 → 拒答（agent.py:658-662） | — |
| 5 SQL 生成 | `llm.chat` → `_finalize_sql` | 生成失败重试 | — |
| 6 统一闸门 | `_pre_execute_check`（agent.py:517）：`guard.sql_guard`（只读）→ `guard.check_tables_ex`（表权限）→ `sql_validator.validate_sql_ex`（五维）→ `permission.evaluate`（字段级）→ `apply_row_filters`（行级，改写后重新校验） | 任一 error → 不执行 | **校验器异常 fail-closed**（sql_validator.py:480-491，异常记 status=error 阻断）；test_gate 122 项覆盖 |
| 7 唯一执行出口 | `_exec_sql`（agent.py:114-132），拒答/非查询抛 `NotExecutable` | — | 自愈 SQL **重新过完整闸门**（agent.py:786-813） |
| 8 结果校验 | `result_validator.validate_result`（result_validator.py:34） | 仅 warning，不阻断 | **无标准答案对比**（见问题 C-02） |
| 9 结果脱敏 | `permission.mask_rows`（agent.py:838-841） | — | — |
| 10 NL 回答 | `nl_answer.answer`（agent.py:850） | 失败降级表格快照 | — |
| 11 审计 | `server._audit_permission`（server.py:818） | 落库失败仅打印，不影响主流程 | **S-01：SQL 明文 + request_id 恒空**（见下） |

**结论**：唯一执行出口、自愈重校验、fail-closed 均在位且有测试覆盖（test_gate 122 项全通过）。未发现绕过校验进入执行的分支。

---

## 三、测试基线（任务 2）

执行命令（2026-10-26 10:00，耗时 2m05s）：

```
.venv/bin/python run_tests.py unit     # 纯逻辑层
.venv/bin/python run_tests.py config   # 本地配置库层
```

| 层 | 测试 | 结果 |
|---|---|---|
| unit | test_imports（26/26）、test_pipeline（11/11）、test_gate（**122/122**）、test_join_suggest（10/10）、test_guard、test_labels（33/33）、test_retrieve（22/22）、test_permission、test_legacy_validation（9/9）、test_calendar | **全部通过 ✅** |
| config | test_acl（84/84）、test_knowledge_governance（31/31） | **全部通过 ✅** |
| integration | test_gaps（需真实 ClickHouse） | 未运行（无生产库访问，不据其失败判定缺陷） |

- unit 层不依赖外部服务；config 层读写本地 `agent_config.db`（已备份，测试后核实库未被破坏）。
- **既有失败：0**。后续回归以"新增失败"为整改引入问题的判据。

### 评测指标现状（result_accuracy / field_retrieval_recall）

- `check_result` 比较逻辑**已存在**（`evaluation/evaluators/checks.py:161-175`）：把期望与实际结果提取数字集合做包含判断——弱比较（不校验列结构、数值 "10"≠"10.0"、期望数字可能碰巧命中无关列）。
- **`result_accuracy=None` 的真实原因：golden 集仅 35 条、其中 `expected_result` 覆盖为 0 条**（grep 实测 0）。是**样本缺失**，不是评测逻辑缺失。
- **golden 集基于真实数仓表**（`dwd_tu_order_all`/`ods_tu_customer_all`/`ods_valet_order_all` 等），本地无生产数据，**无法为现有用例人工核算预期结果** → 真实业务样本按任务书标记**待审核/阻塞**。
- `field_retrieval_recall`：checks.py 无该 check（schema.py 审计项），确认未实现。

---

## 四、已确认问题与证据（证据等级标注）

| 编号 | 问题 | 等级 | 证据 |
|---|---|---|---|
| C-01 | 未登记关系的一对多 JOIN 无拦截：`_check_p2` 仅遍历已登记且 `pre_agg_required=True` 的关系（sql_validator.py:240-261） | **静态审查发现**（运行复现见任务 3） | sql_validator.py:240-261；semantic_layer.py:346-363（关系只来自登记表） |
| C-02 | 结果正确性无标准答案对比：golden 0 条 `expected_result` | **已确认缺陷**（数据缺失） | `grep -c expected_result golden_v1.jsonl` = 0；baseline.json `result_accuracy=None` |
| S-01a | 审计存 SQL 明文：`_audit_permission` 的 `final_sql` 直接取 `payload["sql"]`（server.py:835），未用已有的 `permission._safe_sql_repr`（permission.py:613） | **已确认缺陷** | server.py:835；config_store.add_permission_audit 落库 |
| S-01b | request_id 恒空：server.py:826 读 `request.state.request_id`，但中间件（server.py:130-157）**从未设置该属性** | **已确认缺陷** | 中间件全文 131-157 无 request_id 赋值 |
| S-02 | 登录无速率限制、密码仅≥6位 | **静态审查发现**（任务书标注"先不做"） | server.py:427；config_store.py:2310/2428/2498 |
| S-03 | `.env` 含真实密钥、`tunnel.sh` 硬编码跳板机 IP | **已确认（凭据暴露面）**，非 git 仓库暂无历史泄露 | 任务 9 详细扫描 |
| C-04 | `check_result` 弱比较：无列结构/精度/NULL 规则 | **静态审查发现** | checks.py:161-175 |

---

## 五、待验证风险与业务口径（需人工确认）

1. **真实数仓中的实际一对多关系清单**：哪些表对存在 1:N 且未登记 → 需业务方在知识库补登记（代码侧只能启发式告警，不能替业务断言基数）。
2. **真实业务 Golden 样本**：20 条带 `expected_result` 的人工审核样本需业务方提供标准 SQL 与结果（本地已按 demo 固定数据先建样本集，见任务 4）。
3. **`db_sqlite.py` PRAGMA f-string 拼接**（表名来自 sqlite_master/管理接口）：当前无注入实据，标记待验证风险。
4. **生产 CK 只读账号实际授权范围**：无法本地验证，需 `ck_check.py` 在生产侧执行。

---

## 六、本轮计划修改的文件范围（白名单）

| 文件 | 任务 | 修改性质 |
|---|---|---|
| `sql_validator.py` | 任务3 | `_check_p2` 增加未登记关联启发式告警（warning，不改既有 error 拦截） |
| `test_gate.py` 或新增 `test_join_cardinality.py` | 任务3 | 新增 JOIN 基数测试（临时 SQLite，人工核算预期） |
| `server.py` | 任务7 | `_auth_middleware` 生成 request_id；`_audit_permission` 复用 `_safe_sql_repr` |
| `test_permission.py` 或新增 `test_audit_safety.py` | 任务7 | 审计脱敏 + request_id 传播测试 |
| `evaluation/evaluators/checks.py` | 任务4 | `check_result` 增强（结构化比较/精度归一/NULL 规则，保持向后兼容） |
| `evaluation/datasets/`（新增 demo 结果集 jsonl） | 任务4 | 基于 demo_shop.db 固定数据人工核算 ≥20 条 |
| `docs/risk-remediation/` | 任务11 | 报告 |

**不修改**：agent.py 核心闸门、guard.py、permission.py 判定逻辑、db_*、config_store.py、前端、API 契约、数据库结构。

---

## 七、暂停条件确认

本轮未触发暂停条件：不依赖未确认口径即可启动任务 3/7/4(demo 部分)/9；真实业务口径类（生产 CK 授权、真实 Golden 样本）按任务书记录阻塞，不猜测执行。
