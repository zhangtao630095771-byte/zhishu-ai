# -*- coding: utf-8 -*-
"""
test_labels.py —— 结果列中文名的测试（重点：同名列消歧）

★ 为什么这个必须测？
  表头是用户唯一能依赖的"这列是什么"的说明。
  表头改错，比没有中文名更糟：用户会照着错的名字理解数据。

  而"同名列消歧"是这套逻辑里最容易出问题的地方 ——
  它要根据列名里的修饰词去猜中文（this_week → 本周），
  猜错了就是贴错标签；猜不出还得退化成"括号标注"，不能直接留两个一样的名字。

★ 特别要守住的底线：
  「本周和上周的订单量对比」两列绝不能都叫"订单量" ——
  那样用户根本分不清哪列是哪列（以前表头下挂着英文字段名还能凑合认，
  现在表头只显示中文，同名问题会被放大）。

★ 不连数据库、不调模型，毫秒级跑完。

运行： .venv/bin/python test_labels.py
"""
import colname


PASSED, FAILED = [], []


def check(label, got, expected):
    ok = (got == expected)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望: {expected}")
        print(f"       实际: {got}")


def mk(cols_zh):
    """按 {列名: 中文名} 造一份 labels"""
    return {c: {"zh": zh, "source": "metric"} for c, zh in cols_zh.items()}


def disamb(cols_zh):
    """跑消歧，返回 {列名: 消歧后的中文名}"""
    L = mk(cols_zh)
    colname.disambiguate(L, list(L))
    return {c: v["zh"] for c, v in L.items()}


# ═══════════════════════════════════════════════════════════
# ① 同名列消歧（有词典，能翻成中文）
# ═══════════════════════════════════════════════════════════
def test_disambiguate_zh():
    print()
    print("─" * 68)
    print("① 同名消歧：能翻成中文的（不许出现两个一样的表头）")
    print("─" * 68)

    cases = [
        ({"this_week_order_count": "订单量", "last_week_order_count": "订单量"},
         {"this_week_order_count": "本周订单量", "last_week_order_count": "上周订单量"}),
        ({"this_month_finish_count": "完单量", "last_month_finish_count": "完单量"},
         {"this_month_finish_count": "本月完单量", "last_month_finish_count": "上月完单量"}),
        #   prev_month 应该整体翻成"上月"（比更短的 prev「上期」准确）——
        #   这正是"长组合优先"要保证的效果
        ({"current_month_amount": "金额", "prev_month_amount": "金额"},
         {"current_month_amount": "本月金额", "prev_month_amount": "上月金额"}),
    ]
    for got_in, expect in cases:
        got = disamb(got_in)
        check(f"{list(got_in)}", got, expect)
        #   底线：消歧后必须没有重名
        check(f"  ↳ 消歧后无重名", len(set(got.values())), len(got))


# ═══════════════════════════════════════════════════════════
# ② 词典里没有的修饰词 → 退化成括号标注（至少能区分）
# ═══════════════════════════════════════════════════════════
def test_disambiguate_fallback():
    print()
    print("─" * 68)
    print("② 翻不出来时：括号标注差异部分，绝不留下两个同名表头")
    print("─" * 68)

    got = disamb({"north_area_amount": "金额", "south_area_amount": "金额"})
    check("north/south", got,
          {"north_area_amount": "金额（north）", "south_area_amount": "金额（south）"})
    check("  ↳ 消歧后无重名", len(set(got.values())), len(got))

    #   完全同名的列（列名都一样，实际不会出现，但兜底不能崩）
    got = disamb({"amount": "金额"})
    check("单列不动", got, {"amount": "金额"})


# ═══════════════════════════════════════════════════════════
# ③ 不该被动的地方（不重名就别乱加前缀）
# ═══════════════════════════════════════════════════════════
def test_no_false_disambiguation():
    print()
    print("─" * 68)
    print("③ 不该动的地方：不重名不加前缀、空中文名不参与")
    print("─" * 68)

    check("不重名 → 原样",
          disamb({"channel_source": "来源渠道", "order_count": "订单量"}),
          {"channel_source": "来源渠道", "order_count": "订单量"})

    check("含修饰词但不重名 → 也不加",
          disamb({"this_week_order_count": "订单量"}),
          {"this_week_order_count": "订单量"})

    check("空中文名不受影响",
          disamb({"place_order_lola": "", "need_pick_driver_reject": ""}),
          {"place_order_lola": "", "need_pick_driver_reject": ""})

    check("空名 + 有名混着，只有重名的才动",
          disamb({"a_x_amount": "金额", "b_x_amount": "金额", "lon": ""}),
          {"a_x_amount": "金额（a）", "b_x_amount": "金额（b）", "lon": ""})


# ═══════════════════════════════════════════════════════════
# ④ 修饰词匹配的优先级（长词优先，且必须有区分度）
# ═══════════════════════════════════════════════════════════
def test_pick_modifier():
    print()
    print("─" * 68)
    print("④ 挑修饰词：长组合优先；没区分度的不用")
    print("─" * 68)

    #   current_month 应该整体翻成"本月"，而不是用更短的 current（"当前"）
    cn, _ = colname.pick_modifier("current_month_amount", ["prev_month_amount"])
    check("current_month 整体匹配（不是 current）", cn, "本月")

    #   "month" 在两边都有 → 没有区分度，不能选它
    cn, _ = colname.pick_modifier("this_month_amount", ["last_month_amount"])
    check("this_month（不是 month）", cn, "本月")

    #   完全没有可用修饰词
    cn, _ = colname.pick_modifier("abc_amount", ["def_amount"])
    check("无可用修饰词 → None", cn, None)


# ═══════════════════════════════════════════════════════════
# ⑤ 列名词级翻译（计算列的兜底中文名）
# ═══════════════════════════════════════════════════════════
def test_translate():
    print()
    print("─" * 68)
    print("⑤ 计算列兜底：按列名逐词翻译（翻不了就保持英文，不做半中半英）")
    print("─" * 68)

    cases = [
        # ★ 用户截图里的那两列 —— 这两条必须过
        ("driver_count", "司机数"),
        ("month", "月份"),
        ("order_count", "订单数"),
        ("finish_rate", "完成率"),
        ("cancel_rate", "取消率"),
        ("avg_distance", "平均距离"),
        ("total_amount", "总金额"),
        ("driver_id", "司机"),          # id 翻成空串，不留"司机编号"的啰嗦
        # 翻不了 → 保持英文原样（不做"司机count"这种半中半英）
        ("place_order_lola", ""),
        ("top10_city", ""),
        ("id", ""),                     # 只产出空串 = 没翻出中文
        ("", ""),
        (None, ""),
    ]
    for col, expect in cases:
        check(f"{col!r} → {expect!r}", colname.translate(col), expect)

    #   底线：凡是翻出结果的，必须真的是中文（不能返回纯英文）
    for col in ["driver_count", "month", "order_count", "finish_rate"]:
        out = colname.translate(col)
        check(f"  ↳ {col} 翻出来是中文", bool(out) and not out.isascii(), True)


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print("╔" + "═" * 66 + "╗")
    print("║" + "结果列中文名测试（消歧 + 计算列兜底）".center(56) + "║")
    print("╚" + "═" * 66 + "╝")

    test_disambiguate_zh()
    test_disambiguate_fallback()
    test_no_false_disambiguation()
    test_pick_modifier()
    test_translate()

    print()
    print("=" * 68)
    total = len(PASSED) + len(FAILED)
    if not FAILED:
        print(f"🎉 全部通过（{len(PASSED)}/{total}）")
    else:
        print(f"⚠️ 通过 {len(PASSED)}/{total}，失败项：")
        for f in FAILED:
            print(f"   ❌ {f}")
    print("=" * 68)
    # ★ 失败必须让进程返回非 0 —— 否则 CI/上层 runner 会把失败的测试当成功（曾经就是这样漏掉的）
    raise SystemExit(1 if FAILED else 0)
