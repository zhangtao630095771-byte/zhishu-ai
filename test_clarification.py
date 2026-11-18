# -*- coding: utf-8 -*-
"""
test_clarification.py —— 多轮澄清交互（Query Clarification）测试

★ 对应方案第七节 16 个场景 + 验收标准。纯逻辑 + 临时库 + 端点三层：
   · 决策路由 / 回答合并 / 状态机 —— 纯逻辑，用【模拟候选数据】跑（已显式标注）
   · 会话持久化 + 多任务隔离 + 归属校验 —— 临时库
   · 端点行为（直接执行/澄清/取消/失败/越权）—— TestClient + 打桩，不连 LLM/ClickHouse

★ 模拟数据说明（方案 §5 / §7 允许）：下列候选是【测试数据】，只用于验证机制，
   绝不在生产展示或保存为真实业务口径。真实口径以 metrics 表（approved）为准。
"""
import os
import sys
import secrets

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import clarification as C    # noqa: E402
import config_store          # noqa: E402

_passed = 0
_failed = []


def ok(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name} {detail}")


# ═══════════════════════════════════════════════════════════
# 模拟候选数据（显式标记为测试数据）
# ═══════════════════════════════════════════════════════════
def _METRIC(**kw):
    """快速构造指标对象（默认 approved/manual，可覆盖）。"""
    d = dict(name="", keywords=[], definition="", doc="", tables=[], enabled=True,
             metric_type=None, status="approved", source="manual",
             required_predicates=[], forbidden_predicates=[])
    d.update(kw)
    return d

# 场景2：同一业务词「订单量」有两个可信口径（发单量/完单量）
MOCK_AMBIGUOUS = [
    _METRIC(name="发单量", keywords=["订单量", "发单"],
            definition="发单量 = COUNT(*)（发单≠完单，统计所有发起订单，不限状态）",
            metric_type="count", tables=["dwd_order_all"], forbidden_predicates=["status"]),
    _METRIC(name="完单量", keywords=["订单量", "完单"],
            definition="完单量 = COUNT(*) WHERE status=6（仅已完成订单）",
            metric_type="count", tables=["dwd_order_all"], required_predicates=["status = 6"]),
]

# 场景1/4：唯一可信定义
MOCK_UNIQUE = [
    _METRIC(name="发单量", keywords=["订单量", "发单"],
            definition="发单量 = COUNT(*)（发单≠完单）",
            metric_type="count", tables=["dwd_order_all"], forbidden_predicates=["status"]),
]

# 场景3/12：发单量无正式定义（只有未审核 candidate，或干脆没有）
MOCK_MISSING = [
    _METRIC(name="发单量", keywords=["订单量"], definition="（未审核）",
            status="candidate", source="ai", enabled=True),
]

# 一个金额指标（用于「知识不足」判定）
MOCK_AMOUNT = [
    _METRIC(name="GMV", keywords=["成交金额"], definition="GMV = SUM(amount) WHERE status=6",
            metric_type="amount", tables=["dwd_order_all"]),
]


# ═══════════════════════════════════════════════════════════
# ① 决策路由
# ═══════════════════════════════════════════════════════════
def test_scenario1_unique_direct():
    # 指标唯一且定义可信 → 直接执行，不追问
    d = C.decide("查上个月发单量", {"metrics": []}, MOCK_UNIQUE)
    ok(d["action"] == C.ACTION_EXECUTE, "1 指标唯一 → 直接执行", d["reason"])
    d2 = C.decide("查GMV", {"metrics": ["GMV"]}, MOCK_AMOUNT)
    ok(d2["action"] == C.ACTION_EXECUTE, "1 点名唯一指标 → 直接执行")


def test_scenario2_ambiguous_clarify():
    # 同一业务词多个可信口径 → 澄清
    d = C.decide("查上个月订单量", {"metrics": []}, MOCK_AMBIGUOUS)
    ok(d["action"] == C.ACTION_CLARIFY, "2 多口径 → 澄清", d["reason"])
    ok({c["name"] for c in d["candidates"]} == {"发单量", "完单量"},
       "2 候选集正确", [c["name"] for c in d["candidates"]])


def test_scenario3_missing_refuse():
    # 用户要的指标无正式定义 → 拒绝猜测
    d = C.decide("查上个月发单量", {"metrics": []}, MOCK_MISSING)
    ok(d["action"] == C.ACTION_UNKNOWN, "3 无正式定义 → 停止猜测", d["action"])
    # 金额意图但无定义
    d2 = C.decide("查上个月爽约赔付金额", {"metrics": []}, MOCK_AMOUNT)
    ok(d2["action"] == C.ACTION_UNKNOWN, "3 具体指标无定义 → unknown", d2["reason"])


def test_scenario12_unreviewed_not_formal():
    # 未审核知识不能冒充正式定义：candidate 不进候选
    ok(C.approved_metrics(MOCK_MISSING) == [], "12 candidate 不算可信口径")
    cands, _ = C.resolve_metric_candidates("查上个月发单量", MOCK_MISSING)
    ok(cands == [], "12 未审核不进候选")


# ═══════════════════════════════════════════════════════════
# ② 澄清问题生成
# ═══════════════════════════════════════════════════════════
def test_build_clarification():
    d = C.decide("查上个月订单量", {"metrics": []}, MOCK_AMBIGUOUS)
    cl = C.build_clarification("查上个月订单量", d["candidates"], "metric", 1)
    ok(cl["slot"] == "metric" and len(cl["options"]) == 2, "澄清含两个候选")
    ok(all(o["definition"] and o["status"] == "approved" for o in cl["options"]),
       "每个候选项带正式定义与审核状态")
    ok(cl["allow_cancel"] and cl["allow_free_text"], "允许取消 + 自由文本")


# ═══════════════════════════════════════════════════════════
# ③ 回答合并（状态机）
# ═══════════════════════════════════════════════════════════
def _state(q="查上个月订单量", metrics=MOCK_AMBIGUOUS):
    st = C.new_state("u1", q, {"metrics": []}, metrics)
    st["query_id"] = "q-" + secrets.token_hex(4)
    return st


def test_scenario4_answer_locks_metric():
    st = _state()
    st, out = C.apply_answer(st, "发单量", MOCK_AMBIGUOUS)
    ok(out == "ready", "4 选发单量 → ready", out)
    ok(st["confirmed_slots"]["metric"] == "发单量", "4 已确认条件写入 metric")
    ov = C.build_understanding_override(st)
    ok(ov["metrics"] == ["发单量"], "4 override 锁死指标")


def test_scenario5_keep_other_conditions():
    # 补充地区条件 → 保留原指标与时间
    st = _state()
    st["confirmed_slots"]["metric"] = "发单量"
    st, out = C.apply_answer(st, "只看临沂", MOCK_AMBIGUOUS)
    ok(st["confirmed_slots"]["metric"] == "发单量", "5 补充过滤后指标保留")
    ok("临沂" in (st["confirmed_slots"].get("filters") or []), "5 过滤已追加")


def test_scenario6_update_time():
    # 修改统计时间 → 更新 time，保留指标
    st = _state()
    st["confirmed_slots"]["metric"] = "发单量"
    st, out = C.apply_answer(st, "改成上周", MOCK_AMBIGUOUS)
    ok(out == "ready", "6 改时间 → ready", out)
    ok((st["confirmed_slots"]["time"] or {}).get("expr") == "上周", "6 时间已更新")
    ok(st["confirmed_slots"]["metric"] == "发单量", "6 指标保留")


def test_scenario7_free_text_merge():
    # 自由文本识别并合并有效条件
    st = _state()
    st, out = C.apply_answer(st, "发单量，只看已完成", MOCK_AMBIGUOUS)
    ok(out == "ready" and st["confirmed_slots"]["metric"] == "发单量",
       "7 自由文本点名指标合并")


def test_scenario8_other_goes_to_knowledge():
    st = _state()
    st, out = C.apply_answer(st, "其他", MOCK_AMBIGUOUS)
    ok(out == "needs_knowledge", "8 其他 → 补充说明流程", out)


def test_scenario9_cancel():
    st = _state()
    st, out = C.apply_answer(st, "取消", MOCK_AMBIGUOUS)
    ok(out == "cancelled" and st["status"] == C.STATUS_CANCELLED, "9 取消 → 不再执行 SQL")


def test_scenario10_round_cap():
    # 达到轮次上限 → 转知识治理，不无限追问
    st = _state()
    st["clarification_round"] = C.MAX_CLARIFY_ROUNDS
    st["confirmed_slots"]["metric"] = None
    st, out = C.apply_answer(st, "发单量", MOCK_AMBIGUOUS)
    # 选了候选后应 ready；这里测「仍无法消歧」的封顶分支：直接对 _finalize 断言
    st2 = _state()
    st2["clarification_round"] = C.MAX_CLARIFY_ROUNDS
    st2["confirmed_slots"]["metric"] = None
    st2["intent"]["metrics"] = []
    st2, out2 = C._finalize(st2, MOCK_AMBIGUOUS)
    ok(out2 == "needs_knowledge" and "无法消歧" in (st2.get("reason") or ""),
       "10 轮次上限 → 停止追问")


def test_scenario3b_answer_unknown_metric():
    # 选了「发单量」但正式定义不存在（candidate）→ 拒绝
    st = C.new_state("u1", "查上个月订单量", {"metrics": []}, MOCK_AMBIGUOUS)
    st["confirmed_slots"]["metric"] = "发单量"
    st, out = C._finalize(st, MOCK_MISSING)   # 发单量在 MOCK_MISSING 里是 candidate
    ok(out == "needs_knowledge", "3b 锁定指标无正式定义 → 停止生成 SQL", out)


# ═══════════════════════════════════════════════════════════
# ④ 时间边界（方案 §5 示例）
# ═══════════════════════════════════════════════════════════
def test_prev_natural_month():
    import datetime
    s, e, ex = C.prev_natural_month(datetime.date(2026, 10, 15))
    ok(s.isoformat() == "2026-09-01", "上月起点 2026-09-01", s)
    ok(ex.isoformat() == "2026-10-01", "左闭右开终点 2026-10-01", ex)
    ok(e.isoformat() == "2026-09-30", "闭区间最后一天 2026-09-30", e)


# ═══════════════════════════════════════════════════════════
# ⑤ 持久化 + 多任务隔离 + 归属校验（临时库）
# ═══════════════════════════════════════════════════════════
def _with_temp_db(fn):
    import tempfile
    orig = config_store.CONFIG_DB
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    config_store.CONFIG_DB = path
    config_store._state = {"ready": False}   # 强制重初始化（建表）
    try:
        fn()
    finally:
        config_store.CONFIG_DB = orig
        config_store._state = {"ready": False}
        try:
            os.remove(path)
        except Exception:
            pass


def test_scenario11_session_isolation():
    def run():
        st1 = _state("查上个月订单量")
        st2 = _state("查上个月完单量")
        st1["query_id"] = "A1"
        st2["query_id"] = "B2"
        st1["username"], st2["username"] = "alice", "bob"
        config_store.clarify_save(st1)
        config_store.clarify_save(st2)
        a = config_store.clarify_load("A1", "alice")
        b = config_store.clarify_load("B2", "bob")
        ok(a and a["original_question"] == "查上个月订单量", "11 各任务状态独立")
        ok(b and b["original_question"] == "查上个月完单量", "11 状态不串")
        ok(config_store.clarify_load("A1", "bob") is None, "11 跨用户读取被拒")
        config_store.clarify_delete("A1", "alice")
        ok(config_store.clarify_load("A1", "alice") is None, "11 删除生效")
    _with_temp_db(run)


# ═══════════════════════════════════════════════════════════
# ⑥ 不绕过 SQL Guard（静态契约）
# ═══════════════════════════════════════════════════════════
def test_scenario13_16_no_guard_bypass():
    src = open(os.path.join(HERE, "clarification.py"), encoding="utf-8").read()
    ok("execute_sql" not in src and "_exec_sql" not in src, "13 澄清模块不执行 SQL")
    ok("import db" not in src and "import guard" not in src and "import agent" not in src,
       "13/16 澄清模块不 import 执行/护栏（执行仍走 agent.ask 单出口）")
    # 澄清只产出「问题」与「意图」，不产出 SQL —— SQL 由 agent 生成并经 _pre_execute_check
    ok("_pre_execute_check" not in src, "13 校验闸门由 agent 承担，澄清不复制")


# ═══════════════════════════════════════════════════════════
# ⑦ 端点行为（TestClient + 打桩，不连 LLM / ClickHouse）
# ═══════════════════════════════════════════════════════════
def test_endpoints():
    import server
    from fastapi.testclient import TestClient

    def run():
        # 在临时库跑，避免污染真实库（admin 由 init_store 种子，other 手动建）
        try:
            config_store.create_user("other", "TestPass123!")
        except Exception:
            pass
        # 临时库的种子账号带 must_change_password，会触发中间件 403；清掉以走通业务接口
        conn = config_store._connect()
        conn.execute("UPDATE users SET must_change_password = 0")
        conn.commit()
        conn.close()
        tok = config_store.create_session("admin")
        c = TestClient(server.app)
        h = {"Authorization": f"Bearer {tok}"}

        # 打桩：understand / agent.ask / 指标清单 / payload / index
        calls = {}
        def fake_understand(q, sidx=None):
            return {"intent": "summary", "metrics": [], "entities": [],
                    "dimensions": [], "time_range": "", "filters": [], "ambiguity": ""}
        def fake_ask(index, q, user=None, understanding_override=None, want_answer=False):
            calls["ask"] = {"q": q, "override": understanding_override}
            return {"question": q, "ok": True, "sql": "SELECT 1 AS x",
                    "columns": ["x"], "rows": [[1]], "row_count": 1,
                    "tables": [], "metrics": [], "dicts": [], "info": "", "attempts": 1,
                    "pipeline": "v2", "error": "", "answer": "ok",
                    "perm_decision": {}, "understanding": understanding_override}
        def fake_payload(result, index):
            return {"question": result.get("question"), "ok": result.get("ok"),
                    "sql": result.get("sql"), "columns": result.get("columns"),
                    "rows": result.get("rows"), "row_count": result.get("row_count"),
                    "tables": [], "metrics": [], "dicts": [], "info": "",
                    "attempts": 1, "pipeline": "v2", "error": "", "answer": ""}
        def fake_index():
            return {"_semantic": {}}

        orig = (server.query_understanding.understand, server.agent.ask,
                server._build_ask_payload, server.get_index, server._clarify_metrics)
        server.query_understanding.understand = fake_understand
        server.agent.ask = fake_ask
        server._build_ask_payload = fake_payload
        server.get_index = fake_index
        server._clarify_metrics = lambda: MOCK_AMBIGUOUS
        try:
            # 场景2：发起澄清
            r = c.post("/api/clarify/start", json={"question": "查上个月订单量"}, headers=h)
            d = r.json()
            ok(d["kind"] == "clarify" and d.get("query_id"), "端点 发起澄清", str(d)[:80])
            qid = d["query_id"]

            # 场景4：回答 → 执行（agent.ask 带 override）
            r2 = c.post("/api/clarify/answer", json={"query_id": qid, "answer": "发单量"}, headers=h)
            d2 = r2.json()
            ok(d2.get("ok") is True and calls.get("ask", {}).get("override", {}).get("metrics") == ["发单量"],
               "端点 回答后执行并锁口径", str(d2)[:80])
            ok(d2.get("clarify", {}).get("kind") == "done", "端点 执行完成标记")

            # 场景9：取消
            r3 = c.post("/api/clarify/start", json={"question": "查上个月订单量"}, headers=h)
            qid3 = r3.json()["query_id"]
            r4 = c.post(f"/api/clarify/{qid3}/cancel", headers=h)
            ok(r4.json().get("kind") == "cancelled", "端点 取消")

            # 场景11：越权读取（另一真实用户 other）
            tok2 = config_store.create_session("other")
            r5 = c.get(f"/api/clarify/{qid}", headers={"Authorization": f"Bearer {tok2}"})
            ok(r5.status_code == 404, "端点 越权读取 404", r5.status_code)

            # 场景14：执行失败正确返回状态
            def fake_ask_fail(index, q, user=None, understanding_override=None, want_answer=False):
                return {"question": q, "ok": False, "sql": "", "columns": [], "rows": [],
                        "row_count": 0, "tables": [], "metrics": [], "dicts": [], "info": "",
                        "attempts": 1, "pipeline": "v2", "error": "模拟执行失败", "answer": "",
                        "perm_decision": {}, "understanding": understanding_override}
            server.agent.ask = fake_ask_fail
            r6 = c.post("/api/clarify/answer", json={"query_id": qid, "answer": "完单量"}, headers=h)
            d6 = r6.json()
            ok(d6.get("ok") is False and d6.get("clarify", {}).get("kind") == "done",
               "端点 执行失败如实返回", str(d6)[:80])
            server.agent.ask = fake_ask          # 恢复，供 /api/ask 兼容性检查

            # 场景15：单轮 /api/ask 仍可用（同一打桩下）
            r7 = c.post("/api/ask", json={"question": "查上个月发单量"}, headers=h)
            ok(r7.status_code == 200 and r7.json().get("ok") is True, "端点 /api/ask 单轮兼容",
               f"status={r7.status_code}")
        finally:
            (server.query_understanding.understand, server.agent.ask,
             server._build_ask_payload, server.get_index, server._clarify_metrics) = orig

    _with_temp_db(run)


def main():
    for fn in (test_scenario1_unique_direct, test_scenario2_ambiguous_clarify,
               test_scenario3_missing_refuse, test_scenario12_unreviewed_not_formal,
               test_build_clarification, test_scenario4_answer_locks_metric,
               test_scenario5_keep_other_conditions, test_scenario6_update_time,
               test_scenario7_free_text_merge, test_scenario8_other_goes_to_knowledge,
               test_scenario9_cancel, test_scenario10_round_cap,
               test_scenario3b_answer_unknown_metric, test_prev_natural_month,
               test_scenario11_session_isolation, test_scenario13_16_no_guard_bypass,
               test_endpoints):
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
