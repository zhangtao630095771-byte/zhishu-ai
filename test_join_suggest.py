# -*- coding: utf-8 -*-
"""
test_join_suggest.py —— JOIN 关联字段推荐的纯逻辑单测

★ 重点守住的规则：**两张表都有 id 时，绝不能推荐用 id 关联**。
  这正是之前 t.id = r.id 那个错误的成因 —— 用单测把它钉死，防止以后改坏。

运行： .venv/bin/python test_join_suggest.py   （纯逻辑，不连库、不调模型）
"""
import join_suggest as js

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


def test_name_tokens():
    print("\n【1】表名拆词 _name_tokens")
    t = js._name_tokens("ods_tu_large_handcart_order_fee_detail_all")
    check_true("含 fee", "fee" in t, t)
    check_true("含 order", "order" in t, t)
    check_true("不含噪声词 all/ods", ("all" not in t and "ods" not in t), t)


def test_no_id_join():
    print("\n【2】★ 两表都有 id 时，不得推荐 id 关联（防 t.id = r.id）")
    # 复刻 ods_tu_record_all ⟷ ods_tu_record_type_all 的真实字段
    lcols = ["id", "type", "money", "ctime"]
    rcols = ["id", "code", "title"]
    cands = js._deterministic_candidates("ods_tu_record_all", "ods_tu_record_type_all", lcols, rcols)
    pairs = [(c["left_col"], c["right_col"]) for c in cands]
    check("候选里没有 (id, id)", ("id", "id") in pairs, False)
    print(f"       实际候选：{pairs}")


def test_same_name_priority():
    print("\n【3】同名字段优先，且通用噪声列被排除")
    lcols = ["id", "order_no", "customer_id", "status", "money"]
    rcols = ["id", "order_no", "status", "title"]
    cands = js._deterministic_candidates("ods_a_order_all", "ods_b_order_all", lcols, rcols)
    pairs = [(c["left_col"], c["right_col"]) for c in cands]
    check_true("推荐了 order_no", ("order_no", "order_no") in pairs, pairs)
    check_true("排除了 id", ("id", "id") not in pairs, pairs)
    check_true("排除了 status", ("status", "status") not in pairs, pairs)
    check_true("order_no 排在最前（得分最高）", cands[0]["left_col"] == "order_no", cands[:1])


def test_fk_guess_is_conservative():
    print("\n【4】外键推断要保守：业务词要能在对方表名里找到")
    # 左表 customer_id，右表是 fee 明细 → 业务词 customer 不在右表名里 → 不推
    c1 = js._deterministic_candidates("ods_order_all", "ods_fee_detail_all",
                                      ["id", "customer_id"], ["id", "fee_no"])
    check_true("不瞎推 customer_id→id", all(not (c["left_col"] == "customer_id" and c["right_col"] == "id") for c in c1), c1)
    # 右表含 fee 且左表有 fee_id → 可推
    c2 = js._deterministic_candidates("ods_order_all", "ods_fee_detail_all",
                                      ["id", "fee_id"], ["id", "fee_no"])
    check_true("能推 fee_id→id", any(c["left_col"] == "fee_id" and c["right_col"] == "id" for c in c2), c2)


if __name__ == "__main__":
    print()
    print("╔" + "═" * 56 + "╗")
    print("║" + "JOIN 关联字段推荐 · 纯逻辑单测".center(46) + "║")
    print("╚" + "═" * 56 + "╝")
    test_name_tokens()
    test_no_id_join()
    test_same_name_priority()
    test_fk_guess_is_conservative()
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
    # ★ 失败必须让进程返回非 0 —— 否则 CI/上层 runner 会把失败的测试当成功（曾经就是这样漏掉的）
    raise SystemExit(1 if FAILED else 0)
