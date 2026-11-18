# 06 · P0 数据治理补齐清单（第二阶段 2-A 只读产出）

> 生成日期：2026-11-10 · 性质：只读产出（未写库、未改代码）
> 用途：为「第二阶段实施（写库）」提供精确、可执行的清单与脚本
> 依据：sqlite3 实测 `agent_config.db` + 通读 `_migrate_governance.py` / `_fix_metric_predicates.py` / `sql_validator.py`

---

## 0. 结论摘要

| 项 | 状态 | 数量 |
|---|---|---|
| `stable_key` 缺失行 | 需回填 | 6 表共 **123 行** + `kb_docs` 全表缺列 |
| `metric_constraint` 与 `metrics` 脱节 | 需重建（僵尸派生表） | 5 处不一致 |
| `candidate` 待复核知识 | 需人工复核 | **64 条** |

---

## 1. stable_key 回填清单（精确）

### 1.1 现状：`kb_docs` 根本没有 `stable_key` 列

实测 `PRAGMA table_info(kb_docs)`：**无 `stable_key` 列**（其余 6 表均有）。
且 `_migrate_governance.py` 的回填函数只覆盖 6 表，**不含 kb_docs**。
→ kb_docs 需要「加列 + 回填」两步，是本次唯一需要 `ALTER TABLE` 的表。

### 1.2 各表精确缺口（实测行）

| 表 | 缺失行数 | 缺失明细 |
|---|---|---|
| `metrics` | 9 | 代驾司机保证金缴纳司机数/缴纳数/缴纳金额、转出人数/转出金额、客户积分实付金额、小板完单量、小板完单金额、总积分 |
| `entities` | 10 | 司机保证金订单、司机保证金转出、司机每日接单行为、客户渠道归因、客户积分完单记录表、客户积分批次、客户积分批次表、客户积分流水、客户积分流水/完单记录表、客户黑名单 |
| `relationships` | 6 | id=26,27,28,29（积分批次×订单四张）、id=44（客户×登录记录）、id=45（客户×注册明细） |
| `dict_entries` | 5 | 司机会员类型、完单渠道、官网站群、积分流水业务渠道、资金流水类型 |
| `table_comments` | 7 | ods_ctc_point_batch_all、ods_ctc_point_record_all、ods_customer_blacklist_all、ods_customer_channel_detail_all、ods_driver_day_behavior_count_all、ods_valet_deposit_all、ods_valet_driver_deposit_transfer_out_all |
| `column_comments` | 84 | 略（复合键「表.字段」，多为后期 seed 新增） |
| `kb_docs` | 10 | **整表缺列**（id=3,4,6,8,9,10,11,12,13,14） |

### 1.3 推荐执行方式（写库步骤，待你确认后执行）

**方式 A（推荐，复用已有脚本）：** 重新运行 `python _migrate_governance.py`
—— 该脚本回填函数对 6 张表**全量遍历 UPDATE**（`SELECT * FROM metrics` 后逐行 UPDATE），
天然覆盖上述 6 表缺失行，且幂等（已填的不变）。

**方式 B（kb_docs 需手工补充）：** 脚本未覆盖 kb_docs，需手工：

```sql
-- ① 加列（与其它知识表对齐，默认无值）
ALTER TABLE kb_docs ADD COLUMN stable_key TEXT;
CREATE INDEX IF NOT EXISTS idx_kb_docs_stable ON kb_docs(stable_key);

-- ② 回填（约定格式 doc:{id}，与 metric:/entity:/dict:/rel:/table:/column: 前缀一致）
UPDATE kb_docs SET stable_key = 'doc:' || id WHERE stable_key IS NULL OR stable_key = '';
```

### 1.4 回填后验收标准

```sql
-- 六表 stable_key 填充率应为 100%
SELECT 'metrics' t, SUM(stable_key IS NOT NULL AND stable_key<>'')||'/'||COUNT(*) FROM metrics
UNION ALL SELECT 'entities', SUM(stable_key IS NOT NULL AND stable_key<>'')||'/'||COUNT(*) FROM entities
UNION ALL SELECT 'relationships', SUM(stable_key IS NOT NULL AND stable_key<>'')||'/'||COUNT(*) FROM relationships
UNION ALL SELECT 'dict_entries', SUM(stable_key IS NOT NULL AND stable_key<>'')||'/'||COUNT(*) FROM dict_entries
UNION ALL SELECT 'table_comments', SUM(stable_key IS NOT NULL AND stable_key<>'')||'/'||COUNT(*) FROM table_comments
UNION ALL SELECT 'column_comments', SUM(stable_key IS NOT NULL AND stable_key<>'')||'/'||COUNT(*) FROM column_comments
UNION ALL SELECT 'kb_docs', SUM(stable_key IS NOT NULL AND stable_key<>'')||'/'||COUNT(*) FROM kb_docs;
```

---

## 2. metric_constraint 一致性发现（重要）

### 2.1 结论：`metric_constraint` 是「僵尸派生表」

- **定位**：派生表，由 `_migrate_governance.build_metric_constraint`（`DELETE` + `INSERT` 全量重建）。
- **运行时消费者：无。** `sql_validator._check_p1` 实际读取的是 `ctx.p1_metrics` 里的
  `required_predicates / required_columns / forbidden_predicates`（来自 `metrics` 表），
  **不读 metric_constraint 表**。全仓只有 `_migrate_governance.py` 与 `_seed_metric_constraints.py` 写它。

### 2.2 脱节证据（实测）

| 指标 | metrics 列现状 | metric_constraint 现状 | 判断 |
|---|---|---|---|
| 完单率 | REQ=[]（已清空） | 仍有 required_predicate=status=6 | 派生表**过期** |
| 接单率 | REQ=[]（已清空） | 仍有 required_predicate=is_picked=1 | 派生表**过期** |
| 接单量 | REQ=[]（已清空） | 仍有 required_predicate=is_picked=1 | 派生表**过期** |
| 代驾司机保证金转出人数 | REQ=["status = 1"] | **缺行** | 派生表**未重建** |
| 代驾司机保证金转出金额 | REQ=["status = 1"] | **缺行** | 派生表**未重建** |

根因：`_fix_metric_predicates.py`（2026-10-29）把完单率/接单率/接单量的 `required_predicates`
清空（这是**正确修复**——它们原是「分子条件」被误标为「全局过滤」），但之后未重建 metric_constraint；
同时后期 seed 新增了保证金转出指标的约束，也未重建。

### 2.3 影响评估与建议

- **对取数正确性：无影响**（运行时读 metrics，不读 metric_constraint）。
- **对治理/复核：有误导**（`_review_governance.py` 与任何基于 metric_constraint 的报表会看到过期约束）。
- **建议**：写库阶段执行 `_migrate_governance.py`（其会重建 metric_constraint），
  或干脆**停用该派生表**，只保留 metrics 列为唯一事实源（更干净）。

---

## 3. candidate 复核清单（64 条）

> 严格模式（`GOVERNANCE_STRICT_STATUS=true`）下 candidate 不进生产检索，需人工复核转 approved / rejected。

### 3.1 指标 metrics（3 条）

| 名称 | 备注 |
|---|---|
| 小板完单量 | 待复核口径 |
| 小板完单金额 | 待复核口径 |
| 总积分 | 待复核口径 |

### 3.2 实体 entities（7 条）

司机保证金订单、司机保证金转出、客户渠道归因、客户积分完单记录表、客户积分批次表、客户积分流水/完单记录表、客户黑名单

### 3.3 文档 kb_docs（1 条）

id=14《客户积分与分渠道完单统计取数口径》

### 3.4 字典 dict_entries（2 条）

完单渠道、积分流水业务渠道

### 3.5 表注释 table_comments（2 条）

ods_customer_blacklist_all、ods_valet_deposit_all

### 3.6 字段注释 column_comments（49 条，**全部 source=ai**）

> 注意：49 条全是 AI 生成（`source='ai'`），在严格模式下**已被 source 闸单独排除**，
> 复核重点不是「是否生效」，而是「内容是否正确、能否转 manual+approved」。

| 表 | 字段 | 说明（截断） |
|---|---|---|
| dwd_driver_base_profile_all | annual_fee_status | 审核激活费状态 |
| dwd_driver_base_profile_all | driver_category | 是否平台司机 |
| dwd_tu_order_cancel_all | customer_cancel_status | 用户取消类型 |
| dwd_tu_order_lab_all | carstate | 车辆类型 |
| dwd_tu_order_lab_all | cartype_code_lab | 车型标签 |
| dwd_tu_order_lab_all | is_deduplicated_order_lab | 是否去重订单标签 |
| dwd_tu_order_lab_all | is_invoice | 是否开发票 |
| dwd_tu_order_lab_all | is_work_day_lab | 工作日标签 |
| dwd_tu_order_lab_all | pick_type | 接单方式 |
| ods_ctc_point_batch_all | create_time | 积分批次创建时间 |
| ods_ctc_point_batch_all | point | 积分值 |
| ods_ctc_point_record_all | amount | 完单实付金额 |
| ods_ctc_point_record_all | biz_no | 业务单号 |
| ods_ctc_point_record_all | channel | 业务渠道编码 |
| ods_ctc_point_record_all | create_time | 记录创建时间 |
| ods_offline_order_driver_location_statistical_all | biz_type | 业务类型 |
| ods_offline_order_driver_location_statistical_all | create_source | 创建来源 |
| ods_offline_order_driver_location_statistical_all | driver_type | 司机类型 |
| ods_offline_order_driver_location_statistical_all | handle_type | 处理类型 |
| ods_offline_order_driver_location_statistical_all | is_need_pay_msg_fee | 是否需要付信息费 |
| ods_offline_order_driver_location_statistical_all | is_read | 是否已读 |
| ods_offline_order_driver_location_statistical_all | is_send_msg | 是否发送短信 |
| ods_rescue_dispatcher_binding_all | binding_status | 救援调度绑定状态 |
| ods_tu_customer_all | pay_mode_type | 支付模式 |
| ods_tu_guaranty_order_all | biz_type | 业务类型 |
| ods_tu_guaranty_order_all | goods_type | 货物类型 |
| ods_tu_guaranty_order_all | guaranty_status | 保障订单状态 |
| ods_tu_guaranty_order_all | inner_entry_user_type | 内部录单用户类型 |
| ods_tu_guaranty_order_all | insure_type | 保障类型 |
| ods_tu_guaranty_order_all | insured_certificate_type | 被保人证件类型 |
| ods_tu_guaranty_order_all | is_first | 是否首单 |
| ods_tu_guaranty_order_all | is_give | 是否赠送 |
| ods_tu_guaranty_order_all | is_special | 是否特殊保障订单 |
| ods_tu_guaranty_order_all | refund_audit_status | 退保审核状态 |
| ods_tu_guaranty_order_all | source | 来源入口 |
| ods_tu_large_handcart_order_all | sub_state | 订单子状态 |
| ods_tu_mall_order_all | is_pay | 是否支付 |
| ods_tu_mall_order_all | pay_type | 支付方式 |
| ods_tu_mall_order_all | state | 商城订单状态 |
| ods_tu_mall_order_log_all | before_state | 变更前状态 |
| ods_tu_pay_log_all | pay_channel | 支付渠道 |
| ods_tu_pay_log_all | refund_type | 退款方式 |
| ods_valet_order_all | order_type | 订单类型 |
| ods_violation_info_all | additional_release_condition_type | 额外解除条件类型 |
| ods_violation_info_all | biz_type | 业务类型 |
| ods_violation_info_all | is_current_punish | 是否为当前处罚 |
| ods_violation_punish_config_all | is_exist_buffer_period | 是否有缓冲期 |
| ods_violation_punish_config_all | punish_duration_type | 处罚时长类型 |
| ods_violation_resell_order_all | biz_type | 业务类型 |

---

## 4. 建议执行顺序（待你确认写库后）

1. 备份：`cp agent_config.db agent_config.db.bak-$(date +%Y%m%d-%H%M%S)`
2. 回填 stable_key：重跑 `_migrate_governance.py` + 手工补 kb_docs（§1.3）
3. 一致性：`_migrate_governance.py` 会顺带重建 metric_constraint（或决定停用该派生表）
4. 复核：逐条处理 64 条 candidate（可先批量看，再逐条 approved/rejected）
5. 验收：跑 §1.4 的填充率 SQL + 重建 RAG 索引（`POST /api/index/rebuild`）

---

## 5. 执行结果（2026-11-10 已写库完成）

### 5.1 已执行操作

| # | 操作 | 结果 |
|---|---|---|
| 1 | 备份 | `agent_config.db.bak-semantickb-20261110-084326`（7704576 字节，与原库一致） |
| 2 | 重跑 `_migrate_governance.py` | 回填 6 表 433 行；新增治理列 0（已齐）；metric_constraint 重建 16 行；audit_log 回灌 1263 行 |
| 3 | kb_docs 手工补列+回填 | 加 `stable_key` 列 + `idx_kb_docs_stable` 索引 + 回填 `doc:{id}` 10 行 |

### 5.2 验收结果（全部通过）

- ✅ stable_key 填充率 **7 表全 100%**：metrics 57/57、entities 52/52、relationships 22/22、dict_entries 23/23、table_comments 46/46、column_comments 233/233、kb_docs 10/10。
- ✅ metric_constraint 与 metrics **双向 0 差异**（重建后从 17 行→16 行，清掉完单率/接单率/接单量 3 条误标约束、新增保证金转出人数/金额 2 条）。
- ✅ 表数量仍 38（未新增）；candidate 仍 64（`keep_status` 护栏生效，未误覆盖复核结论）。

### 5.3 未执行项（需人工）

- **candidate 复核（64 条）未执行**：这是需要业务口径权威判断的人工环节，治理原则明确「AI 生成内容必须人工复核」，agent 不应擅自 approve/reject。清单见 §3。
