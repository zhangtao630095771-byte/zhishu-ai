# -*- coding: utf-8 -*-
"""
一次性修正脚本：修复 ods_tu_record_all <-> ods_tu_record_type_all 的错误关联
（原错误：t.id = r.id ；正确：t.code = r.type）

只做与本问题相关的知识/配置修正，不动检索与流水线主逻辑。
写入全部走 config_store 官方接口。
"""
import config_store

# ───────────────────────────────────────────────────────────
# 1) 关联规则：把 id=5 的错误 join_sql 就地改对
#    保留原 from/to（保证 upsert 命中同一条记录、不产生重复行）
# ───────────────────────────────────────────────────────────
print("== before relationships ==")
for r in config_store.get_relationships(enabled_only=False):
    if "record" in (r["from_entity"] + r["to_entity"]):
        print(r)

config_store.upsert_relationship({
    "from_entity": "ods_tu_record_type_all",
    "to_entity": "ods_tu_record_all",
    "join_sql": "ods_tu_record_all r LEFT JOIN ods_tu_record_type_all t ON t.code = r.type",
    "join_type": "LEFT",
    "cardinality": "1:N",
    "pre_agg_required": False,
    "rule_note": (
        "★唯一正确关联：流水表 r.type（类型编码）= 类型表 t.code（类型编码）。"
        "r.id 是流水记录自身主键、t.id 是类型表自身主键，二者无关，严禁 t.id = r.id；"
        "类型表【没有 type 列】。1 条类型对应 N 条流水。"
    ),
    "enabled": True,
})
print("== after relationships ==")
for r in config_store.get_relationships(enabled_only=False):
    if "record" in (r["from_entity"] + r["to_entity"]):
        print(r)

# ───────────────────────────────────────────────────────────
# 2) 表注释：修正 ods_tu_record_type_all 首行误导（id → code）
# ───────────────────────────────────────────────────────────
config_store.set_table_comment(
    "ods_tu_record_type_all",
    "code对应 ods_tu_record_all.type（不是 id）。\n"
    "收支/资金流水类型配置数据，定义各流水类型的名称(title)、收支方向、利润标识"
    "及在客户端、司机端、订单等渠道的展示规则。"
    "业务关联列是 code（唯一），勿用本表 id 关联流水表。",
    source="manual",
)

# ───────────────────────────────────────────────────────────
# 3) 字段注释
# ───────────────────────────────────────────────────────────
# 3.1 ods_tu_record_all.type —— 已有且正确，补上 JOIN 写法
config_store.set_column_comment(
    "ods_tu_record_all", "type",
    "流水类型编码，取值对应 ods_tu_record_type_all.code（不是 id）。"
    "如 187=缴纳代驾审核激活费、188=代驾审核激活费退回。"
    "按类型统计时 JOIN 条件为 t.code = r.type。",
    source="manual",
)
# 3.2 ods_tu_record_all.id —— 新增：明确它是记录主键、与类型表无关
config_store.set_column_comment(
    "ods_tu_record_all", "id",
    "流水记录【自身主键】（Int32，全表唯一）。与类型表 ods_tu_record_type_all.id 无任何关系，"
    "严禁用它关联类型表（历史错误写法 t.id = r.id 即由此而来）。",
    source="manual",
)
# 3.3 ods_tu_record_type_all.code —— 强化说明
config_store.set_column_comment(
    "ods_tu_record_type_all", "code",
    "流水类型【编码】（Int32，全表唯一），业务关联列。"
    "ods_tu_record_all.type 取值对应本列（不是 id）。JOIN 条件：t.code = r.type。",
    source="manual",
)
# 3.4 ods_tu_record_type_all.id —— 新增：仅本表内部使用
config_store.set_column_comment(
    "ods_tu_record_type_all", "id",
    "类型表【自身主键】（Int32，全表唯一），仅供类型表内部使用。"
    "勿与 ods_tu_record_all.id 关联；流水表按类型关联应使用本表 code 列。",
    source="manual",
)

# ───────────────────────────────────────────────────────────
# 4) 实体：修正「资金收支流水记录」的误导注释，并补关键词
# ───────────────────────────────────────────────────────────
def upsert_entity(name, table_name, grain, comment, keywords, enabled=True):
    ok, why = config_store.upsert_entity({
        "name": name, "table_name": table_name, "grain": grain,
        "comment": comment, "keywords": keywords, "enabled": enabled,
    }, source="manual")
    print(f"  entity {name!r}: {ok} / {why}")

upsert_entity(
    "资金收支流水记录", "ods_tu_record_all", "一行一条流水记录",
    "资金流水记录明细表。★关联类型表只用【编码】：本表 type 对应 "
    "ods_tu_record_type_all.code（不是 id），JOIN 条件 t.code = r.type；"
    "本表 id 是流水记录主键，与类型表 id 无关，严禁 t.id = r.id。"
    "时间字段 ctime（秒级 Unix 时间戳），金额 money，司机 driver，客户 customer。",
    ["资金收支流水", "资金收支流水记录", "资金流水记录", "流水记录", "record", "资金收支"],
)
upsert_entity(
    "流水记录", "ods_tu_record_all", "一行=一条流水记录",
    "资金流水记录表。按类型统计时用 type 关联类型表编码：t.code = r.type"
    "（type 对应 code，不是 id；本表 id 是记录主键，勿关联类型表 id）。",
    ["流水记录", "record", "资金流水"],
)
upsert_entity(
    "流水记录类型", "ods_tu_record_type_all", "一行=一种流水记录类型",
    "资金流水类型配置表。业务关联列是 code（唯一）：ods_tu_record_all.type = 本表.code。"
    "本表 id 为内部主键，勿用于关联流水表。",
    ["流水记录类型", "record_type", "资金流水类型", "流水类型"],
)

# ───────────────────────────────────────────────────────────
# 5) 新增 P4 口径文档（不改动已有的 P4 文档 3 / 4）
# ───────────────────────────────────────────────────────────
config_store.upsert_kb_doc({
    "title": "资金流水按【类型】统计的正确关联口径（ods_tu_record_all.type = ods_tu_record_type_all.code）",
    "priority": 1,
    "enabled": True,
    "keywords": [
        "资金流水", "流水类型", "不同类型", "资金流水统计", "流水统计",
        "record_type", "ods_tu_record_all", "ods_tu_record_type_all",
    ],
    "content": (
        "【正确关联方式 · 关键】\n"
        "按「不同类型」统计资金流水时，唯一正确的 JOIN 是【编码相等】：\n"
        "  FROM ctc_data_warehouse.ods_tu_record_all AS r\n"
        "  LEFT JOIN ctc_data_warehouse.ods_tu_record_type_all AS t ON t.code = r.type\n"
        "\n"
        "★ 只用 code 关联，禁止出现 t.id = r.id：\n"
        "  · r.id 是【流水记录自身主键】（Int32，量级可达数十万），与类型表无关；\n"
        "  · t.id 是【类型表自身主键】，只在类型表内部有意义；\n"
        "  · r.type 对应的是 t.code（类型编码），不是 t.id；\n"
        "  · 类型表【没有 type 列】，任何 \"t.type = r.type\" 的写法都会直接报 "
        "Missing columns 错误。\n"
        "\n"
        "【为什么容易写错】\n"
        "  两张表各有一个 id 列，且类型表同时存在 id 与 code 两列"
        "（实测 210 行中仅 5 行 id=code，205 行 id≠code）。按 id 关联会让绝大多数行"
        "匹配不上 → 分组仍在，但类型名 title 全为空、标签丢失。\n"
        "\n"
        "【推荐写法】\n"
        "  SELECT t.title AS 流水类型, count() AS 笔数, sum(r.money) AS 金额\n"
        "  FROM ctc_data_warehouse.ods_tu_record_all AS r\n"
        "  LEFT JOIN ctc_data_warehouse.ods_tu_record_type_all AS t ON t.code = r.type\n"
        "  WHERE r.ctime >= toUnixTimestamp(toDateTime('2026-09-01 00:00:00'))\n"
        "    AND r.ctime <  toUnixTimestamp(toDateTime('2026-10-01 00:00:00'))\n"
        "  GROUP BY t.title ORDER BY 金额 DESC\n"
        "\n"
        "【实测校验】2026-09：按 r.type 分组共 115 组 / 250,804 条流水；"
        "code 唯一（210/210），JOIN 前后行数一致（不放大）。"
    ),
})

print("\n== 写入完成，回读校验 ==")
for r in config_store.get_relationships(enabled_only=False):
    if "record" in (r["from_entity"] + r["to_entity"]):
        print("REL:", r)
print("COL type:", config_store.get_column_comments("ods_tu_record_all"))
print("COL code:", config_store.get_column_comments("ods_tu_record_type_all"))
print("TABLE cmt:", config_store.get_table_comments().get("ods_tu_record_type_all"))
for d in config_store.get_kb_docs():
    if "正确关联口径" in d["title"]:
        print("KB:", d["id"], d["title"], "priority=", d["priority"])
