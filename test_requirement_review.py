# -*- coding: utf-8 -*-
"""
test_requirement_review.py —— 需求一致性审核（P0 确定性层）单元测试

★ 测试范围（与 docs/requirement_consistency_2026-10-30.md 的 5.1 一一对应）：
    ① 指标被删        ② 维度被误删      ③ 替换写成追加    ④ 过滤丢失
    ⑤ 合法修改（对照组，防误拦）        ⑥ 首轮空契约     ⑦ 注释里出现关键字
    ⑧ 非 SQL 输入降级 ⑨ 时间窗缺失      ⑩ 修改动词识别   ⑪ 成本闸门
    ⑫ 替换"未落实"（过滤被整条删掉）    ⑬ 指标替换不自相矛盾
    ⑭ 无字段名无数字的口径切换（靠 understanding 反推替换对）
  ⑫⑬⑭ 是 P2 阶段用真实模型评测（eval_modify_live.py）反查出来的三条规则空洞，
  每个都配了"防误拦"对照组 —— 新规则最容易犯的错是把正确 SQL 一起拦掉。
  全部为纯逻辑测试：不联网、不调模型、不碰数据库。
  运行： .venv/bin/python test_requirement_review.py
"""
import os
import sys

# 允许从项目根目录直接运行（与其它 test_*.py 一致）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requirement_review as RR          # noqa: E402  ← 被测对象

PASSED, FAILED = [], []


# ★ 本文件只测【确定性层】：显式关闭语义层。
#   否则每条用例都会去调真实模型（慢、要钱、结果还不确定），
#   而"语义层怎么接、怎么合并结论"由 test_gate_req.py 用替身覆盖。
_RR_REVIEW = RR.review


def _review(sql, contract):
    """调用确定性层（不含 LLM 语义裁决）。"""
    return _RR_REVIEW(sql, contract, use_llm=False)


def check(label, got, expected):
    """断言"实际 == 期望"，结果记入全局清单。"""
    ok = (got == expected)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望: {expected}")
        print(f"       实际: {got}")
    return ok


def check_true(label, cond, detail=""):
    """断言"条件为真"，失败时打印 detail 便于定位。"""
    ok = bool(cond)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok and detail != "":
        print(f"       实际: {detail}")
    return ok


# ═══════════════════════════════════════════════════════════
# 基准上下文：上一轮"注册客户数，按月份和应用主体分组"
#   （与 test_modify_query.py 完全一致，保证两个测试文件说的是同一件事）
# ═══════════════════════════════════════════════════════════
BASE_QUESTION = "注册客户数，按月份和应用主体分组"
BASE_UNDERSTANDING = {
    "intent": "summary",
    "metrics": ["注册客户数"],
    "entities": ["register"],
    "dimensions": ["月份", "应用主体"],
    "time_range": "2025-10-01 ~ 2026-11-01（左闭右开）",
    "filters": ["user_type = 1", "app_entity_source = 0"],
}
BASE_SQL = (
    "SELECT r.app_entity_source AS \"应用主体\", "
    "toStartOfMonth(r.create_time) AS \"月份\", "
    "count(*) AS \"注册客户数\" "
    "FROM ctc_data_warehouse.register r "
    "WHERE r.user_type = 1 AND r.app_entity_source = 0 "
    "AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' "
    "GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) "
    "ORDER BY toStartOfMonth(r.create_time) "
    "FORMAT TabSeparatedWithNames"
)


def _mc():
    """构造本轮需要的 modify_context（与 server._modify_query 传入的结构一致）。"""
    return {
        "base_question": BASE_QUESTION,
        "base_understanding": BASE_UNDERSTANDING,
        "base_sql": BASE_SQL,
        "base_tables": ["ctc_data_warehouse.register"],
        "base_metrics": ["注册客户数"],
    }


def _rules(res):
    """把 review 结果压成 rule 名集合，便于断言"哪条规则被触发"。"""
    return {i["rule"] for i in res.get("issues") or []}


def _has_error(res, rule=None):
    """结果里是否存在 error 级问题（可指定具体规则名）。"""
    for i in res.get("issues") or []:
        if i.get("severity") == "error" and (rule is None or i.get("rule") == rule):
            return True
    return False


# ═══════════════════════════════════════════════════════════
# ① 指标被删：本轮只加了完成率，却把原指标弄丢了
# ═══════════════════════════════════════════════════════════
def test_metric_dropped():
    print("\n【①】上一轮有「注册客户数」，本轮 SQL 只剩「完成率」→ 必须报指标缺失")
    u = {"metrics": ["完成率"], "dimensions": ["月份", "应用主体"]}
    c = RR.build_contract("再加上完成率", u, _mc())
    # 把原指标替换成完成率（模拟模型"加一个"却理解成"换一个"）
    sql = BASE_SQL.replace('count(*) AS "注册客户数" ',
                           'sumIf(r.is_complete = 1) / count(*) AS "完成率" ')
    res = _review(sql, c)
    check_true("① 触发 req_metric_missing（error）", _has_error(res, "req_metric_missing"),
               res["issues"])
    check("① verdict=fail", res["verdict"], "fail")
    # 契约里必须同时包含"本轮新增"与"上轮保留"两个指标
    check_true("① 契约合并了本轮与上轮指标",
               set(c["metrics"]) == {"完成率", "注册客户数"}, c["metrics"])


# ═══════════════════════════════════════════════════════════
# ② 维度被误删：SQL 没了 GROUP BY，结果会塌成一行
# ═══════════════════════════════════════════════════════════
def test_dim_dropped():
    print("\n【②】上一轮按月份+应用主体分组，本轮 SQL 塌成一行 / 维度列消失")
    c = RR.build_contract("加个完成率", {"dimensions": []}, _mc())

    # ②-a 只丢了 GROUP BY，维度列还在 → 只报"缺分组"，不重复报"维度缺失"
    #      （同一个问题报两条只会增加噪音，回灌给模型时也更难读）
    sql_no_group = (BASE_SQL
                    .replace("GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) ", "")
                    .replace("ORDER BY toStartOfMonth(r.create_time) ", ""))
    res_a = _review(sql_no_group, c)
    rules_a = _rules(res_a)
    check_true("②-a 触发 req_group_absent", "req_group_absent" in rules_a, rules_a)
    check_true("②-a 不重复报 req_dim_missing", "req_dim_missing" not in rules_a, rules_a)
    check("②-a verdict=fail", res_a["verdict"], "fail")

    # ②-b 维度列和分组一起消失 → 这才是真正的"维度被删"
    sql_dim_gone = (sql_no_group
                    .replace('r.app_entity_source AS "应用主体", ', ""))
    res_b = _review(sql_dim_gone, c)
    rules_b = _rules(res_b)
    check_true("②-b 触发 req_dim_missing", "req_dim_missing" in rules_b, res_b["issues"])
    check("②-b verdict=fail", res_b["verdict"], "fail")


# ═══════════════════════════════════════════════════════════
# ③ 替换写成追加（最高频真实错误）
# ═══════════════════════════════════════════════════════════
def test_replace_appended():
    print("\n【③】「只看 101」被写成 `=0 OR =101` → 必须识别为替换变追加")
    c = RR.build_contract("应用主体只看 101，其它不变", {}, _mc())
    # 契约里应出现一对替换：(app_entity_source, 0 → 101)
    pairs = c.get("replace_pairs") or []
    check_true("③ 契约识别出替换对 0→101",
               any(p["field"] == "app_entity_source" and p["old"] == "0"
                   and p["new"] == "101" for p in pairs), pairs)
    sql = BASE_SQL.replace(
        "r.app_entity_source = 0 ",
        "(r.app_entity_source = 0 OR r.app_entity_source = 101) ")
    res = _review(sql, c)
    check_true("③ 触发 req_replace_appended（error）",
               _has_error(res, "req_replace_appended"), res["issues"])

    # 对照组：正确替换（只有 101）→ 必须【整体】0 error
    #   ★ 这里必须断言 errors==0，而不只是"没报追加"：
    #     替换成功后 =0 已不在 SQL 里，若契约还把"必须保留 =0"当真，
    #     就会用 req_filter_missing 把正确的 SQL 拦下来（两条规则自相矛盾）。
    ok_sql = BASE_SQL.replace("r.app_entity_source = 0", "r.app_entity_source = 101")
    res2 = _review(ok_sql, c)
    check("③ 正确替换（只有 101）整体 errors=0", res2["errors"], 0)
    check_true("③ 正确替换不报 req_replace_appended",
               not _has_error(res2, "req_replace_appended"), res2["issues"])


# ═══════════════════════════════════════════════════════════
# ④ 过滤丢失：去掉维度分组时顺手把等值过滤也删了
# ═══════════════════════════════════════════════════════════
def test_filter_dropped():
    print("\n【④】「只按月份统计」却把 app_entity_source=0 过滤删掉 → 报过滤丢失，"
          "但不得误报维度丢失")
    c = RR.build_contract("只按月份统计", {"dimensions": ["月份"]}, _mc())
    # 契约：应用主体应被判为"要删掉的维度"，月份应保留
    check_true("④ 应用主体进入 must_remove.dimensions",
               "应用主体" in c["must_remove"]["dimensions"], c["must_remove"])
    check_true("④ 月份仍在 must_keep.dimensions",
               "月份" in c["must_keep"]["dimensions"], c["must_keep"])
    sql = BASE_SQL.replace("AND r.app_entity_source = 0 ", "")
    res = _review(sql, c)
    rules = _rules(res)
    check_true("④ 触发 req_filter_missing（error）",
               _has_error(res, "req_filter_missing"), res["issues"])
    check_true("④ 不误报 req_dim_missing", "req_dim_missing" not in rules, rules)


# ═══════════════════════════════════════════════════════════
# ⑤ 合法修改（对照组）：防"一刀切全拦"
# ═══════════════════════════════════════════════════════════
def test_legal_modify_no_false_block():
    print("\n【⑤】合法修改：去掉应用主体分组但保留其等值过滤 → 必须 0 error")
    c = RR.build_contract("去掉应用主体分组，只按月份统计",
                          {"dimensions": ["月份"]}, _mc())
    check_true("⑤ 应用主体进入 must_remove.dimensions",
               "应用主体" in c["must_remove"]["dimensions"], c["must_remove"])
    sql = (BASE_SQL
           .replace('r.app_entity_source AS "应用主体", ', "")
           .replace("GROUP BY r.app_entity_source, toStartOfMonth(r.create_time)",
                    "GROUP BY toStartOfMonth(r.create_time)"))
    res = _review(sql, c)
    check("⑤ 错误数=0（不误拦）", res["errors"], 0)
    check("⑤ verdict=pass", res["verdict"], "pass")
    # 同时确认这条 SQL 确实保留了过滤（否则本用例就失去意义了）
    check_true("⑤ 用例自身有效：SQL 仍含 app_entity_source=0",
               "app_entity_source = 0" in sql, sql)


# ═══════════════════════════════════════════════════════════
# ⑥ 首轮空契约：没有可判定约束 → 直接通过
# ═══════════════════════════════════════════════════════════
def test_empty_contract_passes():
    print("\n【⑥】首轮查询、理解结果为空 → 契约视为空，不产生任何问题")
    c = RR.build_contract("查个数", {}, None)
    check("⑥ 契约标记 empty=True", c["empty"], True)
    res = _review("SELECT count(*) FROM t", c)
    check("⑥ verdict=pass", res["verdict"], "pass")
    check("⑥ issues 为空", res["issues"], [])


# ═══════════════════════════════════════════════════════════
# ⑦ 注释里出现关键字：不得据此判定（G8 回归）
# ═══════════════════════════════════════════════════════════
def test_comments_not_counted():
    print("\n【⑦】SQL 注释里写着旧值 `app_entity_source = 0`，正文已替换 → 不得报追加")
    c = RR.build_contract("应用主体只看 101，其它不变", {}, _mc())
    ok_body = BASE_SQL.replace("r.app_entity_source = 0", "r.app_entity_source = 101")
    sql = "-- 原来是 app_entity_source = 0，已改为 101\n" + ok_body
    res = _review(sql, c)
    check_true("⑦ 剥注释后不触发 req_replace_appended",
               not _has_error(res, "req_replace_appended"), res["issues"])
    # 反向确认剥注释函数真的起作用了
    check_true("⑦ strip_comments 移除了行注释",
               "原来是" not in RR.strip_comments(sql), RR.strip_comments(sql)[:60])


# ═══════════════════════════════════════════════════════════
# ⑧ 非 SQL 输入：降级而不是乱报
# ═══════════════════════════════════════════════════════════
def test_degraded_on_non_sql():
    print("\n【⑧】输入不是 SELECT 查询 → degraded=True，且不得报 error")
    c = RR.build_contract("注册客户数", {"metrics": ["注册客户数"]}, None)
    res = _review("抱歉，我无法回答这个问题", c)
    check("⑧ degraded=True", res["degraded"], True)
    check("⑧ issues 为空（不乱报）", res["issues"], [])
    check("⑧ verdict 仍为 pass（降级不等于失败）", res["verdict"], "pass")


# ═══════════════════════════════════════════════════════════
# ⑨ 时间窗缺失：warning 级，不阻断
# ═══════════════════════════════════════════════════════════
def test_time_range_missing():
    print("\n【⑨】契约要求 2025-10-01~2026-11-01，SQL 只有上界 → warning（不阻断）")
    c = RR.build_contract("查一下", {"time_range": "2025-10-01 ~ 2026-11-01"}, None)
    sql = ("SELECT count(*) AS \"注册客户数\" FROM t "
           "WHERE create_time < '2026-11-01'")
    res = _review(sql, c)
    check_true("⑨ 触发 req_time_missing", "req_time_missing" in _rules(res), res["issues"])
    check("⑨ verdict=pass（只是 warning）", res["verdict"], "pass")
    check_true("⑨ severity 为 warning",
               all(i["severity"] == "warning" for i in res["issues"]), res["issues"])


# ═══════════════════════════════════════════════════════════
# ⑩ 修改动词识别
# ═══════════════════════════════════════════════════════════
def test_operation_detection():
    print("\n【⑩】operation 识别：替换 / 删除 / 追加")
    cases = [
        ("应用主体只看 101", "replace"),
        ("把注册客户数换成注册司机数", "replace"),
        ("不要按月份了", "remove"),
        ("去掉应用主体分组", "remove"),
        ("再加上完成率", "add"),
    ]
    for q, want in cases:
        got = RR.build_contract(q, {}, _mc())["operation"]
        check(f"⑩ 「{q}」→ {want}", got, want)


# ═══════════════════════════════════════════════════════════
# ⑪ 成本闸门：什么时候该审、什么时候不审
# ═══════════════════════════════════════════════════════════
def test_enabled_gate():
    print("\n【⑪】enabled_for：modify_only 下只审修改类请求；always 下全审")
    import config
    old_mode = getattr(config, "REQ_REVIEW_MODE", "modify_only")
    old_enabled = getattr(config, "REQ_REVIEW_ENABLED", True)
    try:
        config.REQ_REVIEW_ENABLED = True
        config.REQ_REVIEW_MODE = "modify_only"
        check("⑪ 增量修改 → 要审", RR.enabled_for("再加完成率", _mc()), True)
        check("⑪ 首轮普通查询 → 不审", RR.enabled_for("昨天注册了多少客户", None), False)
        config.REQ_REVIEW_MODE = "always"
        check("⑪ always 模式 → 首轮也审", RR.enabled_for("昨天注册了多少客户", None), True)
        config.REQ_REVIEW_MODE = "off"
        check("⑪ off → 一律不审", RR.enabled_for("再加完成率", _mc()), False)
        config.REQ_REVIEW_MODE = "modify_only"
        config.REQ_REVIEW_ENABLED = False
        check("⑪ 总开关关闭 → 不审", RR.enabled_for("再加完成率", _mc()), False)
    finally:
        config.REQ_REVIEW_MODE = old_mode
        config.REQ_REVIEW_ENABLED = old_enabled


# ═══════════════════════════════════════════════════════════
# ⑫ 替换"未落实"：旧值没了、新值也没出现（模型直接把过滤条件删了）
#   ★ 为什么要单独测？这是 P2 评测（eval_modify_live.py）暴露的规则空洞：
#     R3 只管 must_keep 里的旧过滤（替换后已被剔除）、
#     R4 只管"新旧同时存在"；
#     于是"把 WHERE 里这一条删掉"既不算丢过滤、也不算追加，静默通过 ——
#     用户说"只看 101"，拿到的却是全量数据，数字变大还没人发现。
# ═══════════════════════════════════════════════════════════
def test_replace_not_applied():
    print("\n【⑫】说「只看 101」，SQL 却把 app_entity_source 过滤整条删了 → 必须报替换未落实")
    c = RR.build_contract("应用主体只看 101，其它不变", BASE_UNDERSTANDING, _mc())
    check_true("⑫ 契约里生成了替换对 (0 → 101)",
               any(p["field"] == "app_entity_source" and str(p["new"]) == "101"
                   for p in c["replace_pairs"]), c["replace_pairs"])

    sql_deleted = BASE_SQL.replace(" AND r.app_entity_source = 0", "")
    res = _review(sql_deleted, c)
    check_true("⑫ 触发 req_replace_not_applied（error）",
               _has_error(res, "req_replace_not_applied"), res["issues"])
    check("⑫ verdict=fail", res["verdict"], "fail")

    # 对照组：正确写成 = 101 → 不能误报（这是"防误拦"的关键）
    sql_ok = BASE_SQL.replace("r.app_entity_source = 0", "r.app_entity_source = 101")
    res_ok = _review(sql_ok, c)
    check("⑫ 正确替换 → 无 error", res_ok["errors"], 0)


# ═══════════════════════════════════════════════════════════
# ⑬ 指标替换不能自我矛盾：说"改成注册司机数"，就不能再要求"注册客户数还在"
#   ★ 这也是 P2 评测暴露的（与 P1 修过的过滤 replace_pairs 完全同源）：
#     must_keep.metrics 无条件继承上一轮指标 → 正确 SQL（只有司机数）
#     会被 R1 判成"删了原指标" → 模型无论怎么改都通不过。
# ═══════════════════════════════════════════════════════════
def test_metric_replace_no_self_conflict():
    print("\n【⑬】把注册客户数改成注册司机数 → 契约不得再要求保留注册客户数")
    # ★ filters 必须一起给：司机口径天然把 user_type 从 1 换成 2，
    #   若本轮 understanding 不写这条，R3 会理直气壮地判"丢了 user_type = 1" ——
    #   那是测试夹具不忠实，不是审核器错（真实流水线里这一步由 query_understanding 给出）。
    u = {"metrics": ["注册司机数"], "dimensions": ["月份", "应用主体"],
         "filters": ["user_type = 2", "app_entity_source = 0"]}
    c = RR.build_contract("把注册客户数改成注册司机数", u, _mc())
    check("⑬ must_keep.metrics 已剔除被替换的旧指标", c["must_keep"]["metrics"], [])
    check("⑬ 契约指标只剩本轮要求", c["metrics"], ["注册司机数"])

    sql_driver = (BASE_SQL
                  .replace("r.user_type = 1", "r.user_type = 2")
                  .replace('count(*) AS "注册客户数"', 'count(*) AS "注册司机数"'))
    res = _review(sql_driver, c)
    check("⑬ 正确 SQL → 无 error（不再自相矛盾）", res["errors"], 0)

    # 反向：SQL 里压根没有司机数 → 仍要报指标缺失（规则不能被"放开"成摆设）
    res_missing = _review(BASE_SQL, c)
    check_true("⑬ 仍是客户口径 → 报 req_metric_missing",
               _has_error(res_missing, "req_metric_missing"), res_missing["issues"])


# ═══════════════════════════════════════════════════════════
# ⑭ 无字段名、无数字的口径切换：只能靠本轮 understanding 反推替换对
#   ★ "不要看代驾了，只看大板" 里既没有字段名也没有 201 —— "大板"是业务黑话。
#     只按问句抠数字必然漏判，进而把正确 SQL（只有 =201）判成"丢了原过滤"（误拦）。
# ═══════════════════════════════════════════════════════════
def test_switch_by_understanding():
    print("\n【⑭】「不要看代驾了，只看大板」→ 用 understanding 反推 102 → 201")

    def _mc_daijia():
        """上一轮看的是代驾（102），本轮要换到大板（201）。"""
        mc = _mc()
        mc["base_sql"] = BASE_SQL.replace("app_entity_source = 0", "app_entity_source = 102")
        mc["base_understanding"] = dict(
            BASE_UNDERSTANDING, filters=["user_type = 1", "app_entity_source = 102"])
        return mc

    u = {"metrics": ["注册客户数"], "dimensions": ["月份", "应用主体"],
         "filters": ["user_type = 1", "app_entity_source = 201"]}
    c = RR.build_contract("不要看代驾了，只看大板", u, _mc_daijia())
    check_true("⑭ 反推出替换对 (102 → 201)",
               any(p["field"] == "app_entity_source" and str(p["old"]) == "102"
                   and str(p["new"]) == "201" for p in c["replace_pairs"]),
               c["replace_pairs"])

    sql_ok = BASE_SQL.replace("app_entity_source = 0", "app_entity_source = 201")
    res = _review(sql_ok, c)
    check("⑭ 正确切换 → 无 error（原实现会误拦）", res["errors"], 0)

    # 经典错误：用 OR 追加 → 新旧口径同时存在
    sql_append = BASE_SQL.replace("app_entity_source = 0",
                                  "(app_entity_source = 102 OR app_entity_source = 201)")
    res_append = _review(sql_append, c)
    check_true("⑭ 写成 OR 追加 → 报 req_replace_appended",
               _has_error(res_append, "req_replace_appended"), res_append["issues"])


def main():
    """依次执行全部用例，汇总结果并给出退出码（供 CI/脚本使用）。"""
    for fn in (test_metric_dropped, test_dim_dropped, test_replace_appended,
               test_filter_dropped, test_legal_modify_no_false_block,
               test_empty_contract_passes, test_comments_not_counted,
               test_degraded_on_non_sql, test_time_range_missing,
               test_operation_detection, test_enabled_gate,
               test_replace_not_applied, test_metric_replace_no_self_conflict,
               test_switch_by_understanding):
        try:
            fn()
        except Exception as e:
            import traceback
            (FAILED).append(f"{fn.__name__} 异常：{e}")
            print(f"  ❌ {fn.__name__} 抛异常：{e}")
            traceback.print_exc()

    print("\n" + "=" * 60)
    if FAILED:
        print(f"❌ 失败 {len(FAILED)} 项（通过 {len(PASSED)} 项）")
        for f in FAILED:
            print("  ❌", f)
        return 1
    print(f"🎉 全部通过（{len(PASSED)} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
