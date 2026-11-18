# -*- coding: utf-8 -*-
"""
test_clarification_multimetric.py —— 多轮澄清「多指标共存」专项回归

★ 对应《多轮澄清机制专项修复任务》第七节 12 项测试要求。
  覆盖两块：
    A. 澄清决策路由（clarification.decide）：多指标不再被当成单选选项
    B. 指标公式过滤条件作用域（sql_validator）：分子条件不得污染全局 WHERE / 分母

★ 测试数据说明：
   - decide 用【模拟候选数据】验证机制（显式标注，非生产口径）；
   - sql_validator 用【构造 ctx】验证校验规则，不连数据库；
   - 真实指标知识（发单量/完单量/完单率/接单量/接单率）的 required_predicates 语义
     已在 _fix_metric_predicates.py 中修正，此处用与真实一致的定义文本做校验断言。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import clarification as C          # noqa: E402
import sql_validator               # noqa: E402

_passed = 0
_failed = []


def ok(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name} {detail}")


# ═══════════════════════════════════════════════════════════
# 模拟候选数据（显式标注为测试数据）
# ═══════════════════════════════════════════════════════════
def _M(**kw):
    d = dict(name="", keywords=[], definition="", doc="", tables=[], enabled=True,
             metric_type=None, status="approved", source="manual",
             required_predicates=[], forbidden_predicates=[])
    d.update(kw)
    return d


# 五个独立指标（对应真实指标名，各自唯一正式定义）
MOCK_FIVE = [
    _M(name="发单量", keywords=["发单"], definition="发单量 = COUNT(*)（不限状态）",
       metric_type="count", tables=["dwd_tu_order_all"], forbidden_predicates=["status"]),
    _M(name="完单量", keywords=["完单"], definition="完单量 = COUNT(*) WHERE status=6",
       metric_type="count", tables=["dwd_tu_order_all"], required_predicates=["status = 6"]),
    _M(name="接单量", keywords=["接单"], definition="接单量 = SUM(CASE WHEN is_picked=1 THEN 1 ELSE 0 END)",
       metric_type="count", tables=["dwd_tu_order_all"]),
    _M(name="接单率", keywords=["接单率"], definition="接单率 = SUM(CASE WHEN is_picked=1 THEN 1 ELSE 0 END)*1.0/COUNT(*)，分母为总订单量",
       metric_type="ratio", tables=["dwd_tu_order_all"]),
    _M(name="完单率", keywords=["完单率"], definition="完单率 = COALESCE(SUM(CASE WHEN status=6 THEN 1 ELSE 0 END)*1.0/COUNT(*),0)，分母为全部订单",
       metric_type="ratio", tables=["dwd_tu_order_all"]),
]

# 同一概念「订单量」对应两个可信口径（发单量/完单量），模拟真实歧义
MOCK_AMBIG = [
    _M(name="发单量", keywords=["订单量", "发单"], definition="发单量 = COUNT(*)（不限状态）",
       metric_type="count", tables=["dwd_tu_order_all"], forbidden_predicates=["status"]),
    _M(name="完单量", keywords=["订单量", "完单"], definition="完单量 = COUNT(*) WHERE status=6",
       metric_type="count", tables=["dwd_tu_order_all"], required_predicates=["status = 6"]),
]


# ═══════════════════════════════════════════════════════════
# A. 决策路由：多指标共存
# ═══════════════════════════════════════════════════════════
def test_single_metric_direct():
    # 要求1：单个已审核指标，直接执行，不追问
    d = C.decide("查上个月发单量", {"metrics": ["发单量"]}, MOCK_FIVE)
    ok(d["action"] == C.ACTION_EXECUTE, "1 单指标直接执行", d["reason"])


def test_five_metrics_not_single_select():
    # 要求2：五个已审核指标同时查询，不要求用户选择其中一个
    q = "查询小板订单26年月维度发单量、完单量、接单量、接单率、完单率"
    u = {"metrics": ["发单量", "完单量", "接单量", "接单率", "完单率"]}
    d = C.decide(q, u, MOCK_FIVE)
    ok(d["action"] == C.ACTION_EXECUTE,
       "2 五指标不触发单选澄清", d["reason"])
    names = {c["name"] for c in d["candidates"]}
    ok({"发单量", "完单量", "接单量", "接单率", "完单率"} <= names,
       "2 五个指标全部保留", sorted(names))


def test_llm_named_multi_not_select():
    # 用户未写全名（如「发单完单」），LLM 识别出多个指标 → 一起查，不单选
    q = "查询发单完单数据"
    u = {"metrics": ["发单量", "完单量"]}
    d = C.decide(q, u, MOCK_FIVE)
    ok(d["action"] == C.ACTION_EXECUTE, "2 LLM识别多指标→执行", d["reason"])


def test_genuine_ambiguity_still_clarify():
    # 要求4：同一个指标名称对应多个可信且冲突的定义时，能正确澄清
    d = C.decide("查上个月订单量", {"metrics": []}, MOCK_AMBIG)
    ok(d["action"] == C.ACTION_CLARIFY, "4 同概念多口径→澄清", d["reason"])
    ok({c["name"] for c in d["candidates"]} == {"发单量", "完单量"},
       "4 候选为两个冲突口径", [c["name"] for c in d["candidates"]])


def test_missing_formal_no_fabricate():
    # 要求5：正式定义缺失（candidate）时，不得编造指标公式
    ms = [_M(name="发单量", keywords=["发单"], definition="（未审核）",
             status="candidate", source="ai", enabled=True)]
    d = C.decide("查上个月发单量", {"metrics": []}, ms)
    ok(d["action"] == C.ACTION_UNKNOWN, "5 缺正式定义→停止猜测", d["action"])


def test_answer_keeps_other_conditions():
    # 要求10：回答澄清后，其他条件（时间/过滤）不丢失
    st = C.new_state("u1", "查上个月订单量", {"metrics": []}, MOCK_AMBIG)
    st["query_id"] = "q1"
    st["confirmed_slots"]["time"] = {"expr": "上月", "desc": "上一自然月",
                                     "start": "2026-09-01", "end": "2026-09-30"}
    st, out = C.apply_answer(st, "发单量", MOCK_AMBIG)
    ok(out == "ready", "10 选口径→ready", out)
    ok(st["confirmed_slots"]["metric"] == "发单量", "10 口径已锁")
    ok(st["confirmed_slots"]["time"]["expr"] == "上月", "10 时间条件保留")


# ═══════════════════════════════════════════════════════════
# B. 指标公式过滤条件作用域（sql_validator）
# ═══════════════════════════════════════════════════════════
def _ctx(metrics):
    class _C:
        p1_metrics = metrics
        p3_tables = ["dwd_tu_order_all"]
        p2_relationships = []
        p3_table_objs = [{"name": "dwd_tu_order_all", "columns": "id, order_type, status, is_picked, create_time"}]
        p4_docs = []
    return _C()


def _errors(sql, metrics):
    ctx = _ctx(metrics)
    resolved = {"bindings": {m["name"]: (m["definition"], "P1") for m in metrics}}
    return [i for i in sql_validator.validate_sql(sql, ctx, resolved, ["dwd_tu_order_all"])
            if i["severity"] == "error"]


def _has_rule(errs, rule):
    return any(i.get("rule") == rule for i in errs)


def test_fadan_not_filtered_by_status():
    # 要求6：发单量不被完单状态过滤（forbidden_predicates=["status"]）
    ms = [_M(name="发单量", keywords=["发单"], definition="发单量 = COUNT(*)（不限状态）",
             metric_type="count", tables=["dwd_tu_order_all"], forbidden_predicates=["status"])]
    bad = "SELECT COUNT(*) AS 发单量 FROM dwd_tu_order_all WHERE status=6"
    errs = _errors(bad, ms)
    ok(_has_rule(errs, "metric_forbidden"), "6 发单量加 status 过滤被拦",
       [i.get('rule') for i in errs])


def test_wandan_rate_denominator_protected():
    # 要求7：完单率分母仍为全部订单（status=6 不得进全局 WHERE）
    ms = [_M(name="完单率", keywords=["完单率"],
             definition="完单率 = COALESCE(SUM(CASE WHEN status=6 THEN 1 ELSE 0 END)*1.0/COUNT(*),0)，分母为全部订单",
             metric_type="ratio", tables=["dwd_tu_order_all"])]
    good = ("SELECT SUM(CASE WHEN status=6 THEN 1 ELSE 0 END)*1.0/COUNT(*) AS r "
            "FROM dwd_tu_order_all WHERE order_type=1")
    errs = _errors(good, ms)
    ok(not _has_rule(errs, "ratio_denominator"), "7 完单率条件聚合不被误拦",
       [i.get('rule') for i in errs])
    bad = ("SELECT SUM(CASE WHEN status=6 THEN 1 ELSE 0 END)*1.0/COUNT(*) AS r "
           "FROM dwd_tu_order_all WHERE status=6")
    errs2 = _errors(bad, ms)
    ok(_has_rule(errs2, "ratio_denominator"), "7 完单率 status 进 WHERE 被拦",
       [i.get('rule') for i in errs2])


def test_jiedan_rate_denominator_protected():
    # 要求8：接单率分母仍为全部订单（is_picked=1 不得进全局 WHERE）
    ms = [_M(name="接单率", keywords=["接单率"],
             definition="接单率 = SUM(CASE WHEN is_picked=1 THEN 1 ELSE 0 END)*1.0/COUNT(*)，分母为总订单量",
             metric_type="ratio", tables=["dwd_tu_order_all"])]
    bad = ("SELECT SUM(CASE WHEN is_picked=1 THEN 1 ELSE 0 END)*1.0/COUNT(*) AS r "
           "FROM dwd_tu_order_all WHERE is_picked=1")
    errs = _errors(bad, ms)
    ok(_has_rule(errs, "ratio_denominator"), "8 接单率 is_picked 进 WHERE 被拦",
       [i.get('rule') for i in errs])


def test_jiedan_amount_not_global_filter():
    # 要求9：接单量的 is_picked=1 是条件聚合，不会成为全局过滤
    #   （修正后接单量 required_predicates 已清空，正确 SQL 用 CASE WHEN，不报 metric_required）
    ms = [_M(name="接单量", keywords=["接单"],
             definition="接单量 = SUM(CASE WHEN is_picked=1 THEN 1 ELSE 0 END)",
             metric_type="count", tables=["dwd_tu_order_all"])]
    good = ("SELECT SUM(CASE WHEN is_picked=1 THEN 1 ELSE 0 END) AS 接单量 "
            "FROM dwd_tu_order_all WHERE order_type=1")
    errs = _errors(good, ms)
    ok(not _has_rule(errs, "metric_required"), "9 接单量条件聚合不报全局过滤缺失",
       [i.get('rule') for i in errs])


def test_wan_dan_liang_global_filter_kept():
    # 对照：完单量的 status=6 是【真正的全局过滤】，required_predicates 保留
    ms = [_M(name="完单量", keywords=["完单"],
             definition="完单量 = COUNT(*) WHERE status=6",
             metric_type="count", tables=["dwd_tu_order_all"],
             required_predicates=["status = 6"])]
    good = "SELECT COUNT(*) AS 完单量 FROM dwd_tu_order_all WHERE status=6"
    errs = _errors(good, ms)
    ok(not _has_rule(errs, "metric_required"), "对照 完单量 status=6 全局过滤满足",
       [i.get('rule') for i in errs])


def main():
    for fn in (test_single_metric_direct, test_five_metrics_not_single_select,
               test_llm_named_multi_not_select, test_genuine_ambiguity_still_clarify,
               test_missing_formal_no_fabricate, test_answer_keeps_other_conditions,
               test_fadan_not_filtered_by_status, test_wandan_rate_denominator_protected,
               test_jiedan_rate_denominator_protected, test_jiedan_amount_not_global_filter,
               test_wan_dan_liang_global_filter_kept):
        try:
            fn()
        except Exception as e:
            import traceback
            _failed.append(f"{fn.__name__} 异常：{e}\n{traceback.format_exc()}")
    print("=" * 60)
    if _failed:
        print(f"❌ 失败 {len(_failed)} 项（通过 {_passed} 项）")
        for f in _failed:
            print("  ❌", f)
        return 1
    print(f"🎉 全部通过（{_passed} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
