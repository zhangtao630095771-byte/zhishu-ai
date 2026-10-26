# -*- coding: utf-8 -*-
"""
test_legacy_validation.py —— Legacy 兜底路径业务校验收口（GAP-1 回归测试）

★ 背景：ask_legacy 原只做「只读 + 表权限 + 字段权限」，缺少 sql_validator 的
   P1 指标硬约束 / P2 JOIN / P3 列存在性。补齐后，Legacy 必须与 V2 一致地
   拒绝「缺必要条件 / 出现禁止过滤 / 引用不存在列」的 SQL，且不得调用 db.execute_sql。

★ 本测试用真实 sql_validator（不 stub），只 stub 检索/生成/执行，验证：
    ① Legacy 拒绝缺 required_predicates 的 SQL，且 db.execute_sql 调用 0 次；
    ② Legacy 拒绝含 forbidden_predicates 的 SQL；
    ③ Legacy 拒绝引用不存在列的 SQL；
    ④ 满足条件的 SQL 正常执行（正向）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import agent
import db
import guard
import rag
import llm
import prompt as prompt_mod

PASSED, FAILED = [], []


def check(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"  {'✅' if cond else '❌'} {label}")
    if not cond and detail:
        print(f"       实际: {detail}")
    return cond


def _fake_index(metrics, tables):
    return {
        "tables": tables,
        "metrics": metrics,
        "dicts": [],
        "allowed_tables": [t["name"] for t in tables],
        "disabled_metrics": [],
    }


def _run_legacy(question, metrics, tables, sql_to_return, *, expect_exec):
    """跑一次 ask_legacy，返回 (db.execute_sql 调用序列, 结果 dict)。"""
    calls = []

    def _fake_exec(s):
        calls.append(s)
        return (["x"], [[1]])          # 模拟执行成功

    patch = {}

    def _set(mod, name, val):
        patch[(mod, name)] = getattr(mod, name)
        setattr(mod, name, val)

    _set(rag, "retrieve", lambda idx, q: ([t["name"] for t in tables],
                                          [m["name"] for m in metrics], "r", None))
    _set(rag, "retrieve_dicts", lambda idx, q: ([], "无字典"))
    _set(rag, "match_disabled_metric", lambda idx, q: None)
    _set(prompt_mod, "build_system_prompt", lambda *a, **k: "sys")
    _set(prompt_mod, "build_repair_prompt", lambda q, s, e: "repair")
    _set(llm, "chat", lambda sp, u: sql_to_return)
    _set(guard, "sql_guard", lambda s: (True, ""))
    _set(guard, "check_tables_ex",
         lambda s, a, strict=None: {"ok": True, "illegal": [], "status": "parsed",
                                    "method": "stub", "reason": "", "message": ""})
    _set(db, "execute_sql", _fake_exec)
    try:
        r = agent.ask_legacy(_fake_index(metrics, tables), question)
    finally:
        for (mod, name), val in patch.items():
            setattr(mod, name, val)
    return calls, r


def test_legacy_required_predicate_blocked():
    print("\n【GAP-1】Legacy 拒绝缺 required_predicates 的 SQL（不执行）")
    metrics = [{"name": "代驾完单量", "definition": "status=40",
                "metric_type": "count", "required_predicates": ["status = 40"]}]
    tables = [{"name": "ods_valet_order_all", "column_list": ["id", "status"]}]
    # llm 返回的 SQL 缺 status = 40 → 应被校验拦下
    calls, r = _run_legacy("查询代驾完单量", metrics, tables,
                           "SELECT count(*) FROM ods_valet_order_all",
                           expect_exec=False)
    check("拒绝执行（ok=False）", r.get("ok") is False, str(r.get("error")))
    check("db.execute_sql 调用 0 次", len(calls) == 0, f"调用序列={calls}")
    check("错误含「业务校验」", "业务校验" in (r.get("error") or ""), str(r.get("error")))


def test_legacy_forbidden_predicate_blocked():
    print("\n【GAP-1】Legacy 拒绝含 forbidden_predicates 的 SQL")
    metrics = [{"name": "发单量", "definition": "发单量不限状态",
                "metric_type": "count", "forbidden_predicates": ["status"]}]
    tables = [{"name": "ods_tu_order_all", "column_list": ["id", "status"]}]
    calls, r = _run_legacy("统计发单量", metrics, tables,
                           "SELECT count(*) FROM ods_tu_order_all WHERE status = 6",
                           expect_exec=False)
    check("拒绝执行", r.get("ok") is False, str(r.get("error")))
    check("db.execute_sql 调用 0 次", len(calls) == 0, f"调用序列={calls}")


def test_legacy_column_exist_blocked():
    print("\n【GAP-1】Legacy 拒绝引用不存在列的 SQL")
    metrics = []
    tables = [{"name": "ods_valet_order_all", "column_list": ["id", "status"]}]
    calls, r = _run_legacy("查询订单", metrics, tables,
                           "SELECT id, not_exist_col FROM ods_valet_order_all",
                           expect_exec=False)
    check("拒绝执行", r.get("ok") is False, str(r.get("error")))
    check("db.execute_sql 调用 0 次", len(calls) == 0, f"调用序列={calls}")


def test_legacy_ok_executes():
    print("\n【GAP-1】Legacy 满足条件时正常执行（正向）")
    metrics = [{"name": "代驾完单量", "definition": "status=40",
                "metric_type": "count", "required_predicates": ["status = 40"]}]
    tables = [{"name": "ods_valet_order_all", "column_list": ["id", "status"]}]
    calls, r = _run_legacy("查询代驾完单量", metrics, tables,
                           "SELECT count(*) FROM ods_valet_order_all WHERE status = 40",
                           expect_exec=True)
    check("执行成功（ok=True）", r.get("ok") is True, str(r.get("error")))
    check("db.execute_sql 调用 1 次", len(calls) == 1, f"调用序列={calls}")


if __name__ == "__main__":
    test_legacy_required_predicate_blocked()
    test_legacy_forbidden_predicate_blocked()
    test_legacy_column_exist_blocked()
    test_legacy_ok_executes()
    print("\n" + "=" * 60)
    print(f"🎉 全部通过（{len(PASSED)}/{len(PASSED) + len(FAILED)}）"
          if not FAILED else f"❌ 存在失败项（{len(FAILED)}）")
    print("=" * 60)
    raise SystemExit(1 if FAILED else 0)
