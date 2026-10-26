"""补充业务实体(P2)与指标(P1)：客户/司机/大板/小板/代驾 的中文业务名 → 真实表 + 过滤口径。
目标：让「根据客户信息表，司机信息表和大板，小板，代驾订单表 查询2025年全年的注册客户数…」能被正确路由与计算。
"""
import config_store as cs

cs._ensure_ready()

# ══════════════ P2 实体：中文业务名 → 真实表 + 关键字段/过滤口径 ══════════════
entities = [
    {
        "name": "客户信息表",
        "table_name": "ods_tu_customer_all",
        "grain": "客户",
        "comment": "客户主表（注册客户数来源）。客户id字段=id(Int64)；注册时间字段=create_data_time(DateTime)。"
                   "统计「注册客户数」按 toYear(create_data_time)=年份 过滤。",
        "keywords": ["客户信息表", "客户表", "注册客户", "客户", "customer"],
    },
    {
        "name": "司机信息表",
        "table_name": "ods_tu_driver_all",
        "grain": "司机",
        "comment": "司机主表（注册司机数来源）。司机id字段=id(Int64)；注册时间字段=create_data_time(DateTime)。"
                   "统计「注册司机数」按 toYear(create_data_time)=年份 过滤。",
        "keywords": ["司机信息表", "司机表", "注册司机", "司机", "driver"],
    },
    {
        "name": "大板订单表",
        "table_name": "ods_tu_large_handcart_order_all",
        "grain": "订单",
        "comment": "大板（大板车）订单主表。发单客户字段=customer_id(Int64)；发单时间字段=order_time(DateTime，订单时间/发单时间)。"
                   "统计「大板发单」按 toYear(order_time)=年份 过滤。",
        "keywords": ["大板订单", "大板订单表", "大板", "large_handcart", "大板车订单"],
    },
    {
        "name": "小板订单表",
        "table_name": "dwd_tu_order_all",
        "grain": "订单",
        "comment": "小板快运订单。注意：小板订单不是独立表，而是统一订单表 dwd_tu_order_all 中 order_type=1 的子集"
                   "（1=小板快运，2=拖车救援，请勿误用2）。发单客户字段=customer(Int64)；"
                   "发单时间字段=ctime(Int32 秒级unix时间戳，用 toDateTime(ctime) 转换) 或 ex_create_time(DateTime)。"
                   "统计「小板发单」必须加条件 order_type=1 且 toYear(toDateTime(ctime))=年份。",
        "keywords": ["小板订单", "小板订单表", "小板", "小板快运", "small_order", "小板车订单"],
    },
    {
        "name": "代驾订单表",
        "table_name": "ods_valet_order_all",
        "grain": "订单",
        "comment": "代驾订单主表。发单客户字段=customer_id(Int64)；发单时间字段=order_time(DateTime，发单时间)。"
                   "统计「代驾发单」按 toYear(order_time)=年份 过滤。",
        "keywords": ["代驾订单", "代驾订单表", "代驾", "valet", "代驾订单"],
    },
]

# ══════════════ P1 指标：权威口径（注入 Prompt 标注 MUST，模型不得自行猜测） ══════════════
metrics = [
    {
        "name": "注册客户数",
        "keywords": ["注册客户数", "注册客户", "新增客户", "新注册客户"],
        "definition": "指定时间段内新注册的客户数。口径：COUNT(DISTINCT id) FROM ods_tu_customer_all "
                      "WHERE toYear(create_data_time) = <年份>（或 create_data_time BETWEEN 起 AND 止）。"
                      "注册时间固定取客户表 create_data_time 字段，不要用 utime/last_login_time。",
        "doc": "客户注册量，按注册时间口径。",
        "tables": ["ods_tu_customer_all"],
        "enabled": True,
    },
    {
        "name": "注册司机数",
        "keywords": ["注册司机数", "注册司机", "新增司机", "新注册司机"],
        "definition": "指定时间段内新注册的司机数。口径：COUNT(DISTINCT id) FROM ods_tu_driver_all "
                      "WHERE toYear(create_data_time) = <年份>（或 create_data_time BETWEEN 起 AND 止）。"
                      "注册时间固定取司机表 create_data_time 字段。",
        "doc": "司机注册量，按注册时间口径。",
        "tables": ["ods_tu_driver_all"],
        "enabled": True,
    },
    {
        "name": "发单客户数",
        "keywords": ["发单客户数", "发单客户", "下单客户数", "发单用户数"],
        "definition": "指定时间段内，在大板/小板/代驾任一业务线发过单的去重客户数（只要在当年发过单即计入，不区分是否完单）。"
                      "口径：将三张订单表的发单客户id取 UNION 后 COUNT(DISTINCT)："
                      "①大板 ods_tu_large_handcart_order_all.customer_id，按 toYear(order_time)=年份；"
                      "②小板 dwd_tu_order_all.customer（必须加 order_type=1），按 toYear(toDateTime(ctime))=年份；"
                      "③代驾 ods_valet_order_all.customer_id，按 toYear(order_time)=年份。"
                      "注意三表客户id字段名不一致（customer_id / customer），UNION 时统一别名；用 UNION 而非 UNION ALL 去重。",
        "doc": "跨业务线发单客户去重，按发单时间口径。",
        "tables": ["ods_tu_large_handcart_order_all", "dwd_tu_order_all", "ods_valet_order_all"],
        "enabled": True,
    },
]

# ── 写入 ──
for e in entities:
    cs.upsert_entity(e)
for m in metrics:
    cs.upsert_metric(m)

# ── 回读确认 ──
print("=== 已写入实体 ===")
for e in cs.get_entities():
    print(f"  {e['name']:8s} -> {e['table_name']:38s} | {e['comment'][:40]}")
print("\n=== 已写入指标 ===")
for m in cs.get_metrics(enabled_only=False):
    if m["name"] in ("注册客户数", "注册司机数", "发单客户数"):
        print(f"  {m['name']:8s} enabled={m.get('enabled')} tables={m.get('tables')}")
print("\nDONE")
