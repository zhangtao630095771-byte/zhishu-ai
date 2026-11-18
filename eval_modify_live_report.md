# 增量修改 · 真实模型回归报告

- 生成时间：2026-10-30 10:38:48
- 模型：deepseek-chat（https://api.deepseek.com/v1）
- 模式：真实模型调用
- 场景数：15（①~⑩ 增量修改 10 个 + E1~E5 需求一致性 5 个）
- 人工断言：15/15 场景整体通过
- 审核器 `req_pass_rate`：15/15（100.0%）
- 审核器 `false_block_rate`：0/15（0.0%，上线门槛 < 5%）

## 说明
本报告只验证【真实模型在增量修改约束下的 SQL 生成】是否符合预期；
SQL 未对真实库执行（执行前仍会走完整安全闸门，已由 test_modify_query.py 覆盖）。

判定口径：所有断言都跑在【剥注释 + 压空白 + 等号归一】后的正文上（修 G8 —— 老版本会把注释里的错误字段名当成「还没改」）。
审核器口径：只跑 `requirement_review` 确定性层（`use_llm=False`），语义层需额外一次模型调用，不纳入本报告成本。

## ① 删分组维度（去掉应用主体分组，只按月份）
**修改问句**：去掉应用主体分组，只按月份统计

**模型生成的 SQL**：
```sql
SELECT toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 0 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `app_entity_source = 0`
- ✅ 必须保留 `toStartOfMonth`
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `user_type = 1`
- ✅ 必须保留 `2025-10-01`
- ✅ 必须保留 `2026-11-01`

**结构判定**：
- ✅ 不再按应用主体分组（GROUP BY 键里没有该字段）
- ✅ 仍有 GROUP BY

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## ② 增指标（加上完成率）
**修改问句**：再加上完成率

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数", sumIf(r.is_complete = 1) / count(*) AS "完成率" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 0 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `完成率`
- ✅ 必须保留 `user_type = 1`
- ✅ 必须保留 `app_entity_source = 0`
- ✅ 必须保留 `toStartOfMonth`
- ✅ 必须保留 `2025-10-01`

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## ③ 改指标（换成注册司机数）
**修改问句**：把注册客户数改成注册司机数

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册司机数" FROM ctc_data_warehouse.register r WHERE r.user_type = 2 AND r.app_entity_source = 0 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `注册司机数`
- ✅ 必须保留 `user_type = 2`
- ✅ 必须保留 `2025-10-01`
- ✅ 必须保留 `app_entity_source = 0`

**契约 · 必须消失**：
- ✅ 必须消失 `user_type = 1`

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## ④ 改时间范围（结束时间到 2026-12-01）
**修改问句**：时间范围改成到 2026-12-01

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 0 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-12-01' GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `2026-12-01`
- ✅ 必须保留 `2025-10-01`
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `app_entity_source = 0`
- ✅ 必须保留 `user_type = 1`

**契约 · 必须消失**：
- ✅ 必须消失 `2026-11-01`

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## ⑤ 改过滤（app_entity_source 0 → 101）
**修改问句**：应用主体只看 101，其它不变

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 101 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `app_entity_source = 101`
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `toStartOfMonth`
- ✅ 必须保留 `2025-10-01`
- ✅ 必须保留 `user_type = 1`

**契约 · 必须消失**：
- ✅ 必须消失 `app_entity_source = 0`

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## ⑥ 删过滤（去掉 app_entity_source 过滤）
**修改问句**：去掉应用主体等于 0 这个过滤条件

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `2025-10-01`
- ✅ 必须保留 `user_type = 1`
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `toStartOfMonth`

**契约 · 必须消失**：
- ✅ 必须消失 `app_entity_source = 0`

**审核器裁决**：`pass`（req_dim_not_removed）
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## ⑦ 改统计口径（count → count distinct id）
**修改问句**：注册客户数要按去重客户 id 统计

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", uniqExact(r.user_id) AS "注册客户数" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 0 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `app_entity_source = 0`
- ✅ 必须保留 `2025-10-01`
- ✅ 必须保留 `user_type = 1`

**结构判定**：
- ✅ 含 DISTINCT / uniqExact 等去重口径

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## ⑧ 报错纠错（基于错误信息修正）
**修改问句**：SQL 报错：DB::Exception: Unknown identifier r.app_entily_source。帮我改对字段名

**模型生成的 SQL**：
```sql
-- 修正字段名拼写错误：app_entily_source → app_entity_source
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数"
FROM ctc_data_warehouse.register r
WHERE r.user_type = 1
  AND r.app_entity_source = 0
  AND r.create_time >= '2025-10-01'
  AND r.create_time < '2026-11-01'
GROUP BY r.app_entity_source, toStartOfMonth(r.create_time)
ORDER BY toStartOfMonth(r.create_time)
FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `app_entity_source`
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `2025-10-01`

**契约 · 必须消失**：
- ✅ 必须消失 `app_entily_source`

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## ⑨ 追加问句（再加一个指标，需保留原指标）
**修改问句**：这个查询不错，再帮我加一个完成率

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数", sumIf(r.is_complete = 1) / count(*) AS "完成率" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 0 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `完成率`
- ✅ 必须保留 `app_entity_source = 0`

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## ⑩ 改分组粒度（按月份 → 按周）
**修改问句**：按周统计，不要按月份了

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfWeek(r.create_time) AS "周", count(*) AS "注册客户数" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 0 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY r.app_entity_source, toStartOfWeek(r.create_time) ORDER BY toStartOfWeek(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `toStartOfWeek`
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `app_entity_source = 0`
- ✅ 必须保留 `2025-10-01`

**契约 · 必须消失**：
- ✅ 必须消失 `toStartOfMonth`

**审核器裁决**：`pass`（req_dim_not_removed）
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## E1 极简改过滤（只说「只看 101」）
**修改问句**：只看 101

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 101 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `app_entity_source = 101`
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `toStartOfMonth`
- ✅ 必须保留 `user_type = 1`

**契约 · 必须消失**：
- ✅ 必须消失 `app_entity_source = 0`

**结构判定**：
- ✅ 仍按应用主体分组（用户没要求删分组）

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## E2 删分组维度但必须留住等值过滤
**修改问句**：去掉应用主体分组

**模型生成的 SQL**：
```sql
SELECT toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 0 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `app_entity_source = 0`
- ✅ 必须保留 `toStartOfMonth`
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `2025-10-01`

**结构判定**：
- ✅ GROUP BY 键里没有 app_entity_source
- ✅ 仍有 GROUP BY（不能退化成一行汇总）

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## E3 增指标（保留原指标）
**修改问句**：再加上完成率

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数", sumIf(r.is_complete = 1) / count(*) AS "完成率" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 0 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `完成率`
- ✅ 必须保留 `toStartOfMonth`
- ✅ 必须保留 `user_type = 1`
- ✅ 必须保留 `app_entity_source = 0`

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## E4 口径切换（代驾 → 大板，不留残留过滤）
**修改问句**：不要看代驾了，只看大板

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 201 AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `app_entity_source = 201`
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `toStartOfMonth`
- ✅ 必须保留 `user_type = 1`

**契约 · 必须消失**：
- ✅ 必须消失 `app_entity_source = 102`

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过

## E5 指代消解（「上面那个查询，时间改到上个月」）
**修改问句**：上面那个查询，时间改到上个月

**模型生成的 SQL**：
```sql
SELECT r.app_entity_source AS "应用主体", toStartOfMonth(r.create_time) AS "月份", count(*) AS "注册客户数" FROM ctc_data_warehouse.register r WHERE r.user_type = 1 AND r.app_entity_source = 0 AND r.create_time >= toStartOfMonth(now() - INTERVAL 1 MONTH) AND r.create_time < toStartOfMonth(now()) GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ORDER BY toStartOfMonth(r.create_time) FORMAT TabSeparatedWithNames
```

**契约 · 必须保留**：
- ✅ 必须保留 `注册客户数`
- ✅ 必须保留 `toStartOfMonth`
- ✅ 必须保留 `app_entity_source = 0`
- ✅ 必须保留 `user_type = 1`

**契约 · 必须消失**：
- ✅ 必须消失 `2025-10-01`
- ✅ 必须消失 `2026-11-01`

**结构判定**：
- ✅ 仍是左闭右开封闭区间（>= 与 < 都在）

**审核器裁决**：`pass`
- ✅ 能通过字段级权限解析（方言感知，原 bug 已修复）

**结论**：✅ 通过
