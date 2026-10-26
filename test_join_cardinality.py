# -*- coding: utf-8 -*-
"""
test_join_cardinality.py —— JOIN 一对多放大风险专项测试（整改任务 3）

★ 背景（docs/audit/02-critical-issues.md C-01）：
  sql_validator._check_p2 只对「已在知识库登记且 pre_agg_required=True」的关联拦截，
  未登记关联的一对多 JOIN 完全漏检 → 聚合指标被静默放大。

覆盖：
  ① 放大效应实证：临时 SQLite 真实执行，人工核算直接 JOIN vs 预聚合的 COUNT/SUM
  ② 已登记 pre_agg_required 关系：直接 JOIN 拦截（error）、预聚合放行（现有能力回归）
  ③ 未登记关联：直接 JOIN 明细表必须产生 warning 告警（关联基数未确认）——整改前漏检
  ④ 1:1 维表直接 JOIN 不被无差别告警（表名启发式只提示，不阻断）
  ⑤ 预聚合正确 SQL 的执行结果与人工核算一致

运行： .venv/bin/python test_join_cardinality.py   （临时内存 SQLite，不连真实库、不调模型）
"""
import sqlite3

import sql_validator as sv

PASSED, FAILED = [], []


def check(label, got, expected):
    ok = (got == expected)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望: {expected}")
        print(f"       实际: {got}")
    return ok


def check_true(label, cond, detail=""):
    ok = bool(cond)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok and detail:
        print(f"       实际: {detail}")
    return ok


# ═══════════════════════════════════════════════════════════
# 临时测试数据（全部人工可核算）
# ═══════════════════════════════════════════════════════════
def _setup():
    """构建：订单主表(3笔) + 费用明细(2/1/2条) + 状态历史(3/2/1条) + 城市维表(1:1)"""
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE dwd_order (order_id INTEGER, city TEXT, amount REAL, status INTEGER);
        CREATE TABLE dwd_order_fee (fee_id INTEGER, order_id INTEGER, fee_type TEXT, fee_amount REAL);
        CREATE TABLE dwd_order_status_history (id INTEGER, order_id INTEGER, status INTEGER, change_time TEXT);
        CREATE TABLE dim_city (city TEXT, province TEXT);
    """)
    # 人工核算基准：COUNT=3，SUM(amount)=600
    c.executemany("INSERT INTO dwd_order VALUES (?,?,?,?)", [
        (1, "北京", 300.0, 6),
        (2, "上海", 200.0, 6),
        (3, "北京", 100.0, 7),
    ])
    # 费用明细：订单1 两条(50+30)、订单2 一条(20)、订单3 两条(10+5)
    # → 直接 JOIN 后 COUNT=5，SUM(amount)=300*2+200*1+100*2=1000（放大）
    c.executemany("INSERT INTO dwd_order_fee VALUES (?,?,?,?)", [
        (101, 1, "信息费", 50.0),
        (102, 1, "服务费", 30.0),
        (201, 2, "信息费", 20.0),
        (301, 3, "服务费", 10.0),
        (302, 3, "其他", 5.0),
    ])
    # 状态历史：订单1 三条、订单2 两条、订单3 一条 → 直接 JOIN 后 COUNT=6（放大）
    c.executemany("INSERT INTO dwd_order_status_history VALUES (?,?,?,?)", [
        (1, 1, 1, "2026-07-01"), (2, 1, 2, "2026-07-02"), (3, 1, 6, "2026-07-03"),
        (4, 2, 1, "2026-07-04"), (5, 2, 6, "2026-07-05"),
        (6, 3, 1, "2026-07-06"),
    ])
    # 1:1 维表
    c.executemany("INSERT INTO dim_city VALUES (?,?)", [("北京", "华北"), ("上海", "华东")])
    conn.commit()
    return conn


# 与生产 agent_config.db 中 relationships 的 join_sql 实际格式一致（JOIN 片段）
REL_1N = {"join_sql": "ods_valet_order_all o JOIN ods_valet_fee_detail_all f ON o.order_no = f.order_no",
          "cardinality": "1:N", "pre_agg_required": 1}


class _Ctx:
    """_check_p2 只读 ctx.p2_relationships，用最小桩替代 SemanticContext"""
    def __init__(self, rels):
        self.p2_relationships = rels


# ═══════════════════════════════════════════════════════════
# ①⑤ 放大效应实证 + 预聚合正确性（真实执行，人工核算）
# ═══════════════════════════════════════════════════════════
def test_amplification_demo():
    print("\n【1】一对多 JOIN 放大效应实证（人工核算预期值）")
    conn = _setup()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM dwd_order")
    check("基准：订单量 COUNT = 3", cur.fetchone()[0], 3)
    cur.execute("SELECT ROUND(SUM(amount),1) FROM dwd_order")
    check("基准：金额 SUM = 600.0", cur.fetchone()[0], 600.0)
    cur.execute("SELECT COUNT(*) FROM dwd_order o JOIN dwd_order_fee f ON o.order_id = f.order_id")
    check("风险实证：直接 JOIN 费用明细后 COUNT = 5（≠3，被放大）", cur.fetchone()[0], 5)
    cur.execute("SELECT ROUND(SUM(amount),1) FROM dwd_order o JOIN dwd_order_fee f ON o.order_id = f.order_id")
    check("风险实证：直接 JOIN 后 SUM(amount) = 1000.0（≠600，金额重复计）", cur.fetchone()[0], 1000.0)
    cur.execute("SELECT COUNT(*) FROM dwd_order o JOIN dwd_order_status_history h ON o.order_id = h.order_id")
    check("风险实证：直接 JOIN 状态历史后 COUNT = 6（≠3，被放大）", cur.fetchone()[0], 6)
    conn.close()


def test_preagg_correct_result():
    print("\n【2】预聚合写法结果与人工核算一致（无放大）")
    conn = _setup()
    cur = conn.cursor()
    cur.execute("""
        SELECT o.order_id, ROUND(o.amount + COALESCE(t.fee_total, 0), 1)
        FROM dwd_order o
        LEFT JOIN (SELECT order_id, SUM(fee_amount) AS fee_total
                   FROM dwd_order_fee GROUP BY order_id) t
        ON o.order_id = t.order_id
        ORDER BY o.order_id""")
    rows = cur.fetchall()
    check("预聚合后每单金额 = 380/220/115（300+80, 200+20, 100+15）", rows,
          [(1, 380.0), (2, 220.0), (3, 115.0)])
    cur.execute("""
        SELECT COUNT(*) FROM dwd_order o
        LEFT JOIN (SELECT order_id, SUM(fee_amount) AS fee_total
                   FROM dwd_order_fee GROUP BY order_id) t
        ON o.order_id = t.order_id""")
    check("预聚合后 COUNT 仍 = 3（无放大）", cur.fetchone()[0], 3)
    conn.close()


# ═══════════════════════════════════════════════════════════
# ② 已登记关系：现有拦截能力回归（不得被本次整改破坏）
# ═══════════════════════════════════════════════════════════
def test_registered_direct_join_blocked():
    print("\n【3】已登记 pre_agg_required=1：直接 JOIN 仍被 error 拦截")
    issues = []
    sv._check_p2("SELECT COUNT(*) FROM ods_valet_order_all o "
                 "JOIN ods_valet_fee_detail_all f ON o.order_no = f.order_no",
                 _Ctx([REL_1N]), issues)
    errs = [i for i in issues if i["severity"] == "error" and i["rule"] == "join_pre_agg"]
    check_true("直接 JOIN 已登记明细表 → error(join_pre_agg)", bool(errs), issues)


def test_registered_preagg_pass():
    print("\n【4】已登记关系：正确预聚合后通过（不误拦）")
    issues = []
    sv._check_p2("SELECT COUNT(*) FROM ods_valet_order_all o "
                 "LEFT JOIN (SELECT order_no, SUM(x) AS c FROM ods_valet_fee_detail_all "
                 "GROUP BY order_no) f ON o.order_no = f.order_no",
                 _Ctx([REL_1N]), issues)
    check("预聚合后无 error", [i["rule"] for i in issues if i["severity"] == "error"], [])


def test_registered_right_table_not_double_warned():
    print("\n【5】已登记关系的右表不重复叠加「未登记」告警")
    issues = []
    sv._check_p2("SELECT COUNT(*) FROM ods_valet_order_all o "
                 "JOIN ods_valet_fee_detail_all f ON o.order_no = f.order_no",
                 _Ctx([REL_1N]), issues)
    rules = [i["rule"] for i in issues]
    check("无 join_unregistered_risk 重复告警", [r for r in rules if r == "join_unregistered_risk"], [])


# ═══════════════════════════════════════════════════════════
# ③④ 未登记关联：告警（整改核心）与不误伤
# ═══════════════════════════════════════════════════════════
def test_unregistered_direct_join_warned():
    print("\n【6】★ 未登记明细表直接 JOIN → 必须产生 warning（整改前漏检）")
    issues = []
    sv._check_p2("SELECT COUNT(*) FROM dwd_order o "
                 "JOIN dwd_order_fee f ON o.order_id = f.order_id",
                 _Ctx([]), issues)
    warns = [i for i in issues if i.get("rule") == "join_unregistered_risk"]
    check_true("费用明细表（未登记）直接 JOIN 产生 warning", bool(warns), issues)
    check("告警为 warning 级（不无差别阻断执行）",
          sorted({i["severity"] for i in issues}), ["warning"])


def test_unregistered_status_history_warned():
    print("\n【7】未登记状态历史表直接 JOIN → 产生 warning")
    issues = []
    sv._check_p2("SELECT COUNT(*) FROM dwd_order o "
                 "JOIN dwd_order_status_history h ON o.order_id = h.order_id",
                 _Ctx([]), issues)
    warns = [i for i in issues if i.get("rule") == "join_unregistered_risk"]
    check_true("状态历史表（未登记）直接 JOIN 产生 warning", bool(warns), issues)


def test_unregistered_dim_join_no_warning():
    print("\n【8】1:1 维表（dim_，非明细命名）直接 JOIN → 不告警（不无差别阻断）")
    issues = []
    sv._check_p2("SELECT o.order_id, c.province FROM dwd_order o "
                 "JOIN dim_city c ON o.city = c.city",
                 _Ctx([]), issues)
    check("维表 JOIN 无任何告警", [i["rule"] for i in issues], [])


def test_unregistered_preagg_no_warning():
    print("\n【9】未登记但已预聚合的明细表 → 不告警（写法已安全）")
    issues = []
    sv._check_p2("SELECT COUNT(*) FROM dwd_order o "
                 "LEFT JOIN (SELECT order_id, SUM(fee_amount) AS c FROM dwd_order_fee "
                 "GROUP BY order_id) f ON o.order_id = f.order_id",
                 _Ctx([]), issues)
    check("预聚合后无告警", [i["rule"] for i in issues], [])


if __name__ == "__main__":
    print()
    print("╔" + "═" * 56 + "╗")
    print("║" + "JOIN 一对多放大风险 · 专项测试（整改任务 3）".center(40) + "║")
    print("╚" + "═" * 56 + "╝")
    test_amplification_demo()
    test_preagg_correct_result()
    test_registered_direct_join_blocked()
    test_registered_preagg_pass()
    test_registered_right_table_not_double_warned()
    test_unregistered_direct_join_warned()
    test_unregistered_status_history_warned()
    test_unregistered_dim_join_no_warning()
    test_unregistered_preagg_no_warning()
    print()
    print("=" * 58)
    total = len(PASSED) + len(FAILED)
    if not FAILED:
        print(f"🎉 全部通过（{len(PASSED)}/{total}）")
    else:
        print(f"⚠️ 通过 {len(PASSED)}/{total}，失败项：")
        for f in FAILED:
            print(f"   ❌ {f}")
    print("=" * 58)
    raise SystemExit(1 if FAILED else 0)
