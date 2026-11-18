# -*- coding: utf-8 -*-
"""
test_result_compare.py —— 业务结果对比引擎专项测试（P0-1-C / P0-2-L3）

覆盖六类判定 + 差异定位：
  ① exact 完全一致（含行顺序不同）
  ② within_tolerance 数值容差内一致（'10' vs 10.0）
  ③ structure_only 结构一致但数值不一致（给出 cell diff 定位）
  ④ column_mismatch 字段不一致（列数不同）
  ⑤ row_count_mismatch 行数不一致
  ⑥ incomparable 无法比较（缺结果/缺期望）

运行： .venv/bin/python test_result_compare.py  （纯逻辑，不连库、不调模型）
"""
from evaluation.result_compare import compare

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


def test_exact():
    print("\n【1】exact：完全一致（行顺序不同也算）")
    r = compare([["上海", 3], ["北京", 4]], EXP)
    check("verdict", r["verdict"], "exact")
    check("matched", r["matched"], True)
    r2 = compare([["北京", 4], ["上海", 3]], EXP)
    check("同序也 exact", r2["verdict"], "exact")


def test_within_tolerance():
    print("\n【2】within_tolerance：数值归一后一致")
    r = compare([["北京", "4"], ["上海", "3.0"]], EXP)
    check("'4'==4、'3.0'==3 → within_tolerance", r["verdict"], "within_tolerance")
    check("matched", r["matched"], True)


def test_structure_only():
    print("\n【3】structure_only：结构一致但数值错，须定位差异格")
    r = compare([["北京", 4], ["上海", 2]], EXP)
    check("verdict", r["verdict"], "structure_only")
    check("matched", r["matched"], False)
    check_true("有 cell_diffs", len(r["details"]["cell_diffs"]) == 1, r["details"])
    d = r["details"]["cell_diffs"][0]
    check("定位到 col=1（订单量列）", d["col"], 1)
    check("expected 值", d["expected"], 3)
    check("actual 值", d["actual"], 2)


def test_column_mismatch():
    print("\n【4】column_mismatch：字段/列数不一致")
    r = compare([["北京", 4, "x"], ["上海", 3, "y"]], EXP)
    check("verdict", r["verdict"], "column_mismatch")
    r2 = compare([[4], [3]], EXP)
    check("少一列也 column_mismatch", r2["verdict"], "column_mismatch")


def test_row_count_mismatch():
    print("\n【5】row_count_mismatch：行数不一致")
    r = compare([["北京", 4]], EXP)
    check("少一行", r["verdict"], "row_count_mismatch")
    check_true("记录行数差异", r["details"]["row_count_expected"] == 2
               and r["details"]["row_count_actual"] == 1, r["details"])
    r2 = compare([["北京", 4], ["上海", 3], ["广州", 1]], EXP)
    check("多一行", r2["verdict"], "row_count_mismatch")


def test_incomparable():
    print("\n【6】incomparable：无法比较")
    check("无实际结果", compare(None, EXP)["verdict"], "incomparable")
    check("无标准结果", compare([["北京", 4]], None)["verdict"], "incomparable")


def test_null():
    print("\n【7】NULL / 空串归一")
    exp = {"rows": [[None, 5]]}
    r = compare([["", 5]], exp)
    check("空串归一为 NULL 后一致", r["verdict"], "within_tolerance")
    r2 = compare([[None, 5.0]], exp)
    check("NULL 与 5.0 归一后一致", r2["verdict"], "within_tolerance")


def test_legacy():
    print("\n【8】旧格式向后兼容（数字包含）")
    r = compare([[10, 7]], 10)
    check("旧格式命中", r["verdict"], "exact")
    r2 = compare([[7]], 10)
    check("旧格式缺失 → structure_only", r2["verdict"], "structure_only")


if __name__ == "__main__":
    print()
    print("╔" + "═" * 56 + "╗")
    print("║" + "业务结果对比引擎 · 专项测试".center(36) + "║")
    print("╚" + "═" * 56 + "╝")
    test_exact()
    test_within_tolerance()
    test_structure_only()
    test_column_mismatch()
    test_row_count_mismatch()
    test_incomparable()
    test_null()
    test_legacy()
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
