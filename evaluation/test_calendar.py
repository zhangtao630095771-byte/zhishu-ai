#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_calendar.py —— 日历 / 节假日解析的【确定性】回归测试

★ 为什么单独放一份"不带大模型"的测试？
  节日 / 假期解析是**纯函数**：给定 (问题, 今天) 就应得到一个确定的日期区间。
  拿 LLM 评测体系去测它，既慢又要花钱，而且模型输出一波动，
  就分不清"是日期解析错了"还是"模型这一轮发挥不好"。
  这类纯逻辑用断言最快也最准。

  系统级效果（模型有没有把区间真正用进 SQL）仍由 golden / regression 数据集负责，
  两边分工：这里管"算得对不对"，数据集管"用没用对"。

★ 背景（线上反馈驱动）：
  反馈 #10「查询分析国庆假期期间小板订单的数据」被解析成「国庆节当天 = 10-01」，
  用户明确指出"未理解国庆假期期间的日期范围"。
  根因：只认【节日当天】这个"点"，没有【放假区间】这个"区间"的概念。
  本测试把「假期 → 区间」的口径固定下来，防止以后改回归。

用法：
    .venv/bin/python evaluation/test_calendar.py
"""
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import calendar_util as cu  # noqa: E402

TODAY = date(2026, 10, 8)   # 固定"今天"，测试结果才不会随运行日期漂移

_fail = []


def _parse(q):
    """问题 → {表达式: (start, end)}，只保留解析成功的"""
    return {e["expr"]: (e["start"], e["end"])
            for e in cu.parse_time_expressions(q, TODAY) if e["start"]}


def case(desc, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {desc}")
    if not ok:
        print(f"        期望 {want}")
        print(f"        实际 {got}")
        _fail.append(desc)


def main():
    print("═" * 66)
    print("① 节日 vs 假期：点与区间的区别（本测试的核心）")
    print("═" * 66)

    # 「国庆假期」= 放假区间（当年无官方表 → 按常规 7 天估算）
    case("「国庆假期期间」应解析为整段假期，不是 10-01 单日",
         _parse("查询分析国庆假期期间小板订单的数据")["国庆假期期间"],
         (date(2026, 10, 1), date(2026, 10, 7)))

    # 「国庆节当天」仍是一个点 —— 假期逻辑不能反过来污染它
    case("「国庆节当天」仍应解析为单日",
         _parse("国庆节当天发单量")["国庆节"],
         (date(2026, 10, 1), date(2026, 10, 1)))

    # 别名「十一黄金周」本身就含"黄金周" → 也应按假期区间理解
    case("「十一黄金周」应按假期区间处理",
         _parse("十一黄金周每天订单量")["十一黄金周"],
         (date(2026, 10, 1), date(2026, 10, 7)))

    case("「春节假期」应按该年春节起算的区间",
         _parse("春节假期完单率")["春节假期"],
         (date(2026, 2, 17), date(2026, 2, 23)))

    # 没有"放假"概念的节日写了"假期" → 老实退化成当天，不编一个区间
    case("「情人节假期」无放假安排 → 退化为当天（不凭空造区间）",
         _parse("情人节假期的订单量")["情人节假期"],
         (date(2026, 2, 14), date(2026, 2, 14)))

    print()
    print("═" * 66)
    print("② 逐年官方安排表（holidays）优先于常规估算")
    print("═" * 66)

    # 2025 国庆与中秋（10-06）连休 → 8 天
    case("2025 年国庆假期应取官方安排 10-01~10-08（不是估算的 7 天）",
         _parse("去年国庆假期发单完单率")["去年国庆假期"],
         (date(2025, 10, 1), date(2025, 10, 8)))

    case("2024 年国庆假期应取官方安排 10-01~10-07",
         _parse("2024年国庆假期订单量")["2024年国庆假期"],
         (date(2024, 10, 1), date(2024, 10, 7)))

    # source 必须区分"官方表"与"估算"—— 注入文本要如实告诉模型/用户
    case("get_holiday_range(2025 国庆) 来源应为 table",
         cu.get_holiday_range("国庆节", 2025)[2], "table")
    case("get_holiday_range(2026 国庆) 来源应为 default（估算）",
         cu.get_holiday_range("国庆节", 2026)[2], "default")
    case("get_holiday_range(情人节) 无假期概念 → None",
         cu.get_holiday_range("情人节", 2026), None)

    print()
    print("═" * 66)
    print("③ 回归：原有时间表达不能被假期逻辑改坏")
    print("═" * 66)

    r = _parse("25号前7天同比去年中秋节前7天发单完单率")
    case("「去年中秋节前7天」= 节前 7 天（不含节日当天）",
         r.get("去年中秋节前7天"), (date(2025, 9, 29), date(2025, 10, 5)))
    case("「25号前7天」仍按当月该日推算",
         r.get("25号前7天"), (date(2026, 10, 18), date(2026, 10, 24)))

    case("「国庆后3天」= 节后 3 天（不含节日当天）",
         _parse("国庆后3天的订单量")["国庆后3天"],
         (date(2026, 10, 2), date(2026, 10, 4)))

    case("普通月份表达不受影响",
         _parse("查询2026年9月的订单量")["2026年9月"],
         (date(2026, 9, 1), date(2026, 9, 30)))

    print()
    print("═" * 66)
    if _fail:
        print(f"❌ {len(_fail)} 条失败：")
        for d in _fail:
            print("   -", d)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
