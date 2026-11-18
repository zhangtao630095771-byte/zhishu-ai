# -*- coding: utf-8 -*-
"""
补种「代驾司机保证金」指标定义 + 两张表的字段注释（幂等、可重复执行）

★ 背景
  用户查询"代驾司机保证金的缴纳数/金额、转出人数/金额"被澄清闸门拦截
  （"未检索到可信指标定义，停止猜测"）。根因有二：
    ① metrics 表没有任何"保证金"相关指标定义（澄清闸门只认指标，不认表名）
    ② ods_valet_deposit_all / ods_valet_driver_deposit_transfer_out_all
       两张表在 column_comments 里没有任何字段注释

★ 本脚本（用 config_store 官方写入入口，走完整治理：history / version / status）
    ① 录入 5 个指标（缴纳数 / 缴纳司机数 / 缴纳金额 / 转出人数 / 转出金额）
    ② 补两张表的字段注释（金额 / 司机 / 状态 / 时间等关键字段）

★ 口径（已与用户确认，2026-11-06）
    缴纳数     = 订单数 COUNT(*)                  （不过滤 status，全量缴纳订单）
    缴纳司机数 = 去重司机数 COUNT(DISTINCT driver)
    缴纳金额   = SUM(total)
    转出人数   = 去重司机数 COUNT(DISTINCT driver)，仅「已实际转出」
    转出金额   = SUM(deposit_amount)，仅「已实际转出」
    · 「实际转出」判定 = status = 1（数据强证据：status=1 与 transfer_out_time 有值严格一一对应）

★ 数据依据（实测 ClickHouse，2026-11-06）
    ods_valet_deposit_all:
      status=1 → 17736 笔 / 8486 司机 / sum(total)=6,297,102（paytime=0、paytype=0）
      status=2 →  9048 笔 / 8797 司机 / sum(total)=  518,502（paytime>0、paytype∈{1,2,3}）
    ods_valet_driver_deposit_transfer_out_all:
      status=1 →  594 条 / 499 司机 / sum(deposit_amount)=417,000.89（transfer_out_time 有值）
      status=0 →   97 条 /  61 司机 / 88,000
      status=2 →  303 条 / 158 司机 / 222,002

运行： .venv/bin/python _seed_deposit_metrics.py
"""
import config_store as cs

OPERATOR = "admin"

# ── ① 5 个指标定义 ──
#   required_predicates：转出两指标强制 status=1（实际转出）；缴纳三指标不过滤（全量）。
METRICS = [
    {
        "name": "代驾司机保证金缴纳数",
        "keywords": ["保证金缴纳数", "保证金缴纳订单数", "缴纳数", "缴纳订单数", "保证金缴纳笔数"],
        "definition": (
            "代驾司机保证金缴纳数 = COUNT(*)\n"
            "  ★ 表 ods_valet_deposit_all，一行 = 一笔保证金缴纳订单\n"
            "  ★ 统计全部缴纳订单（含 status=1 与 status=2，不做状态过滤）\n"
            "  ★ 司机字段 driver，金额字段 total"
        ),
        "doc": "代驾司机保证金缴纳数：司机缴纳保证金的订单总笔数（含全部状态）。",
        "tables": ["ods_valet_deposit_all"],
        "metric_type": "count",
    },
    {
        "name": "代驾司机保证金缴纳司机数",
        "keywords": ["保证金缴纳司机数", "缴纳司机数", "保证金缴纳人数", "缴纳司机", "缴纳人数"],
        "definition": (
            "代驾司机保证金缴纳司机数 = COUNT(DISTINCT driver)\n"
            "  ★ 表 ods_valet_deposit_all，司机字段 driver\n"
            "  ★ 统计缴纳过保证金的去重司机数（含全部 status）"
        ),
        "doc": "代驾司机保证金缴纳司机数：缴纳过保证金的去重司机数。",
        "tables": ["ods_valet_deposit_all"],
        "metric_type": "count",
    },
    {
        "name": "代驾司机保证金缴纳金额",
        "keywords": ["保证金缴纳金额", "缴纳金额", "保证金金额", "缴纳总额"],
        "definition": (
            "代驾司机保证金缴纳金额 = SUM(total)\n"
            "  ★ 表 ods_valet_deposit_all，金额字段是 total（Decimal(10,2)），该表没有 amount 列\n"
            "  ★ 统计全部缴纳金额（含全部 status）"
        ),
        "doc": "代驾司机保证金缴纳金额：司机缴纳保证金的金额总和（字段 total）。",
        "tables": ["ods_valet_deposit_all"],
        "metric_type": "amount",
    },
    {
        "name": "代驾司机保证金转出人数",
        "keywords": ["保证金转出人数", "转出人数", "保证金转出司机数", "转出司机数", "保证金转出"],
        "definition": (
            "代驾司机保证金转出人数 = COUNT(DISTINCT driver)\n"
            "  ★ 表 ods_valet_driver_deposit_transfer_out_all，司机字段 driver\n"
            "  ★ 仅统计「已实际转出」（status=1，即 transfer_out_time 有值）"
        ),
        "doc": "代驾司机保证金转出人数：已实际转出保证金的去重司机数（status=1）。",
        "tables": ["ods_valet_driver_deposit_transfer_out_all"],
        "metric_type": "count",
        "required_predicates": ["status = 1"],
    },
    {
        "name": "代驾司机保证金转出金额",
        "keywords": ["保证金转出金额", "转出金额", "转出总额", "保证金转出"],
        "definition": (
            "代驾司机保证金转出金额 = SUM(deposit_amount)\n"
            "  ★ 表 ods_valet_driver_deposit_transfer_out_all，金额字段是 deposit_amount\n"
            "  ★ 仅统计「已实际转出」（status=1，即 transfer_out_time 有值）\n"
            "  ★ 用户明确口径：取「实际转出」，不用「预计转出」"
        ),
        "doc": "代驾司机保证金转出金额：已实际转出（status=1）的保证金金额总和（字段 deposit_amount）。",
        "tables": ["ods_valet_driver_deposit_transfer_out_all"],
        "metric_type": "amount",
        "required_predicates": ["status = 1"],
    },
]

# ── ② 两张表的字段注释 ──
COLUMNS = {
    "ods_valet_deposit_all": {
        "id": "保证金缴纳订单主键，一行 = 一笔缴纳订单",
        "driver": "司机ID（>0 为有效司机）",
        "depositno": "保证金订单编号",
        "paytype": "支付方式（Int8，取值 0/1/2/3）",
        "paytime": "支付时间（Unix 时间戳，0 表示未支付）",
        "total": "保证金缴纳金额（Decimal(10,2)）",
        "status": "付款状态（Int8，取值 1/2）",
        "create_time": "创建时间",
        "update_time": "更新时间",
    },
    "ods_valet_driver_deposit_transfer_out_all": {
        "id": "转出申请主键，一行 = 一条转出申请",
        "code": "转出申请编号",
        "driver": "司机ID",
        "status": "转出状态：1=已实际转出（transfer_out_time 有值）；0/2=未实际转出",
        "estimate_transfer_out_time": "预计转出时间",
        "transfer_out_time": "实际转出时间（有值 = 已实际转出）",
        "create_time": "创建时间",
        "update_time": "更新时间",
        "cause_type": "转出原因类型",
        "transfer_out_reason": "转出原因",
        "first_transfer_out_state": "首次转出状态",
        "deposit_amount": "转出保证金金额（Decimal(10,2)）",
    },
}


def main():
    cs._ensure_ready()

    print("═" * 66)
    print("① 录入指标定义")
    print("═" * 66)
    for m in METRICS:
        ok, msg = cs.upsert_metric(dict(m), operator=OPERATOR, source="manual")
        mark = "✅" if ok else "⚠️"
        print(f"  {mark} {m['name']}  →  {msg}")
        if m.get("required_predicates"):
            print(f"       required_predicates = {m['required_predicates']}")

    print()
    print("═" * 66)
    print("② 补字段注释")
    print("═" * 66)
    for tbl, cols in COLUMNS.items():
        for cname, comment in cols.items():
            n = cs.set_column_comment(tbl, cname, comment,
                                      source="manual", operator=OPERATOR)
            print(f"  {'✅' if n else '⚠️'} {tbl}.{cname}  ({n} 行)")

    print()
    print("完成。")
    print("★ 澄清闸门（_clarify_metrics）实时读库，录入后立即生效；")
    print("★ RAG 取数链路需【重建索引 / 重启服务】后，新指标与字段注释才注入 prompt。")


if __name__ == "__main__":
    main()
