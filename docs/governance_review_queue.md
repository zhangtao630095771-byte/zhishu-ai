# 知识复核清单（candidate → approved）

> 首次生成：2026-10-27 14:02:50　复核人：admin　**当前状态：已执行完成**

> 判定规则：内容过短/占位词/占位式枚举/跨表同名口径冲突 → 留人工；人工确认错误 → rejected；其余 → 可批量核准。

## 汇总

| 层级 | 待复核 | 合格(已核准) | 可疑(留人工) | 判错(已拒绝) |
|---|---:|---:|---:|---:|
| 实体 | 29 | 29 | 0 | 0 |
| 表注释 | 18 | 18 | 0 | 0 |
| 字段注释 | 98 | 95 | 0 | 3 |
| **合计** | **145** | **142** | **0** | **3** |

## 执行结果（2026-10-27 14:02:50）

```
✅ 已核准 142 条（approved + source=manual + 写 audit_log / knowledge_history）
🚫 已拒绝   3 条（rejected + 写审计，严格/非严格模式都不进检索）
```

复核后六层知识状态：

| 知识表 | 总数 | approved | candidate | rejected |
|---|---:|---:|---:|---:|
| 指标 metrics | 48 | 48 | 0 | 0 |
| 字典 dict_entries | 19 | 19 | 0 | 0 |
| 实体 entities | 42 | 42 | 0 | 0 |
| 关系 relationships | 16 | 16 | 0 | 0 |
| 表注释 table_comments | 39 | 39 | 0 | 0 |
| 字段注释 column_comments | 149 | 146 | 0 | 3 |

因严格模式的召回损失 = 3 条且全部是错误的注释，**已把 `GOVERNANCE_STRICT_STATUS` 默认值改为 `true`**。

## 就地修正项（改掉凑数枚举后核准）

- `column_comments` dwd_tu_order_all.rescue_assign_status
  - 修正：「0=无意义」改为「0=未指派」，与 2/3 的语义序列对齐
- `column_comments` ods_tu_large_handcart_order_all.order_type
  - 修正：删除 AI 凑数的「41=其他类型41，42=其他类型42」两项占位枚举

## 人工确认放行项（冲突检测命中但核对无误）

- `column_comments` dwd_tu_order_all.carstate
  - 确认：本表 carstate 是车况（1=能启动 / 2=不能启动），与列名一致，是冲突对中正确的一方

## 可疑项（需人工确认，保留 candidate）

- 无

## 判错项（已置 rejected，不进任何检索）

- `column_comments` ods_tu_order_pricing_record_all.enterprise_customer_type
  - 原因：枚举自相矛盾——「企业客户类型」却写「1=个人」，0/1 两值无法构成企业类型口径，疑为错行写入
- `column_comments` ods_tu_order_pricing_record_all.carstate
  - 原因：整表串味——本表是小板询价表，carstate 应为车况（能启动/不能启动），注释却写成订单流转状态（待派单/已派单/服务中），与 `dwd_tu_order_all.carstate` 的车况口径冲突，属 AI 抽取时误用他表枚举
- `column_comments` ods_tu_driver_all.is_picked
  - 原因：语义矛盾——注释写「司机接单状态(1=正常,2=未认证)」，与字段名 `is_picked` 及同名字段口径（`dwd_tu_order_all.is_picked` = 0否/1是）冲突，疑为 AI 误读他表字段

## 后续动作

1. ~~处理可疑项~~ —— 本轮无遗留可疑项。
2. **判错项回源头修正**：这 3 条已置 `rejected` 并从检索中排除；需修 AI 抽取 Prompt（同名跨表字段要按表校验，不能沿用上一条的枚举）或人工重录。修正后把 `status` 重置为 `candidate` 再走一轮复核。
3. ~~开启严格模式~~ —— 已完成：`config.py` 默认 `true`，索引已重建（31 表 / 48 指标 / 19 字典 / 42 实体 / 16 关系）。
4. **新增知识的常规流程**：AI 产出的知识一律先落 `candidate` → 跑 `python _review_governance.py report` 预检 → `apply` 复核 → 重启服务重建索引。

## 回滚方式

本轮复核前的配置库快照：`agent_config.db.bak-20261027-140106-pre-review`。
如需回滚，停服后把该文件覆盖回 `agent_config.db` 即可（会一并回退到复核前的 candidate 状态）。
