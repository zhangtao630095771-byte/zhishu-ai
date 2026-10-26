# -*- coding: utf-8 -*-
"""
test_result_check.py —— 业务结果正确性判分器专项测试（整改任务 4）

★ 背景（docs/audit/02-critical-issues.md C-02 / C-04）：
  旧 check_result 是「数字集合包含」的弱比较：
    ① 不校验行数/列结构 → 少算一行的结果也可能通过；
    ② "10" 与 "10.0" 字符串不同 → 精度误报；
    ③ 期望数字碰巧出现在无关列 → 假阳性通过。
  本次增强为结构化比较（expected_result={"rows": [...], "columns": [...], "precision": N}）：
    行数一致 + 无序逐行比较 + 数值按精度归一 + NULL 归一；
    旧格式（纯数字/字符串/列表）保持向后兼容。

运行： .venv/bin/python test_result_check.py   （纯逻辑，不连库、不调模型）
"""
from evaluation.evaluators.checks import check_result

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


EXP = {"columns": ["城市", "订单量"], "rows": [["北京", 4], ["上海", 3]], "precision": 4}


def test_structured_pass():
    print("\n【1】结构化比较：正确结果通过")
    ok, _ = check_result({"rows": [["上海", 3], ["北京", 4]]}, {"expected_result": EXP})
    check("行顺序不同（无序比较）仍通过", ok, True)
    ok, _ = check_result({"rows": [["北京", "4"], ["上海", "3.0"]]},
                         {"expected_result": EXP})
    check("数值字符串/整数/小数形式归一后通过（'4'==4, '3.0'==3）", ok, True)


def test_structured_fail():
    print("\n【2】结构化比较：错误结果必须被发现")
    ok, detail = check_result({"rows": [["北京", 4], ["上海", 2]]},
                              {"expected_result": EXP})
    check_true("数值错误（3 被算成 2）→ False", ok is False, detail)
    ok, detail = check_result({"rows": [["北京", 4]]}, {"expected_result": EXP})
    check_true("少一行（行数不符）→ False", ok is False, detail)
    ok, detail = check_result({"rows": [["北京", 4], ["上海", 3], ["广州", 1]]},
                              {"expected_result": EXP})
    check_true("多一行 → False", ok is False, detail)
    ok, detail = check_result({"rows": [[4], [3]]}, {"expected_result": EXP})
    check_true("列结构不符（少一列）→ False", ok is False, detail)


def test_precision_and_null():
    print("\n【3】精度与 NULL 规则")
    exp = {"rows": [[0.5714]], "precision": 2}
    ok, _ = check_result({"rows": [[0.57]]}, {"expected_result": exp})
    check("precision=2：0.5714 ≈ 0.57 通过", ok, True)
    exp4 = {"rows": [[0.5714]], "precision": 4}
    ok, _ = check_result({"rows": [[0.57]]}, {"expected_result": exp4})
    check("precision=4：0.57 ≠ 0.5714 不通过", ok, False)
    exp_null = {"rows": [[None, 5]]}
    ok, _ = check_result({"rows": [[None, 5.0]]}, {"expected_result": exp_null})
    check("NULL 与 NULL 匹配、数值归一", ok, True)
    ok, _ = check_result({"rows": [["", 5]]}, {"expected_result": exp_null})
    check("空字符串归一为 NULL 后匹配（CK 空串/SQLite NULL 兼容）", ok, True)


def test_backward_compat():
    print("\n【4】旧格式向后兼容（纯数字/列表的数字集合包含）")
    ok, _ = check_result({"rows": [[10, 7]]}, {"expected_result": 10})
    check("纯数字期望仍通过", ok, True)
    ok, _ = check_result({"rows": [[7]]}, {"expected_result": 10})
    check("旧格式：期望数字缺失仍判 False", ok, False)
    ok, _ = check_result({"rows": None}, {"expected_result": EXP})
    check("无结果可对比 → None（不计分母）", ok, None)
    ok, _ = check_result({"rows": [[1]]}, {"expected_result": None})
    check("用例未提供期望 → None（不计分母）", ok, None)


def test_false_positive_fixed():
    print("\n【5】旧弱比较的假阳性被修复：期望数字不得碰巧命中无关列")
    # 期望「订单量=7」，实际结果里 7 出现在【城市编码列】，订单量列其实是 100
    exp = {"columns": ["city_code", "订单量"], "rows": [[7, 100]]}
    ok, detail = check_result({"rows": [[7, 100]]}, {"expected_result": exp})
    check_true("结构化逐行比较：数值位置对才通过", ok is True, detail)
    exp2 = {"columns": ["city_code", "订单量"], "rows": [[7, 7]]}
    ok2, _ = check_result({"rows": [[7, 100]]}, {"expected_result": exp2})
    check_true("订单量列实际是 100 ≠ 7 → False（旧逻辑会假阳性通过）", ok2 is False)


if __name__ == "__main__":
    print()
    print("╔" + "═" * 56 + "╗")
    print("║" + "结果正确性判分器 · 专项测试（整改任务 4）".center(36) + "║")
    print("╚" + "═" * 56 + "╝")
    test_structured_pass()
    test_structured_fail()
    test_precision_and_null()
    test_backward_compat()
    test_false_positive_fixed()
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
