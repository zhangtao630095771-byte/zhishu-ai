# -*- coding: utf-8 -*-
"""
eval/run_eval.py —— 轻量回归评测集

★ 用途：改完代码/知识库/阈值后，跑一遍确认没有把已修复的口径改坏。
★ 断言维度（只做「必须满足」的弱断言，避免误报）：
    ① ok              —— 是否执行成功
    ② expected_tables   —— SQL 必须用到这些表
    ③ expected_metrics  —— 召回必须包含这些指标
    ④ expected_sql_contains —— SQL 必须包含这些关键谓词（口径正确性的核心）

用法：
    .venv/bin/python eval/run_eval.py           # 全量跑
    .venv/bin/python eval/run_eval.py 1 3 7     # 只跑指定 id
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rag
import agent

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "cases.json")


def load_cases():
    return json.load(open(CASES, encoding="utf-8"))


def check_case(c, r):
    """返回失败原因列表（空 = 通过）"""
    fails = []
    if r.get("ok") != c.get("expected_ok", True):
        fails.append(f"ok={r.get('ok')}（期望 {c.get('expected_ok', True)}），error={r.get('error')}")
        return fails  # 执行失败时其余断言无意义
    sql = (r.get("sql") or "").lower()
    for t in c.get("expected_tables", []):
        if t.lower() not in [x.lower() for x in (r.get("tables") or [])]:
            fails.append(f"缺表 {t}（实际 {r.get('tables')}）")
    for m in c.get("expected_metrics", []):
        if m not in (r.get("metrics") or []):
            fails.append(f"缺指标 {m}（实际 {r.get('metrics')}）")
    for s in c.get("expected_sql_contains", []):
        if s.lower() not in sql:
            fails.append(f"SQL 缺关键谓词：{s}")
    return fails


def run(cases, index):
    passed, failed = 0, 0
    for c in cases:
        q = c["question"]
        try:
            r = agent.ask(index, q)
        except Exception as e:
            print(f"❌ [{c['id']}] {c['name']} → 异常：{e}")
            failed += 1
            continue
        fails = check_case(c, r)
        if fails:
            failed += 1
            print(f"❌ [{c['id']}] {c['name']} → {'；'.join(fails)}")
            if not r.get("ok"):
                print(f"     SQL={r.get('sql') or ''}")
        else:
            passed += 1
            print(f"✅ [{c['id']}] {c['name']}")
    print(f"\n========== 评测结果：{passed}/{passed + failed} 通过 ==========")
    return failed == 0


if __name__ == "__main__":
    cases = load_cases()
    ids = set(sys.argv[1:])
    if ids:
        cases = [c for c in cases if str(c.get("id")) in ids]
        if not cases:
            print(f"未找到 id 为 {sorted(ids)} 的用例")
            sys.exit(1)
    print(f"加载 {len(cases)} 条用例，正在建索引...")
    index = rag.build_index()
    ok = run(cases, index)
    sys.exit(0 if ok else 1)
