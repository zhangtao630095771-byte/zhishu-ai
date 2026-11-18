# 07 · 统一语义注册表 semantic_registry 落地（P2 第一步）

> 落地日期：2026-11-10 · 状态：已完成并验收通过
> 脚本：`_seed_semantic_registry.py`（幂等，全量重建，纯标准库）

---

## 1. 定位与设计

七类知识分散在 7 张表，主键形态、状态字段、层级都不同。`semantic_registry` 是它们的**统一登记口**：Semantic Context Builder 只读这一张表，就能拿到「某类知识有哪些、稳定 ID、层级、状态、哈希」。

- **事实源仍是 7 张原表**，本表只是只读投影（派生注册表，每次 `DELETE + INSERT` 全量重建）。
- **stable_key 直接复用**各表已回填的稳定 ID（P0 阶段已回填到 100%）。
- 与 `metric_constraint` 的区别：`metric_constraint` 是纯派生、无消费者；`semantic_registry` 是给后续 Builder 的**正向依赖**（未来 `dimension/concept/process` 等新维度也可登记进来，VIEW 装不下它们）。

---

## 2. 表结构

```sql
CREATE TABLE semantic_registry (
    stable_key      TEXT PRIMARY KEY,   -- 统一稳定 ID（复用各表 stable_key）
    entity_type     TEXT NOT NULL,      -- metric/entity/relationship/dict/table_comment/column_comment/kb_doc
    knowledge_level INTEGER NOT NULL,   -- 1~4（P1~P4）
    display_name    TEXT NOT NULL,      -- 人类可读名
    source_table    TEXT NOT NULL,      -- 源表名
    source_pk       TEXT NOT NULL,      -- 源表主键（字符串化，便于回查原表）
    status          TEXT,               -- approved/candidate/rejected
    source          TEXT,               -- manual/ai
    enabled         INTEGER DEFAULT 1,
    content_hash    TEXT,               -- 内容哈希（漂移检测）
    updated_at      TEXT,
    created_at      TEXT
);
-- 索引：idx_sr_type(entity_type) / idx_sr_level(knowledge_level)
--      idx_sr_status(status) / idx_sr_src_table(source_table)
```

---

## 3. 填充结果（验收通过）

| 项 | 值 |
|---|---|
| 总行数 | **443**（与 7 表源数据完美对齐，无遗漏无多余） |
| 分类型 | metric 57 / entity 52 / relationship 22 / dict 23 / table_comment 46 / column_comment 233 / kb_doc 10 |
| 分层级 | P1 57 / P2 74 / P3 302 / P4 10 |
| 分状态 | approved 376 / candidate 64 / rejected 3 |
| stable_key 重复 | **0**（PRIMARY KEY + 显式自检双保险） |

`source_pk` 字符串化约定：`column_comment` 用 `表名|字段名`（`|` 分隔，因表名/字段名可能含 `.`）；其余用 `name` 或 `CAST(id AS TEXT)`。

---

## 4. 后续接入点（Semantic Context Builder）

注册表是「数据侧」落地，**尚未接入取数链路**（未改 `agent.py` / `semantic_layer.py`，取数逻辑零改动）。建议下一步：

1. `semantic_layer.enrich_index` 之后，用注册表做「跨层统计与告警」（如 P1 层 candidate 数量、某知识层级漂移）；
2. 新增 `dimension/concept` 维度时，直接 `INSERT` 进注册表 + 建对应源表；
3. `Semantic Context Builder`（建议独立 `semantic_builder.py`）以注册表为单一查询入口构建上下文。

**边界**：注册表只登记元数据（ID/类型/层级/状态/哈希），**不承载口径正文**；口径仍从原表读，避免双事实源。

---

## 5. 已知边界

- `kb_doc` 类无 `content_hash`（kb_docs 表缺该字段，本次未补，登记为 NULL）。
- 注册表为派生表，源表知识变更后需重跑 `_seed_semantic_registry.py` 同步（与 `metric_constraint` 同理，建议纳入「知识变更后重建」清单）。
- 当前为物化表（非实时 VIEW），后续若追求实时性可改为 VIEW + 单独扩展表（`semantic_registry_extra`）承载新维度。
