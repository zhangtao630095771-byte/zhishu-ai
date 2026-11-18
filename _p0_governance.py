# -*- coding: utf-8 -*-
"""
_p0_governance.py —— P0-1 业务域与资产治理（幂等、可重复执行）

★ 作用：
  1. 补齐业务主题骨架：order 域补「订单交易」、valet 域补「代驾经营」。
  2. 生成「未归类资产」的归类建议报告（只读，不自动归类 —— 归类需人工确认）。

★ 原则：
  - 只做【无争议、可逆、增补】的操作；命名/语义判断只出建议、不落库。
  - 全程走 config_store，自动写 knowledge_history 审计。
"""
import config_store

# 表名前缀 → (建议域, 建议主题, 置信度, 依据说明)
# 依据现有 7 个 data_domains 与 5 个 business_subjects；未建域/主题的标 None。
_TABLE_HINT = [
    ("ods_valet", "valet", "valet_operation", "high", "valet 前缀=代驾业务"),
    ("dwd_valet", "valet", "valet_operation", "high", "valet 前缀=代驾业务"),
    ("ods_tu_driver", "driver", "driver_operation", "high", "driver 前缀=司机资源"),
    ("dwd_tu_driver", "driver", "driver_operation", "high", "driver 前缀=司机资源"),
    ("ods_driver", "driver", "driver_operation", "high", "driver 前缀=司机资源"),
    ("ods_tu_large_handcart", "large_handcart", "大板订单", "high", "large_handcart 前缀=大板拖运"),
    ("ods_tu_mall", "order", "order_trade", "medium", "商城订单归属订单交易域"),
    ("dwd_tu_mall", "order", "order_trade", "medium", "商城订单归属订单交易域"),
    ("ods_tu_order", "order", "order_trade", "medium", "order 前缀=订单交易"),
    ("dwd_tu_order", "order", "order_trade", "medium", "order 前缀=订单交易"),
    ("ods_tu_guaranty", "order", "order_trade", "medium", "保障/担保订单=订单交易的一种"),
    ("ods_violation", "order", "risk_governance", "high", "违规/处罚=订单域下风控治理主题"),
    ("ods_offline_order", "order", "order_trade", "low", "线下订单属订单域，需确认"),
    ("ods_ctc", "customer", "customer_operation", "high", "客户积分=客户与会员域"),
    ("ods_tu_customer", "customer", "customer_operation", "high", "客户表=客户与会员域"),
    ("ods_user_register", "customer", "customer_operation", "high", "用户注册=客户与会员域"),
    ("dws_day_user_login", "customer", "customer_operation", "high", "用户登录活跃=客户与会员域"),
    ("ods_sys", None, None, "low", "系统表无独立系统域，需人工判断是否纳入"),
]


def add_subjects():
    """补齐主题骨架（幂等）。"""
    existing_domains = {d["name"] for d in config_store.list_data_domains()}
    # ① 客户与会员域（用户确认新建）
    if "customer" not in existing_domains:
        config_store.upsert_data_domain("customer", "客户与会员", "客户/会员/积分/注册/登录活跃等客户侧资产", owner="", enabled=1)
        print("  ✓ 新增域「customer」（客户与会员）")
    else:
        print("  = 域「customer」已存在，跳过")

    existing = {s["name"] for s in config_store.list_business_subjects()}
    plan = [
        ("order_trade", "order", "订单交易",
         "订单交易域核心主题：订单创建 / 履约 / 取消 / 转卖 / 保障等交易全流程"),
        ("valet_operation", "valet", "代驾经营",
         "代驾业务域核心主题：代驾订单 / 费用 / 司机激活 / 保证金等代驾经营"),
        ("customer_operation", "customer", "客户经营",
         "客户与会员域核心主题：客户 / 积分 / 注册 / 登录活跃等客户侧资产"),
        ("risk_governance", "order", "风控治理",
         "订单域下风控治理主题：违规信息 / 违规处罚 / 转卖审核等治理类资产"),
    ]
    for name, domain, display, desc in plan:
        if name in existing:
            print("  = 主题「%s」已存在，跳过" % name)
            continue
        config_store.upsert_business_subject(name, domain, display, desc, enabled=1)
        print("  ✓ 新增主题「%s」（域=%s，%s）" % (name, domain, display))


def suggest(subject_of=None):
    """生成未归类资产归类建议，返回结构化清单。"""
    entities = config_store.get_entities()
    metrics = config_store.get_metrics(enabled_only=True)

    # 已归类 key
    import sqlite3
    con = sqlite3.connect("agent_config.db")
    classified = set()
    for t, k in con.execute("SELECT asset_type, asset_key FROM topic_assets"):
        classified.add((t, k))
    con.close()

    def hint_of(table_name):
        if not table_name:
            return (None, None, "low", "无物理表")
        for prefix, dom, subj, conf, why in _TABLE_HINT:
            if table_name.startswith(prefix):
                return (dom, subj, conf, why)
        return (None, None, "low", "表名无已知前缀，需人工判断")

    ent_rows = []
    for e in entities:
        if not e.get("enabled", True):
            continue
        if ("entity", e["name"]) in classified:
            continue
        dom, subj, conf, why = hint_of(e.get("table_name"))
        ent_rows.append({
            "type": "实体", "name": e["name"], "table": e.get("table_name") or "",
            "domain": dom, "subject": subj, "confidence": conf, "why": why,
        })

    metric_rows = []
    for m in metrics:
        if ("metric", m["name"]) in classified:
            continue
        tables = m.get("tables") or []
        dom, subj, conf, why = hint_of(tables[0] if tables else "")
        metric_rows.append({
            "type": "指标", "name": m["name"],
            "table": "、".join(tables) if tables else "",
            "domain": dom, "subject": subj, "confidence": conf, "why": why,
        })
    return ent_rows + metric_rows


def write_report(rows):
    lines = [
        "# 未归类资产归类建议（P0-1）",
        "",
        "> 生成时间：2026-11-17　本清单【只建议、不自动归类】，确认后在语义工作台批量归类。",
        "",
        "## 统计",
        "",
        "- 未归类实体：%d 个" % sum(1 for r in rows if r["type"] == "实体"),
        "- 未归类指标：%d 个" % sum(1 for r in rows if r["type"] == "指标"),
        "",
        "## 归类建议（按置信度排序）",
        "",
        "| 类型 | 名称 | 物理表/依赖表 | 建议域 | 建议主题 | 置信度 | 依据 |",
        "|---|---|---|---|---|---|---|",
    ]
    order = {"high": 0, "medium": 1, "low": 2}
    for r in sorted(rows, key=lambda x: (order[x["confidence"]], x["name"])):
        dom = r["domain"] or "（需新建域）"
        subj = r["subject"] or "（需新建主题）"
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["type"], r["name"], r["table"], dom, subj, r["confidence"], r["why"]))
    lines += [
        "",
        "## 需人工决策项",
        "",
        "1. **客户类资产**（ods_ctc_* / ods_tu_customer_* / 用户登录活跃）：当前无「客户与会员」域，需决定新建该域或归入营销域。",
        "2. **系统表**（ods_sys_user_all）：是否纳入知识库，还是作为技术表忽略。",
        "3. **违规/转卖类**（ods_violation_*）：归订单域还是新建「风控/治理」主题。",
        "4. **语义歧义实体**（4 组，见去重脚本 REVIEW）：保障/担保、转卖审核/违规、线下定位三视角。",
    ]
    with open("docs/未归类资产归类建议_P0-1.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("建议报告已写入 docs/未归类资产归类建议_P0-1.md")


def main():
    config_store.init_store()
    print("① 补齐主题骨架：")
    add_subjects()
    print("\n② 生成未归类建议：")
    rows = suggest()
    write_report(rows)
    print("   未归类实体 %d、指标 %d" % (
        sum(1 for r in rows if r["type"] == "实体"),
        sum(1 for r in rows if r["type"] == "指标")))


if __name__ == "__main__":
    main()
