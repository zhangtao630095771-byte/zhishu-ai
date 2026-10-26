# -*- coding: utf-8 -*-
"""
test_gaps.py —— 知识缺口扫描的判断逻辑测试

★ 为什么这个必须测？
  缺口清单是给人"照着补"的行动指引 —— 它的价值全在【准】上：
    误报（明明写了取值却报出来）→ 用户看两条就不信这份清单了
    漏报（该报的没报）        → 隐患永远发现不了
  而判断逻辑是【启发式】，靠正则和关键词，最容易在真实数据上出错。

  实测踩过：第一版 `_mentions_values` 的分隔符字符类漏了「.」，
  而本库大量注释写成"1.线上收款 2.线下收款"这种点号分隔 ——
  结果 24 个缺口里大部分是误报。

★ 本测试不连数据库、不加载模型，毫秒级跑完。

运行： .venv/bin/python test_gaps.py
"""
import enum_parse
import gaps


# ═══════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════
PASSED, FAILED = [], []


def check(label, got, expected):
    ok = (got == expected)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望: {expected}")
        print(f"       实际: {got}")


# ═══════════════════════════════════════════════════════════
# ① 注释里是否说明了取值含义（决定报不报缺口）
# ═══════════════════════════════════════════════════════════
def test_mentions_values():
    print()
    print("─" * 68)
    print("① 注释是否说明了取值含义（判错 = 误报或漏报）")
    print("─" * 68)

    # 下面这些【写了】取值含义 → 不该报缺口
    wrote = [
        "司机收款类型（1.线上收款 2.线下收款）",       # ★ 点号分隔，曾误报
        "支付状态：1.未支付 2.已支付",                # ★ 点号分隔
        "特殊订单类型  0.普通订单 1.特殊订单A",        # ★ 点号分隔
        "是否需要牵引车（1.是 2.否）",
        "订单状态，1待付款，2待接单，3待装车",          # 逗号分隔
        "司机类型，1内部司机，2普通司机，3vip",
        "订单状态：2待接单 3已接单 4配送中",            # 空格分隔
        "外部渠道来源 1=G车联 2=其他",                 # 等号分隔
        "客户类型 1=个人 2=企业",
    ]
    # 下面这些【没写】取值含义 → 该报缺口
    not_wrote = [
        "订单状态",                                  # 只说字段是什么
        "客户类型",
        "是否注销",
        "订单id",
        "订单成交金额（客户支付总额，单位元）",
        "是否有司机接单,有司机接单的订单量，接单订单",   # is_picked，没说 0/1
        "外部渠道来源 1=G车联",                       # 只说明了一个值 → 仍算缺口
        "",
        None,
    ]

    for text in wrote:
        check(f"写了取值 → 不报：「{(text or '')[:26]}」",
              gaps._mentions_values(text), True)
    for text in not_wrote:
        check(f"没写取值 → 报：「{(text or '(空)')[:26]}」",
              gaps._mentions_values(text), False)


# ═══════════════════════════════════════════════════════════
# ①b 注释是否【覆盖】真实取值 —— 单一判据
#
# ★ 为什么要给这个判据单独写测试？
#   它同时决定两件事：AI 生成的注释能不能落库、这个字段算不算缺口。
#   一旦它漂移，两个地方会同时出错，而且是"自相矛盾"式的错
#   （AI 说生成成功，缺口页却说这条还是缺口），用户会直接失去信任。
#   这是一处实现、两处语义，必须用测试钉住。
# ═══════════════════════════════════════════════════════════
def test_covers_values():
    print()
    print("─" * 68)
    print("①b 注释是否覆盖了每一个真实取值（=1 覆盖 / ≠1 缺口）")
    print("─" * 68)

    cases = [
        # (注释, 真实取值, 期望, 说明)
        ("客户类型（1=个人客户，3=企业客户）", ["1", "3"], True, "全部覆盖"),
        ("外部渠道来源（1=G车联）", ["1", "3"], False, "漏了 3"),
        ("保险费状态（0=未投保，1=已投保，2=投保中，3=投保失败）",
         ["0", "1", "2", "3"], True, "四个值都说清"),

        # ── 字符串枚举：parse_values 认不出（只认数字），covers_values 必须认 ──
        ("外部机构渠道code（空=非外部机构渠道，alipay=支付宝渠道）",
         ["", "alipay"], True, "★字符串枚举 + 空值"),
        ("外部机构渠道code（空=非外部机构渠道）",
         ["", "alipay"], False, "漏了 alipay"),

        # ── 多选字段：值是组合串，逐个原子查才不会永久误报 ──
        ("小板支付方式 0 现付 1 到付 支持多个用逗号隔开",
         ["0", "0,1"], True, "★逗号分隔的多选"),
        ("支付模式，1全额支付，2付定金，3到付",
         ['["1","2","3"]', '["1"]'], True, "★JSON 数组多选"),
        ("发单限制 0:小板 1 大板 3代驾 支持多个用逗号隔开",
         ["0", "0,1,2", "2"], False, "组合里出现了没解释的 2"),

        # ── 空值必须被显式说明（'' in 任意文本 恒真，不特判等于没校验）──
        ("支付模式，1全额支付，2付定金，3到付",
         ["", "1", "2"], False, "★没说空值是什么意思"),

        # ── 真实踩到的人工注释：把 1~7 写成了 2~7 ──
        ("订单状态：2待接单 3已接单 4配送中 5已送达 6已完成 7已取消",
         ["1", "2", "3", "4", "5", "6", "7"], False, "★人工注释漏了 status=1"),

        ("", ["1", "3"], False, "空注释"),
        (None, ["1", "3"], False, "无注释"),
        ("抗氧化专线的处理方式", [], False, "没有取值时不算覆盖"),
    ]

    for comment, values, expected, note in cases:
        label = f"{note}：「{(comment or '(空)')[:22]}」"
        check(label, enum_parse.covers_values(comment, values), expected)


# ═══════════════════════════════════════════════════════════
# ①c 注释里声称、但真实数据没有的【幽灵值】（过期注释 / 写错）
#
# ★ 这是「覆盖判据」的镜像：covers_values 管"漏了哪些值"，
#   mentioned_codes 管"哪些值是注释瞎说的"。
#   两者一起把"注释 vs 真实数据"的偏差钉成两道独立测试，
#   免得哪天 gaps 或 enum_parse 一改，漏掉方向就没人发现。
# ═══════════════════════════════════════════════════════════
def test_mentioned_codes():
    print()
    print("─" * 68)
    print("①c 注释声称的编码 vs 真实取值 → 幽灵值（写错/过期的注释）")
    print("─" * 68)

    cases = [
        # (注释, 真实取值, 期望幽灵值集合, 说明)
        ("发单限制 0:小板 1 大板 3代驾 支持多个用逗号隔开",
         ["0", "0,1,2", "2"], {"3"}, "★真实踩到的：注释说 3，数据只有 0,1,2"),
        ("特殊订单类型 0.普通订单 1.特殊订单A 2.特殊订单B",
         ["0", "2"], {"1"}, "提到不存在的 1"),
        ("支付状态：0未支付 1已支付",
         ["0", "1", "4", "5", "6"], set(), "全都在数据里，无幽灵值"),
        ("外部机构渠道code（空=非外部机构渠道，alipay=支付宝渠道）",
         ["", "alipay"], set(), "★字符串枚举认不出编码，不误报"),
        ("抗氧化专线的处理方式", ["0", "1"], set(), "纯文字注释，无数字编码"),
    ]

    for comment, values, expected, note in cases:
        real = set()
        for v in values:
            real.update(enum_parse.components(str(v)))
        got = enum_parse.mentioned_codes(comment) - real - {""}
        label = f"{note}：「{(comment or '(空)')[:20]}」"
        check(label, got, expected)


def test_scan_flags_stale():
    """端到端：真实库里能扫出至少一条 stale（避免幽灵值检测形同虚设）。"""
    print()
    print("─" * 68)
    print("①d scan 能检出 stale（注释有错值）")
    print("─" * 68)
    try:
        import config_store, knowledge, config
        business = knowledge.business_tables()
        visible = sorted(config_store.filter_visible_tables(
            set(business), config.ACL_DEFAULT_VISIBLE))
        r = gaps.scan(visible)
        stale = [i for i in r["items"] if i["severity"] == "stale"]
        check("能扫到 stale 条目（真实库）", len(stale) > 0, True)
        print(f"    （共 {len(stale)} 条 stale，示例："
              + "，".join(f"{i['table']}.{i['column']}={i['ghost']}" for i in stale[:3]))
    except Exception as e:
        check(f"真实库扫描抛错：{e}", True, False)


# ═══════════════════════════════════════════════════════════
# ② 哪些字段值得去查 distinct（粗筛）
# ═══════════════════════════════════════════════════════════
def test_is_candidate():
    print()
    print("─" * 68)
    print("② 候选字段粗筛（漏掉就永远扫不到，多筛只是慢一点）")
    print("─" * 68)

    should = [
        ("status", "Int8"),
        ("pay_status", "Int8"),
        ("vehicle_type", "String"),
        ("is_delete", "Int8"),            # is_ 开头
        ("app_entity_source", "Int32"),
        ("driver_level", "Int8"),
        ("order_category", "Int8"),
    ]
    should_not = [
        ("total", "Decimal(10, 2)"),      # 金额，不可能是枚举
        ("ctime", "Int32"),               # 时间戳
        ("carload_time_start", "Int32"),  # 时间
        ("order_desc", "String"),         # 名字不像枚举（没命中关键词）
        ("driver_name", "String"),
        ("status_date", "Date"),          # 类型就该排除
        ("tags", "Array(String)"),
    ]

    for col, ty in should:
        check(f"该扫：{col} ({ty})", gaps._is_candidate(col, ty), True)
    for col, ty in should_not:
        check(f"不扫：{col} ({ty})", gaps._is_candidate(col, ty), False)


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print("╔" + "═" * 66 + "╗")
    print("║" + "知识缺口扫描 · 判断逻辑测试".center(56) + "║")
    print("╚" + "═" * 66 + "╝")

    test_mentions_values()
    test_covers_values()
    test_mentioned_codes()
    test_scan_flags_stale()
    test_is_candidate()

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
