# -*- coding: utf-8 -*-
"""
test_acl.py —— 权限体系专项测试（表级 + 指标级）

★ 为什么权限要单独测？
  和 test_guard.py 是同一个理由：
    业务错了 → 用户会用出来 → 会反馈
    权限错了 → 【没有任何症状】→ 直到出事那天才发现
  而"数据被越权查走"是不可逆的，所以权限必须有专门的、可反复跑的测试。

★ 这个测试不调大模型、不联网，几秒钟跑完，可以随时跑。

运行： .venv/bin/python test_acl.py
"""
import config                                # 全局配置（默认可见策略）
import config_store                          # 配置存储（读写权限、指标状态）
import knowledge                             # 知识访问层
import rag                                   # 检索层（被测的拦截函数）


# ═══════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════
PASSED = []                                  # 记录通过数
FAILED = []                                  # 记录失败项


def check(label, got, expected):
    """断言一个结果，记录下来"""
    ok = (got == expected)
    (PASSED if ok else FAILED).append(label)
    mark = "✅" if ok else "❌"
    print(f"  {mark} {label}")
    if not ok:
        print(f"       期望: {expected}")
        print(f"       实际: {got}")


def build_fake_index():
    """
    手工拼一个"够用"的索引字典

    ★ 为什么要造假 index？
      因为 match_disabled_metric 只用到 index 里的 metrics / disabled_metrics 两个键，
      根本不需要向量、不需要模型。
      手工造一个就能测，省掉几十秒的模型加载 —— 测试跑得快，你才会愿意经常跑。
      （这叫"测试替身"，是单元测试的常规手法）
    """
    all_metrics = knowledge.get_all_metrics()
    return {
        "metrics": [m for m in all_metrics if m["enabled"]],
        "disabled_metrics": [m for m in all_metrics if not m["enabled"]],
    }


# ═══════════════════════════════════════════════════════════
# 第一部分：表级权限
# ═══════════════════════════════════════════════════════════
def test_table_acl():
    print()
    print("=" * 66)
    print("第一部分：表级权限（filter_visible_tables）")
    print("=" * 66)

    all_tables = {"dwd_order_all", "dim_user", "dim_driver", "dwd_refund", "dim_user_contact"}

    acl = config_store.get_table_acl()
    print(f"  当前白名单登记情况（{len(acl)} 条）：")
    for t, info in sorted(acl.items()):
        print(f"    {t:<20} {'允许' if info['visible'] else '禁止'}   {info.get('note','')}")
    print()

    # ── 黑名单模式（default_visible=True）──
    visible_blacklist = config_store.filter_visible_tables(all_tables, default_visible=True)
    check("黑名单模式：显式标 0 的敏感表被屏蔽",
          "dim_user_contact" in visible_blacklist, False)
    check("黑名单模式：张数 = 全部 - 被屏蔽的",
          len(visible_blacklist),
          len(all_tables) - len([t for t, i in acl.items()
                                 if not i["visible"] and t in all_tables]))

    # ── 白名单模式（default_visible=False）—— 真实库应该用这个 ──
    visible_whitelist = config_store.filter_visible_tables(all_tables, default_visible=False)
    explicitly_allowed = {t for t, i in acl.items() if i["visible"]}
    check("白名单模式：只放行【显式登记为允许】的表",
          visible_whitelist, all_tables & explicitly_allowed)
    #   ★ 注意这里不能硬编码"期望 4 张表"——
    #     白名单模式的语义是"没登记的一律不可见"，
    #     所以期望值应该【从当前名单算出来】，而不是写死。
    #     （写死的话，你改一次权限测试就红了，这种测试没人愿意跑）

    check("白名单模式：从没登记过的表默认不可见",
          config_store.filter_visible_tables({"never_registered_xyz"}, False),
          set())

    # ── ★ 模拟"只开放几张表"的完整流程（你接真实库要做的就是这个）──
    print()
    print("  ── 模拟：临时把 dwd_order_all 加入白名单 ──")
    acl_before = config_store.get_table_acl().get("dwd_order_all")   # 先备份原状态
    try:
        config_store.set_table_acl("dwd_order_all", True, "测试用")
        check("授权后 → 可见",
              "dwd_order_all" in config_store.filter_visible_tables(all_tables, False), True)

        config_store.set_table_acl("dwd_order_all", False, "测试用")
        check("撤权后 → 不可见",
              "dwd_order_all" in config_store.filter_visible_tables(all_tables, False), False)
    finally:
        # 还原成测试前的状态（有可能原来是"没登记"，那就要删掉这条记录）
        if acl_before is None:
            config_store.delete_table_acl("dwd_order_all")
        else:
            config_store.set_table_acl("dwd_order_all", acl_before["visible"],
                                       acl_before.get("note", ""))

    # ── 批量函数必须和单张判断函数结果一致（防止两套逻辑跑偏）──
    one_by_one = {t for t in all_tables
                  if config_store.is_table_visible(t, True)}
    check("批量判断 == 逐张判断（两套逻辑一致）",
          visible_blacklist, one_by_one)


# ═══════════════════════════════════════════════════════════
# 第二部分：指标级权限
# ═══════════════════════════════════════════════════════════
def test_metric_acl():
    print()
    print("=" * 66)
    print("第二部分：指标级权限（match_disabled_metric）")
    print("=" * 66)

    index = build_fake_index()
    print(f"  当前开放指标：{[m['name'] for m in index['metrics']]}")
    print(f"  当前停用指标：{[m['name'] for m in index['disabled_metrics']]}")

    # ── 全部开放时，什么都不该拦 ──
    for q in ["查询2026年7月客单价", "查询2026年7月完单率", "查询2026年7月订单量"]:
        check(f"全部开放时放行：{q}", rag.match_disabled_metric(index, q), None)

    # ── 停用「客单价」 ──
    config_store.set_metric_enabled("客单价", False)
    try:
        index = build_fake_index()
        check("停用后：问客单价 → 拦截",
              rag.match_disabled_metric(index, "查询2026年7月客单价"), "客单价")
        check("停用后：问订单量 → 仍放行",
              rag.match_disabled_metric(index, "查询2026年7月订单量"), None)
        check("停用后：问取消率 → 仍放行",
              rag.match_disabled_metric(index, "查询2026年7月取消率"), None)
    finally:
        config_store.set_metric_enabled("客单价", True)     # ★ 无论如何都还原
        #   finally = 不管上面有没有报错，这段一定执行。
        #   测试改动了系统状态，就必须保证能还原，否则会影响后续运行。

    # ── ★ 边界：短关键词不能误伤 ──
    print()
    print("  ── 边界测试：短关键词误伤 ──")
    config_store.set_metric_enabled("完单量", False)         # 它的关键词里有 "完单"（长度2）
    try:
        index = build_fake_index()
        check("停用「完单量」后：问完单量 → 拦截",
              rag.match_disabled_metric(index, "查询2026年7月完单量"), "完单量")
        check("★ 问「完单率」不能被「完单」误伤",
              rag.match_disabled_metric(index, "查询2026年7月完单率"), None)
        #   ↑ 这一条是最关键的：如果没有"最长关键词优先"，
        #     "完单" 这个短词会把合法的"完单率"也拦掉，系统就废了。
    finally:
        config_store.set_metric_enabled("完单量", True)

    # ── 指标名本身也算关键词（防止配置漏写关键词导致拦截失效）──
    print()
    print("  ── 边界测试：指标名兜底 ──")
    config_store.set_metric_enabled("GMV", False)
    try:
        index = build_fake_index()
        check("停用 GMV：问 GMV（大写） → 拦截",
              rag.match_disabled_metric(index, "查询2026年7月GMV"), "GMV")
        check("停用 GMV：问 gmv（小写） → 拦截",
              rag.match_disabled_metric(index, "查询2026年7月gmv"), "GMV")
    finally:
        config_store.set_metric_enabled("GMV", True)

    # ── 收尾：确认全部还原 ──
    print()
    left = [m["name"] for m in knowledge.get_disabled_metrics()]
    check("测试结束后所有指标已还原为开放", left, [])


# ═══════════════════════════════════════════════════════════
# 第三部分：真假指标 vs 正常指标 的完全隔离
# ═══════════════════════════════════════════════════════════
def test_index_isolation():
    print()
    print("=" * 66)
    print("第三部分：停用指标不能进入检索索引")
    print("=" * 66)

    config_store.set_metric_enabled("客单价", False)
    try:
        enabled = knowledge.get_metrics()
        check("停用的指标不在 get_metrics() 里",
              "客单价" in [m["name"] for m in enabled], False)
        check("停用的指标在 get_disabled_metrics() 里",
              "客单价" in [m["name"] for m in knowledge.get_disabled_metrics()], True)
        check("get_all_metrics() 仍能看到它（管理视图）",
              "客单价" in [m["name"] for m in knowledge.get_all_metrics()], True)
    finally:
        config_store.set_metric_enabled("客单价", True)


# ═══════════════════════════════════════════════════════════
# 第四部分：★ 配置一致性 —— 指标依赖的表必须都已授权
# ═══════════════════════════════════════════════════════════
def test_metric_tables_consistency():
    """
    每个"已开放"指标的依赖表，必须都在白名单里（也就是可见）。

    ★ 为什么必须有这条测试？
      真实踩过的坑：6 个指标全配成 dwd_order_all —— 一张真实库里根本
      不存在的表（教程遗留的名字）。连锁反应是：
        · 表检索召不回任何东西 → 界面显示"表:未匹配"
        · 指标却把这个"幽灵表名"带进了 prompt
        · 模型拿到一个查不到结构的表名 → 退回训练记忆编表 → 编出 orders
        · 最后被表级权限拦下，用户看到"越权访问被拦截"
      整条链路里【没有任何一处报错】，全是静默漂移。
      这条测试把问题提前到【配置阶段】暴露，而不是等用户提问才炸。
    """
    print()
    print("=" * 66)
    print("第四部分：指标依赖表必须都已授权（配置一致性）")
    print("=" * 66)

    # 黑名单模式下默认全部可见，这条检查没有意义
    if config.ACL_DEFAULT_VISIBLE:
        print("  ⏭️  当前是黑名单模式（默认可见），跳过该检查")
        return

    acl = config_store.get_table_acl()
    visible = {t for t, info in acl.items() if info["visible"]}

    metrics = knowledge.get_metrics()            # 只检查已开放的指标
    if not metrics:
        print("  ⏭️  当前没有开放的指标，跳过该检查")
        return

    for m in metrics:
        for t in m["tables"]:
            check(f"指标「{m['name']}」依赖的表 {t} 已授权", t in visible, True)


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print("╔" + "═" * 64 + "╗")
    print("║" + "权限体系专项测试（表级 + 指标级）".center(54) + "║")
    print("╚" + "═" * 64 + "╝")

    test_table_acl()
    test_metric_acl()
    test_index_isolation()
    test_metric_tables_consistency()

    print()
    print("=" * 66)
    total = len(PASSED) + len(FAILED)
    if not FAILED:
        print(f"🎉 全部通过（{len(PASSED)}/{total}）")
    else:
        print(f"⚠️ 通过 {len(PASSED)}/{total}，失败项：")
        for f in FAILED:
            print(f"   ❌ {f}")
    print("=" * 66)
    # ★ 失败必须让进程返回非 0 —— 否则 CI/上层 runner 会把失败的测试当成功（曾经就是这样漏掉的）
    raise SystemExit(1 if FAILED else 0)
