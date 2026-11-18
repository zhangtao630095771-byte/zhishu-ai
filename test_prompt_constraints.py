# -*- coding: utf-8 -*-
"""
test_prompt_constraints.py —— 「同名字段跨表串味」护栏测试

★ 这一组测试在防什么？
  2026-10-27 复核知识库时抓到一条真实的错知识：
    询价表 ods_tu_order_pricing_record_all 的 carstate（车况）
    被写成了订单表的「订单流转状态」枚举，两张表的注释几乎一字不差。
  这类错误的可怕之处在于【静默】：SQL 语法正确、跑得出数、不报任何错，
  只是数字是错的。用户看到一张正常的表，不会想到去怀疑字段含义。

  修复分两层：
    ① 抽取侧（suggest.py）——生成字段注释时，先确定性比对
       "注释声称的编码" vs "本表真实取值"，对不上就判定串味、剥掉枚举段；
    ② 查询侧（prompt.py）——把系统级硬约束 + 本次的同名跨表字段清单
       一起注入 System Prompt，让模型带着"这两个 carstate 不是一回事"的前提写 SQL。

  本文件测的是第 ② 层（纯逻辑，不连库、不调模型），外加第 ① 层里
  可独立验证的剥壳函数。

★ 为什么"两条流水线都要接"也要测？
  这个项目已经因为"两条流水线行为漂移"栽过一次：v2 检索了字典却没注入 prompt，
  页面看着正常、就是答不对。硬约束如果只接在 legacy 的 build_system_prompt 里，
  v2 就完全不受保护 —— 所以这里额外用源码断言钉住 v2 的接线。

运行： .venv/bin/python test_prompt_constraints.py
"""
import prompt
import suggest


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


# ═══════════════════════════════════════════════════════════
# 造两个"同名不同义"的表：完全复刻真实踩坑的那一对
# ═══════════════════════════════════════════════════════════
#   ★ 注意这个坑的真实形态：两条注释的【前半句一模一样】（都写「车辆状况」），
#     只有后面的枚举不同。只看前缀根本看不出错，所以测试也照这个形态造。
def _order_table():
    """订单表：carstate 是车况（正确的一方）"""
    return {
        "name": "dwd_tu_order_all",
        "columns": (
            "- id (String)   订单号\n"
            "- carstate (Nullable(Int32))   车辆状况（0=未知，1=能启动，2=不能启动）\n"
            "- create_time (Int32)   创建时间\n"
        ),
        "column_list": ["id", "carstate", "create_time"],
    }


def _pricing_table():
    """询价表：前缀也写「车辆状况」，枚举却是订单流转码 —— 被搬错了表"""
    return {
        "name": "ods_tu_order_pricing_record_all",
        "columns": (
            "- carstate (Nullable(Int32))   车辆状况（0=未知，1=待派单，2=已派单，3=司机已接单）\n"
            "- pricing_time (DateTime)   询价时间\n"
        ),
        "column_list": ["carstate", "pricing_time"],
    }


# ═══════════════════════════════════════════════════════════
# ① 字段行解析：注入文本 → {字段名: 注释}
# ═══════════════════════════════════════════════════════════
def test_parse_columns():
    print()
    print("─" * 68)
    print("① 表结构文本解析（字段名 / 类型 / 注释）")
    print("─" * 68)

    cols = prompt._parse_table_columns(_order_table())
    check("解析出 3 个字段", sorted(cols), ["carstate", "create_time", "id"])
    check_true("carstate 注释含枚举",
               "1=能启动" in cols.get("carstate", ""), cols.get("carstate"))

    #   类型里带嵌套括号（Nullable(Int32)）是很常见的真实现象，
    #   按"第一个右括号"切会把注释切坏，必须能处理。
    check_true("嵌套括号类型不破坏注释解析",
               cols.get("carstate", "").startswith("车辆状况"), cols.get("carstate"))

    #   注释本身带括号（中文括号、英文括号都算），不能被当成类型结束
    t = {"name": "t", "columns": "- stat (Int32)   状态(0=关, 1=开)"}
    got = prompt._parse_table_columns(t).get("stat")
    check("注释自带括号时保留完整", got, "状态(0=关, 1=开)")

    #   脏行（空行、非字段行）不能抛异常 —— 这段是护栏，不该把主流程搞挂
    t2 = {"name": "t", "columns": "\n\n表头乱入\n- a (Int32)\n"}
    check("脏文本只取合法字段行", list(prompt._parse_table_columns(t2)), ["a"])
    check("空表结构返回空字典", prompt._parse_table_columns({}), {})


# ═══════════════════════════════════════════════════════════
# ② 同名跨表字段识别
# ═══════════════════════════════════════════════════════════
def test_detect_same_name():
    print()
    print("─" * 68)
    print("② 同名跨表字段识别（只看本次注入的表）")
    print("─" * 68)

    tables = [_order_table(), _pricing_table()]
    names = ["dwd_tu_order_all", "ods_tu_order_pricing_record_all"]
    same = prompt._detect_same_name_columns(tables, names)
    got_fields = [c for c, _ in same]
    check("只认出 carstate（id/create_time 是技术字段，已排除）", got_fields, ["carstate"])
    occ = dict(same)["carstate"]
    check("carstate 归属两张表", sorted(t for t, _ in occ),
          ["dwd_tu_order_all", "ods_tu_order_pricing_record_all"])
    check_true("带出订单表那套（车况）",
               "1=能启动" in dict(occ)["dwd_tu_order_all"],
               dict(occ)["dwd_tu_order_all"])
    check_true("带出询价表那套（订单流转码，被搬错了表）",
               "1=待派单" in dict(occ)["ods_tu_order_pricing_record_all"],
               dict(occ)["ods_tu_order_pricing_record_all"])

    #   ★ 本次只用了一张表时不该报警：否则每次单表查询都糊一堆无关警告，
    #     真问题反而被埋掉（"狼来了"效应）。
    only_one = prompt._detect_same_name_columns(tables, ["dwd_tu_order_all"])
    check("只注入一张表时不报同名", only_one, [])

    #   技术字段（id / create_time）全库同义，不参与提示
    tech = prompt._detect_same_name_columns(
        [{"name": "a", "columns": "- id (String)\n- create_time (Int32)"},
         {"name": "b", "columns": "- id (String)\n- create_time (Int32)"}],
        ["a", "b"])
    check("纯技术字段不产生提示", tech, [])


# ═══════════════════════════════════════════════════════════
# ③ 跨表提示文本
# ═══════════════════════════════════════════════════════════
def test_cross_table_block():
    print()
    print("─" * 68)
    print("③ 跨表提示文本（点名到表，不给空话）")
    print("─" * 68)

    tables = [_order_table(), _pricing_table()]
    names = ["dwd_tu_order_all", "ods_tu_order_pricing_record_all"]
    txt = prompt._build_cross_table_block(tables, names)

    check_true("含 carstate 条目", "- carstate：" in txt)
    check_true("列出订单表名", "dwd_tu_order_all：" in txt)
    check_true("列出询价表名", "ods_tu_order_pricing_record_all：" in txt)
    check_true("附使用规则", "本表无注释时不要猜枚举" in txt)
    #   两条注释不同 → 这是"真不同义"，不该误标成复制串味
    check_true("注释不同时不误报'复制串味'", "复制串味" not in txt, txt[:200])

    #   注释一字不差 → 正是"整表复制"的指纹（carstate 当初就是这么错的）
    twin_a = {"name": "a", "columns": "- carstate (Int32)   订单流转状态（2=待接单，7=已取消）"}
    twin_b = {"name": "b", "columns": "- carstate (Int32)   订单流转状态（2=待接单，7=已取消）"}
    dup = prompt._build_cross_table_block([twin_a, twin_b], ["a", "b"])
    check_true("注释完全一致时提示复制串味", "复制串味" in dup, dup[:300])

    #   没有同名跨表字段 → 一个字都不出现（不留空标题，否则模型会去找不存在的东西）
    check("无重名时返回空串",
          prompt._build_cross_table_block([_order_table()], ["dwd_tu_order_all"]), "")
    check("不传表时返回空串", prompt._build_cross_table_block(), "")


# ═══════════════════════════════════════════════════════════
# ④ 系统级硬约束
# ═══════════════════════════════════════════════════════════
def test_hard_constraints():
    print()
    print("─" * 68)
    print("④ 系统级硬约束（代码内置，配置库删不掉）")
    print("─" * 68)

    h = prompt.HARD_CONSTRAINTS
    check_true("有 H1 同名不等于同义", "H1." in h)
    check_true("有 H2 注释编码只覆盖本字段", "H2." in h)
    check_true("有 H3 以本表业务为准", "H3." in h)
    check_true("写明 carstate 真实案例", "carstate" in h)
    check_true("写明询价表名", "ods_tu_order_pricing_record_all" in h)
    check_true("禁止跨表套用", "禁止跨表套用" in h)
    check_true("无注释时不许猜枚举", "不许猜枚举" in h)
    check_true("给出可执行自查动作", "自查动作" in h)

    #   ★ 即使一张表都没哈希中，硬约束也必须在 ——
    #     它是兜底条款，不能因为"本次没检测到重名"就整体消失。
    blk = prompt.build_hard_constraints_block([], [])
    check("无表时仍返回硬约束", blk, prompt.HARD_CONSTRAINTS)

    tables = [_order_table(), _pricing_table()]
    names = ["dwd_tu_order_all", "ods_tu_order_pricing_record_all"]
    blk2 = prompt.build_hard_constraints_block(tables, names)
    check_true("有重名时硬约束 + 清单都拼上",
               blk2.startswith(prompt.HARD_CONSTRAINTS) and "⚠️ 同名跨表字段" in blk2)


# ═══════════════════════════════════════════════════════════
# ⑤ 字典的"表作用域"警告
# ═══════════════════════════════════════════════════════════
def test_dict_scope_warning():
    print()
    print("─" * 68)
    print("⑤ 字典表作用域（field_hint 落在多张表时必须点名）")
    print("─" * 68)

    d = {"name": "车况", "field_hint": "carstate",
         "items": [{"value": "0", "label": "能启动"}, {"value": "1", "label": "不能启动"}],
         "enabled": True}
    tables = [_order_table(), _pricing_table()]
    names = ["dwd_tu_order_all", "ods_tu_order_pricing_record_all"]

    txt = prompt._build_dict_block([d], ["车况"], "询价车况", tables=tables, table_names=names)
    check_true("落两张表时点名警告", "多张表里都出现" in txt, txt[:400])
    check_true("警告里带订单表名", "dwd_tu_order_all" in txt)
    check_true("警告里带询价表名", "ods_tu_order_pricing_record_all" in txt)
    check_true("给出'不确定就别用'的兜底",
               "不要用这套编码做过滤" in txt, txt[:400])

    #   只落一张表 → 不该有这条警告（否则噪声淹没真信息）
    one = prompt._build_dict_block([d], ["车况"], "订单车况",
                                   tables=tables, table_names=["dwd_tu_order_all"])
    check_true("落单张表时不警告", "多张表里都出现" not in one, one[:300])

    #   老调用方不传 tables → 行为保持不变（向后兼容）
    old = prompt._build_dict_block([d], ["车况"], "车况")
    check_true("不传 tables 时保持老行为", "多张表里都出现" not in old)
    check_true("不传 tables 时字典内容照旧", "0=能启动" in old)


# ═══════════════════════════════════════════════════════════
# ⑥ 硬约束块（共享组件）：必须含 H1 + 同名跨表清单 + 末尾收口
#   ★ 双流水线收敛后 prompt.build_system_prompt 已退役；硬约束由
#     build_hard_constraints_block 产出，被统一主流程 agent._build_system_prompt 复用。
# ═══════════════════════════════════════════════════════════
def test_hard_constraints_block_end():
    print()
    print("─" * 68)
    print("⑥ 硬约束块（共享组件）：H1 + 同名跨表清单 + 末尾收口")
    print("─" * 68)

    tables = [_order_table(), _pricing_table()]
    names = ["dwd_tu_order_all", "ods_tu_order_pricing_record_all"]
    sp = prompt.build_hard_constraints_block(tables, names)

    check_true("含 H1 硬约束", "H1." in sp)
    check_true("含本次同名跨表清单", "⚠️ 同名跨表字段" in sp)
    check_true("硬约束在整段末尾收口", sp.strip().endswith(
        "该字段注释不可采信。") or "H3." in sp[sp.rindex("H1."):])


# ═══════════════════════════════════════════════════════════
# ⑦ 接线守卫：统一主流程必须接上硬约束与字典表作用域
# ═══════════════════════════════════════════════════════════
def test_wiring():
    print()
    print("─" * 68)
    print("⑦ 接线守卫（统一主流程不能漏）")
    print("─" * 68)

    agent_src = open("agent.py", encoding="utf-8").read()
    check_true("统一主流程: agent.py 调用了硬约束",
               "prompt.build_hard_constraints_block(" in agent_src,
               "统一主流程的 system_prompt 组装在 agent._build_system_prompt，不调这里就完全不受约束")
    #   ★ 这条断言针对的是一类具体的回归：v2 曾经检索了字典却没注入 prompt，
    #     导致"配了字典模型看不到"。硬约束如果也不接，历史会重演。
    check_true("统一主流程: 字典块也传了 tables（表作用域警告生效）",
               "table_names=resolved.get(\"tables\")" in agent_src)


# ═══════════════════════════════════════════════════════════
# ⑧ 抽取侧：串味注释的枚举段必须能剥掉
# ═══════════════════════════════════════════════════════════
def test_strip_enum():
    print()
    print("─" * 68)
    print("⑧ 抽取侧：剥掉串味的枚举段，保住业务描述")
    print("─" * 68)

    cases = [
        #  典型串味：业务描述「车况」是对的，后面那串订单状态码是抄来的
        ("车况（1=待接单，2=已接单，3=已完成）", "车况", "中文括号枚举"),
        ("车况(1=待接单，2=已接单)", "车况", "英文括号枚举"),
        ("订单状态：1待接单，2已接单", "订单状态：1待接单，2已接单", "无括号不动"),
        ("金额（单位：元）", "金额（单位：元）", "非枚举括号不能误剥"),
        ("", "", "空注释"),
    ]
    for raw, expected, note in cases:
        check(f"剥壳 · {note}", suggest._strip_enum_parts(raw), expected)

    #   ★ 剥壳的边界：宁可剥不干净，也不能把「（单位：元）」这种正常说明剥掉 ——
    #     剥错了等于凭空删掉人写的信息，比留着脏数据更难被发现。
    check_true("保底：正常说明不被破坏",
               suggest._strip_enum_parts("日租金（元/天）") == "日租金（元/天）")


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print("╔" + "═" * 60 + "╗")
    print("║" + "Prompt 硬约束 · 跨表串味护栏测试".center(46) + "║")
    print("╚" + "═" * 60 + "╝")

    test_parse_columns()
    test_detect_same_name()
    test_cross_table_block()
    test_hard_constraints()
    test_dict_scope_warning()
    test_hard_constraints_block_end()
    test_wiring()
    test_strip_enum()

    print()
    print("=" * 62)
    total = len(PASSED) + len(FAILED)
    if not FAILED:
        print(f"🎉 全部通过（{len(PASSED)}/{total}）")
    else:
        print(f"⚠️ 通过 {len(PASSED)}/{total}，失败项：")
        for f in FAILED:
            print(f"   ❌ {f}")
    print("=" * 62)
    # ★ 失败必须返回非 0，否则上层 runner 会把失败当成功（这个坑踩过）
    raise SystemExit(1 if FAILED else 0)
