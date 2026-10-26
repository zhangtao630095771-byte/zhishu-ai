"""补充「线上订单」与「调度单」两类订单口径到 P1 指标注册表（Metric Registry）。
口径来源（用户权威定义）：
  线上订单口径 : is_salesman = 0 AND is_service_staff IN (0,4,5)
  调度单      : is_salesman = 1 AND is_service_staff IN (1,2,3)
依赖：is_service_staff / is_salesman 的枚举已由「客服/调度身份」「是否业务员」字典定义。
"""
import config_store as cs

cs._ensure_ready()

# ── 1) 线上订单口径 ──
cs.upsert_metric({
    "name": "线上订单",
    "keywords": [
        "线上订单", "线上订单口径", "线上单", "C端订单", "普通订单",
        "app订单", "用户自主下单", "客户自主下单",
    ],
    "definition": (
        "线上订单 = 客户自主在线上（C端/App）发起、非调度/业务员代发的订单。"
        "判定口径（必须同时满足）：is_salesman = 0 AND is_service_staff IN (0,4,5)。"
        "说明：is_salesman=0 表示非业务员发单；"
        "is_service_staff ∈ {0(不是),4(平台客服),5(代理商)} 表示非各线路调度代发"
        "（1小板调度/2代驾调度/3大板调度/6汽车销售 均不算线上订单）。"
        "统计线上订单相关指标时，WHERE 条件必须同时带上这两个判定。"
    ),
    "doc": (
        "与「调度单」互斥。is_service_staff=6(汽车销售) 不计入线上订单，也不计入调度单。"
        "is_salesman=1 的订单一律不属于线上订单。"
    ),
    "tables": ["dwd_tu_order_all"],
    "enabled": True,
})

# ── 2) 调度单口径 ──
cs.upsert_metric({
    "name": "调度单",
    "keywords": [
        "调度单", "调度订单", "业务员单", "代发单", "调度代发", "业务员代发",
    ],
    "definition": (
        "调度单 = 由业务员/各线路调度代客户发起的订单。"
        "判定口径（必须同时满足）：is_salesman = 1 AND is_service_staff IN (1,2,3)。"
        "说明：is_salesman=1 表示是业务员发单；"
        "is_service_staff ∈ {1(小板调度),2(代驾调度),3(大板调度)} 表示由调度代发。"
        "统计调度单相关指标时，WHERE 条件必须同时带上这两个判定。"
    ),
    "doc": (
        "与「线上订单」互斥。注意 is_service_staff=4(平台客服)/5(代理商)/6(汽车销售) "
        "即使 is_salesman=0 也不满足此口径；is_salesman=0 的订单一律不属于调度单。"
    ),
    "tables": ["dwd_tu_order_all"],
    "enabled": True,
})

# ── 回读确认 ──
print("=== 已写入的指标（含本次两条）===")
for m in cs.get_metrics(enabled_only=False):
    if m["name"] in ("线上订单", "调度单"):
        print(f"  [{ 'ON' if m['enabled'] else 'OFF' }] {m['name']}")
        print(f"      kw={m['keywords']}")
        print(f"      def={m['definition'][:120]}...")
print("\nOK: 两条订单口径已写入 Metric Registry（P1）。")
