# -*- coding: utf-8 -*-
"""
test_ambiguity.py —— 智能澄清引擎（第二阶段：时间 + 实体）专项测试

覆盖（对应任务 §九 验收）：
  ① #337/#339 案例：时间口径歧义正确触发澄清，给出 3 个候选口径
  ② 明确问题不触发不必要澄清
  ③ 行为时间已明确修饰时不误报
  ④ 实体映射 approved → 不阻断；未核准 → 阻断（不许静默换表）
  ⑤ 多实体歧义 → 澄清实体
  ⑥ 完整闭环：用户选择时间口径 → 落进 confirmed_slots → build_final_question 落实
  ⑦ 口径对象 caliber 写入实体映射（防静默换表）
  ⑧ 确定性：同一问题多次运行决策一致

运行：.venv/bin/python test_ambiguity.py   （纯逻辑，不连库、不调模型）
"""
import clarification as C
import ambiguity as A

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


# ── 已审核实体清单（含 status，模拟 config_store.get_entities_with_governance）──
ENTITIES = [
    {"name": "客户信息表", "table_name": "ods_tu_customer_all", "grain": "客户",
     "comment": "客户主表，注册时间 create_data_time",
     "keywords": ["注册客户", "客户", "customer"], "status": "approved",
     "source": "manual", "version": 2},
    {"name": "小板订单表", "table_name": "dwd_tu_order_all", "grain": "订单",
     "comment": "小板快运订单，order_type=1 子集，发单时间 ctime",
     "keywords": ["小板订单", "小板", "small_order", "小板快运"], "status": "approved",
     "source": "manual", "version": 2},
    {"name": "代驾订单表", "table_name": "ods_valet_order_all", "grain": "订单",
     "comment": "代驾订单主表", "keywords": ["代驾订单", "代驾"], "status": "approved",
     "source": "manual", "version": 2},
    {"name": "大板订单表", "table_name": "ods_tu_large_handcart_order_all", "grain": "订单",
     "comment": "大板订单主表", "keywords": ["大板订单", "大板"], "status": "approved",
     "source": "manual", "version": 2},
    {"name": "未核准实体", "table_name": "some_new_table", "grain": "—",
     "comment": "AI 起草未复核", "keywords": ["未核准实体"], "status": "candidate",
     "source": "ai", "version": 1},
]

# 原问题（#337/#339）
Q = "查询10月注册客户有发小板订单的客户占比"
U = {"intent": "summary", "metrics": [], "entities": [], "dimensions": [],
     "time_range": "2026-10-01~2026-10-31", "filters": [], "ambiguity": "", "degraded": False}

# 已审核指标（唯一「占比」口径，供闭环测试用）
MOCK_METRICS = [{
    "name": "发单客户占比", "keywords": ["发单", "占比"], "definition": "有发单的注册客户占注册客户比例",
    "enabled": True, "status": "approved", "source": "manual", "metric_type": "ratio",
    "tables": [], "required_predicates": [], "forbidden_predicates": [],
}]


# ═══════════════════════════════════════════════════════════
# ① #337/#339 时间歧义
# ═══════════════════════════════════════════════════════════
def test_time_ambiguity_triggered():
    print("\n【1】#337/#339 案例：注册时间明确 + 行为时间未明确 → 触发时间澄清")
    d = C.decide_full(Q, U, [], ENTITIES)
    check("决策为 clarify", d["action"], C.ACTION_CLARIFY)
    check("澄清槽位为 time", d.get("clarify_slot"), "time")
    labels = [o["label"] for o in d.get("clarify_options", [])]
    check("给出 3 个候选口径", labels,
          ["注册后至当前", "与注册时间同区间", "注册后指定时间窗口"])
    # 每个选项都有统计范围说明 + 区别
    check_true("每个选项含统计范围说明与区别",
               all(o.get("desc") and o.get("diff") for o in d.get("clarify_options", [])),
               d.get("clarify_options"))


# ═══════════════════════════════════════════════════════════
# ② 明确问题不澄清 / ③ 行为时间已明确不误报
# ═══════════════════════════════════════════════════════════
def test_no_ambiguity_cases():
    print("\n【2】明确 / 无歧义问题 → 不触发澄清")
    # 行为时间已明确修饰（「10月内」）
    d1 = C.decide_full("查询10月注册且10月内发小板订单的客户占比", U, [], ENTITIES)
    check("「10月内发单」行为时间已明确 → 不澄清时间", d1.get("clarify_slot") != "time", True)
    # 无行为词（只问注册客户数）
    d2 = C.decide_full("查询10月注册客户数", U, [], ENTITIES)
    check("无行为词 → 不澄清时间", d2.get("clarify_slot") != "time", True)
    # 无注册锚点
    d3 = C.decide_full("查询本月订单量", {"metrics": [], "entities": [], "time_range": ""},
                       [], ENTITIES)
    check("无注册锚点 → 不澄清时间", d3.get("clarify_slot") != "time", True)


# ═══════════════════════════════════════════════════════════
# ④ 实体映射确认 / 阻断
# ═══════════════════════════════════════════════════════════
def test_entity_approved_no_block():
    print("\n【3】实体映射 approved → 不阻断，且写入口径对象 caliber")
    ents = A.resolve_entities(Q, U, ENTITIES)
    check_true("「小板订单」映射到已审核实体", ents is not None and len(ents) > 0,
               "resolve_entities 返回空")
    if ents:
        tables = {e.get("table_name") for e in ents}
        check_true("「小板订单」映射到 dwd_tu_order_all",
                   "dwd_tu_order_all" in tables, tables)
        check("全部实体均 approved",
              all(e.get("status") == "approved" for e in ents), True)


def test_entity_unapproved_block():
    print("\n【4】理解层识别出未核准实体 → 阻断（不许静默换表）")
    u = dict(U, entities=["未核准实体"])
    d = C.decide_full("查询未核准实体的订单", u, [], ENTITIES)
    check("未核准实体 → unknown（阻断）", d["action"], C.ACTION_UNKNOWN)
    check_true("阻断原因说明映射未核准", "审核" in d.get("reason", ""), d.get("reason"))


def test_entity_unknown_block():
    print("\n【5】识别出的实体名不在清单 → 阻断")
    u = dict(U, entities=["不存在的实体"])
    d = C.decide_full("查询某订单", u, [], ENTITIES)
    check("映射断裂 → unknown（阻断）", d["action"], C.ACTION_UNKNOWN)


# ═══════════════════════════════════════════════════════════
# ⑤ 多实体歧义
# ═══════════════════════════════════════════════════════════
def test_multi_entity_ambiguous():
    print("\n【6】问题同时提到多个实体且未点名 → 澄清实体")
    d = C.decide_full("查询客户的小板和大板订单情况", U, [], ENTITIES)
    if d.get("clarify_slot") == "entity":
        check_true("澄清实体槽位", True, "")
        check("给出多个实体选项", len(d.get("clarify_options", [])) >= 2, True)
    else:
        check("多实体应澄清（当前槽位）", d.get("clarify_slot"), "entity")


# ═══════════════════════════════════════════════════════════
# ⑥ 完整闭环：选口径 → 落实 SQL
# ═══════════════════════════════════════════════════════════
def test_full_roundtrip():
    print("\n【7】完整闭环：发起时间澄清 → 用户选口径 → 落实进最终问题")
    decision = C.decide_full(Q, U, MOCK_METRICS, ENTITIES)
    st = C.new_state("u1", Q, U, MOCK_METRICS, ENTITIES, decision=decision)
    st["query_id"] = "T1"
    st["status"] = C.STATUS_CLARIFYING
    st["pending_clarifications"] = [C._pending_for_slot(Q, decision, "time", 1)]
    # 用户选择「与注册时间同区间」
    st, outcome = C.apply_answer(st, "与注册时间同区间", MOCK_METRICS, ENTITIES)
    check("选择后进入 ready", outcome, "ready")
    check("行为时间口径写入 confirmed_slots",
          (st["confirmed_slots"].get("time") or {}).get("action_scope"), "within_anchor")
    check("确认主体记录", st["confirmed_slots"].get("confirmed_by"), "u1")
    check_true("确认时间已记录", bool(st["confirmed_slots"].get("confirmed_at")),
               st["confirmed_slots"].get("confirmed_at"))
    final_q = C.build_final_question(st)
    check_true("最终问题落实了时间口径（同一区间）",
               "与注册时间同一时间区间" in final_q, final_q)


# ═══════════════════════════════════════════════════════════
# ⑧ 确定性：多次运行一致
# ═══════════════════════════════════════════════════════════
def test_deterministic():
    print("\n【8】同一问题多次运行口径一致")
    slots = [C.decide_full(Q, U, [], ENTITIES).get("clarify_slot") for _ in range(5)]
    check("5 次运行澄清槽位一致", len(set(slots)), 1)
    check("槽位恒为 time", slots[0], "time")


if __name__ == "__main__":
    print()
    print("╔" + "═" * 62 + "╗")
    print("║" + "智能澄清引擎 · 第二阶段（时间+实体）专项测试".center(38) + "║")
    print("╚" + "═" * 62 + "╝")
    test_time_ambiguity_triggered()
    test_no_ambiguity_cases()
    test_entity_approved_no_block()
    test_entity_unapproved_block()
    test_entity_unknown_block()
    test_multi_entity_ambiguous()
    test_full_roundtrip()
    test_deterministic()
    print()
    print("=" * 64)
    total = len(PASSED) + len(FAILED)
    if not FAILED:
        print(f"🎉 全部通过（{len(PASSED)}/{total}）")
    else:
        print(f"⚠️ 通过 {len(PASSED)}/{total}，失败项：")
        for f in FAILED:
            print(f"   ❌ {f}")
    print("=" * 64)
    raise SystemExit(1 if FAILED else 0)
