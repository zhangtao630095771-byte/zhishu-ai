# S3 整改报告：实体数据契约与主表合并

> 时间：2026-10-19
> 阶段：S3（任务 6 的核心）
> 目标：P2 命中实体后，其主表必须进入 P3 与最终 Prompt；司机问题转为固定回归用例。

---

## 1. 结论

1. 新增 `entity_table_name(entity)` 单点，兼容「裸实体」与「索引包装项」两种形态，消除“拆几层”靠猜的问题。
2. 修 `SemanticContext.all_table_names()`：P2 实体主表现在能正确并入 P3。
3. 司机问题真实链路已可答：P3 包含 `ods_tu_driver_all`，比例字段进入 Prompt，返回 23 种组合。
4. 顺带修掉一个被真实案例暴露的 P0 误报：`AS t GROUP BY "中文别名"` 被误判成裸中文别名。
5. 知识库已标记「信息费提升比例」口径待确认，模型回答时会显式声明假设。

---

## 2. 代码改动

### 2.1 统一取主表入口

`semantic_layer.py` 新增：

```python
def entity_table_name(entity):
    if not isinstance(entity, dict):
        return None
    return entity.get("table_name") or (entity.get("raw") or {}).get("table_name")
```

替换了 3 处手工取值：

| 位置 | 原写法 | 问题 |
|---|---|---|
| `all_table_names()` | `e.get("raw", {}).get("table_name")` | 对裸实体二次解包 → None（司机表丢失） |
| P1 前置 `wanted_tables_early` | `(e or {}).get("raw", {}).get("table_name")` | 依赖包装形态 |
| P3 实体过滤 `tbl_owner` | `(e.get("raw") or {}).get("table_name")` | 依赖包装形态 |

### 2.2 修复主表合并

`all_table_names()` 改为：

```python
for e in self.p2_entities:
    t = entity_table_name(e)
    if t and t not in names:
        names.append(t)
```

### 2.3 修复 P0 别名误报

`sql_validator._check_p0` 原正则允许空格：

```python
r"\bas\s+([^\n,`(]+?)\s*(?:,|from|$|\))"
```

会把 `AS t GROUP BY "信息费默认比例"` 整段当作别名，误报“裸中文别名”。改为只取 AS 后第一个 token：

```python
r"\bas\s+([^\s,()]+)"
```

---

## 3. 修改前后证据

### 3.1 检索层

**修改前**

```
P2: ['司机信息表']
P3: ['ods_valet_fee_detail_all', ..., 'ods_valet_order_all']   ← 无司机表
```

**修改后（真实索引）**

```
P2: ['司机信息表']
P3: [..., 'ods_tu_driver_all']
司机字段注入:
  default_information_fee_ratio   信息费默认比例
  activation_reduce_ratio         信息费降低比例
```

### 3.2 端到端问数

```
ok: True | pipeline: v2 | attempts: 1
rows: 23
SQL 使用 ods_tu_driver_all，按三个比例字段 GROUP BY
```

结果示例（前三）：

```
(0.00, 0.00, 0.00)  162,849
(10.00, 0.00, 0.00) 135,688
(10.00, 0.00, 3.00)  36,571
```

### 3.3 口径标记

知识库新增文档「信息费三类比例口径（待确认）」，并更新字段注释：

> 司机额外服务费比例（是否等同「信息费提升比例」待业务确认；当前绝大多数司机为 0）

模型实际回答已显式声明：

> 司机表无权威「信息费提升比例」字段，暂以 `extra_information_fee` 作为提升比例口径，属假设口径。

---

## 4. 测试

| 测试 | S2 后 | S3 后 |
|---|---|---|
| `test_gate.py` | 95/95 | **103/103** |
| 其余 unit/config/integration | 全绿 | 全绿 |

新增用例 `test_entity_main_table_merged`：

- 裸实体 / 包装实体都能取到同一主表；
- P3 只召回噪声表时，实体主表仍能并入；
- `p3_table_objs` 包含司机表，比例字段进入上下文。

另新增两条 P0 别名用例：子查询别名不误报、裸中文别名仍拦截。

---

## 5. 兼容性与风险

- `entity_table_name` 只做读取，不改变实体存储结构，旧数据无需迁移。
- `all_table_names` 行为变化：P2 实体的主表现在一定会进 P3（预期收益）；P3 表数可能 +1~2，Prompt 略增。
- 知识库新增一条文档与字段注释修改，可通过后台删除/编辑。
- **仍未解决**：P1 仍会召回「大板信息费 / 代驾信息费 / 司机补缴信息费次数」三个金额类指标，属于 S4 的召回降噪；本次未改，避免与实体修复混在一起验证。
- 回答中的“提升比例”仍是假设口径，业务确认前不得当作权威结论。

---

## 6. 下一步（S4/S5）

1. S4：按问题类型区分金额/比例/状态/计数指标，减少无关指标挤占 P3；用司机问题做前后对比。
2. S5：补齐 Golden 用例（标准 SQL + 期望组合数/字段），把“提升比例”口径确认结果写回知识库。
3. 监控：记录每次 P2/P3 命中与最终 Prompt 表清单，便于发现“实体命中但主表未进上下文”的回归。
