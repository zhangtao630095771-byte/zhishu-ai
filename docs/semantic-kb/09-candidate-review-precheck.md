# 09 · Candidate 复核预审报告（64 条）

> 生成日期：2026-11-10 · 性质：预审辅助（未执行任何 approve/reject，最终裁决权在人工）
> 配套脚本：`_review_candidates.py`（默认 dry-run，`--apply` 才实际改库）

---

## 0. 预审结论（分档）

| 档 | 数量 | 结论 |
|---|---|---|
| **A 档（建议 approve）** | 59 条 | 证据充分、内容自洽、无冲突 |
| **B 档（需人工细看）** | 2 条 | 字典 field_hint 冲突 |
| **C 档（建议 rejected）** | 3 条 | 已被去重停用的冗余实体 |
| （另有）已停用实体 | 3 条 | 见 §4，与 C 档重叠说明 |

> ⚠️ **重要前提**：49 条字段注释全部 `source=ai`，我的预审只能判断「格式规范、无内部矛盾、无与已有知识冲突」，**无法验证「枚举值是否与实际数据一致」**——这需要人工抽查或查 ClickHouse 实际值。因此 A 档的「建议 approve」是「建议人工抽查后批量 approve」，不是「直接 approve」。

---

## 1. A 档：建议 approve（59 条）

### 1.1 指标（3 条）—— definition 具体可执行、与 channel 口径自洽

| 指标 | definition 摘要 | 判定 |
|---|---|---|
| 小板完单量 | `ods_ctc_point_record_all` 中 `channel=1`，`COUNT(DISTINCT biz_no)` | ✅ 口径完整 |
| 小板完单金额 | `ods_ctc_point_record_all` 中 `channel=1`，`SUM(amount)` | ✅ 口径完整 |
| 总积分 | `ods_ctc_point_batch_all` 按 `customer_id` 汇总 `point` | ✅ 口径完整 |

自洽性：`channel=1` 与字段注释 `ods_ctc_point_record_all.channel`（1=小板）及字典「积分流水业务渠道」（1=小板）一致。

### 1.2 实体（4 条，enabled=1 的）—— table_name 准确、grain/comment 清晰

| 实体 | 主表 | 判定 |
|---|---|---|
| 司机保证金订单 | ods_valet_deposit_all | ✅ |
| 司机保证金转出 | ods_valet_driver_deposit_transfer_out_all | ✅ |
| 客户渠道归因 | ods_customer_channel_detail_all | ✅ |
| 客户黑名单 | ods_customer_blacklist_all | ✅ |

### 1.3 表注释（2 条）

| 表 | 注释摘要 | 判定 |
|---|---|---|
| ods_customer_blacklist_all | 客户黑名单管控信息 | ✅ |
| ods_valet_deposit_all | 司机代驾保证金订单明细 | ✅ |

### 1.4 文档（1 条）

| 文档 | 内容摘要 | 判定 |
|---|---|---|
| id=14《客户积分与分渠道完单统计取数口径》 | 总积分取 `ods_ctc_point_batch_all`、分渠道完单取 `ods_ctc_point_record_all`（channel 1=小板/2=代驾/3=大板）、两表按 customer_id LEFT JOIN | ✅ 与 A 档指标口径完全自洽 |

### 1.5 字段注释（49 条）

- 全部 `source=ai`，格式为「枚举值=含义」或「（空=未设置，值=含义）」，无重复（同字段无 approved 版本冲突）。
- 抽查样例均自洽：`ods_valet_order_all.order_type`（1=普通/2=预约/3=其他）、`ods_tu_mall_order_all.state`（1待支付~9已取消）、`ods_violation_info_all.biz_type`（1拖车/3救援）等。
- **建议**：人工抽查 3~5 处枚举值（对照 ClickHouse 实际数据）后批量 approve。

---

## 2. B 档：需人工细看（2 条字典）

| 字典 | field_hint | 内容 | 问题 |
|---|---|---|---|
| 完单渠道 | `channel` | 1=拖车小板完单、2=代驾完单、… | 与「积分流水业务渠道」同绑 `channel`，语义重叠 |
| 积分流水业务渠道 | `channel` | 1=小板、2=代驾、3=大板 | 同上 |

**风险**：`field_hint="channel"` 太泛（多张表都有 `channel` 字段），且两个字典语义有重叠（「小板」≈「拖车小板完单」）。需人工确认：
1. 这两个字典是否重复，应否合并？
2. `field_hint` 应否绑定到具体字段（如 `ods_ctc_point_record_all.channel`），而非泛化的 `channel`？

---

## 3. C 档：建议 rejected（3 条已停用实体）

这三条在「实体去重」中已被停用（`enabled=0`），是「客户积分流水 / 客户积分批次」的重复别名：

| 实体 | 现状 |
|---|---|
| 客户积分批次表 | 已停用（重复于「客户积分批次」） |
| 客户积分完单记录表 | 已停用（重复于「客户积分流水」） |
| 客户积分流水/完单记录表 | 已停用（重复于「客户积分流水」） |

建议将这三条 `status` 由 `candidate` 改为 `rejected`（重复冗余，不采纳），使账目一致（`enabled=0` + `rejected`）。

---

## 4. 审批方式

### 方式一：用配套脚本（推荐）

```bash
# 预览（dry-run，不改库）
.venv/bin/python _review_candidates.py

# 执行 A 档 approve（58 条：3 指标 + 4 实体 + 2 表注释 + 49 字段注释）
.venv/bin/python _review_candidates.py --apply A

# 执行 C 档 reject（3 条已停用实体）
.venv/bin/python _review_candidates.py --apply C

# B 档（2 条字典）脚本不会自动处理，需人工决策后在脚本里补
```

### 方式二：页面人工点「通过 / 拒绝」

知识治理页面（`gov` tab）逐条操作，底层走 `config_store.set_knowledge_status`，自动写审计。

---

## 5. 审批后收尾

- 审批完成后，**重建 RAG 索引**（`POST /api/index/rebuild`）让结论生效；
- 注意：B 档 2 条字典若保持 candidate，严格模式下不进检索，不影响正确性（只是少一条字典召回）。
