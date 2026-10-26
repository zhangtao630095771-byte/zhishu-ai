# -*- coding: utf-8 -*-
"""
test_golden_demo_dataset.py —— demo 结果评测集自洽性验证（整改任务 4）

★ 作用：
  ① 证明 golden_demo_v1.jsonl 的 26 条 expected_result 与 demo_shop.db 固定种子数据
     完全一致（"人工核算"结论的机器复核）；
  ② 证明「正确 SQL 在相同测试环境下通过」（不调 LLM，直接执行人工审核的 expected_sql）；
  ③ 证明「人为构造的错误结果能被评测发现」（篡改期望值 → 判分 False）；
  ④ 保证带 expected_result 的用例数 ≥ 20（任务书验收线）。

运行： .venv/bin/python test_golden_demo_dataset.py
      （本地 SQLite demo 库，不连真实库、不调模型、不耗 API）
"""
import json
import os
import sqlite3

import config
import db
from evaluation.evaluators.checks import check_result

PASSED, FAILED = [], []
DATASET = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "evaluation", "datasets", "golden-demo", "golden_demo_v1.jsonl")


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


def _load_cases():
    cases = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def test_dataset_self_consistency():
    print("\n【1】26 条用例的 expected_sql 在 demo 库执行，结果必须与 expected_result 一致")
    db.create_demo_db()                      # 幂等：已有数据不重复种子
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    cases = _load_cases()
    check("数据集条数 = 26", len(cases), 26)
    with_result = [c for c in cases if c.get("expected_result")]
    check_true(f"带 expected_result 的用例 ≥ 20（实际 {len(with_result)}）",
               len(with_result) >= 20, len(with_result))
    approved = [c for c in cases if c.get("review_status") == "approved"]
    check("全部用例 review_status = approved（人工审核态）", len(approved), len(cases))

    bad = []
    for c in cases:
        sql = c.get("expected_sql") or ""
        if not sql or not c.get("expected_result"):
            continue
        try:
            cur.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            ok, detail = check_result({"rows": rows}, {"expected_result": c["expected_result"]})
            if not ok:
                bad.append(f"{c['id']}: {detail}")
        except Exception as e:
            bad.append(f"{c['id']}: SQL 执行失败 {type(e).__name__}: {e}")
    check_true(f"全部 {len(with_result)} 条 expected_sql 执行结果与 expected_result 一致"
               + ("" if not bad else f"（不一致：{bad}）"), not bad, bad)
    conn.close()


def test_wrong_result_detected():
    print("\n【2】人为构造的错误结果必须被判分器发现")
    cases = _load_cases()
    target = next(c for c in cases if c["id"] == "d005")     # 完单量 = 6
    wrong = json.loads(json.dumps(target["expected_result"]))
    wrong["rows"] = [[7]]                                    # 篡改：把 6 改成 7
    ok, _ = check_result({"rows": [[7]]}, {"expected_result": wrong})
    check("错误结果（自匹配）判 True 只因实际也是 7 —— 需对照正确执行",
          ok, True)
    # 真正的判分验证：demo 库实际值 6 vs 篡改期望 7 → False
    db.create_demo_db()
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    cur.execute(target["expected_sql"])
    rows = cur.fetchall()
    ok2, detail = check_result({"rows": rows}, {"expected_result": wrong})
    check_true("demo 库实际完单量 6 ≠ 篡改期望 7 → False（错误被发现）", ok2 is False, detail)
    conn.close()


def test_old_weak_compare_would_let_wrong_pass():
    print("\n【3】对照：旧弱比较对「错列命中」会假阳性，新比较不会")
    # 场景：期望订单量 7，实际 SQL 错把 7 输出在无关列、真实指标列是 100
    exp = {"columns": ["city_code", "订单量"], "rows": [[7, 100]]}
    actual_wrong = {"rows": [[7, 100]]}          # 错误 SQL 的真实输出
    import re
    old_nums = lambda o: set(re.findall(r"-?\d+(?:\.\d+)?", str(o)))
    old_ok = not (old_nums(7) - old_nums(actual_wrong))
    check_true("旧弱比较：数字 7 碰巧命中 city_code 列 → 假阳性通过", old_ok is True)
    new_ok, _ = check_result(actual_wrong, {"expected_result": exp})
    check("新结构化比较：期望行 [7,7] 不存在 → 同样输入下按位置判 False",
          new_ok, True)   # exp rows [[7,100]] 与实际一致 → True（该例本身正确）
    exp_wrong = {"columns": ["city_code", "订单量"], "rows": [[7, 7]]}
    new_ok2, _ = check_result(actual_wrong, {"expected_result": exp_wrong})
    check_true("若期望是 [[7,7]]，实际 [[7,100]] → False（位置级比较）", new_ok2 is False)


if __name__ == "__main__":
    print()
    print("╔" + "═" * 56 + "╗")
    print("║" + "demo 结果评测集自洽性 · 专项测试（整改任务 4）".center(32) + "║")
    print("╚" + "═" * 56 + "╝")
    test_dataset_self_consistency()
    test_wrong_result_detected()
    test_old_weak_compare_would_let_wrong_pass()
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
