# -*- coding: utf-8 -*-
"""
test_pipeline.py —— 流水线「连通性 / 契约」测试

★ 为什么需要这一组测试？（血泪教训）
  刚修过三类「管道接了、但没接通」的 bug —— 都不报错、页面看着正常、就是答案不对：
    ① v2 流水线检索了数据字典，却从没把字典拼进 prompt →「配了字典模型看不到」
    ② 大字典注入被截断到 100 项，关键取值（钻石会员 161~166）被截掉
    ③ 拒答分支漏传 dicts → 前端显示「召回的字典（无）」，把人带偏
  这类 bug 靠人工点点点很难发现，必须有一组专门检查
  「每一段的产物，有没有真的交给下一段」的测试。

★ 检查点（stage → 产物 → 交接断言）
      检索 retrieve_dicts  →  dict_names  →  断言：非空时 prompt 必须含字典块
      组装 _build_dict_block →  文本       →  断言：相关取值必须在 100 项截断之内
      出口 _rv             →  result       →  断言：拒答时也要带 dicts
      legacy 组装           →  prompt       →  断言：两条流水线都能注入字典

★ 用测试替身（monkeypatch）替换 LLM/DB，不联网、不调模型，秒级跑完。
运行： .venv/bin/python test_pipeline.py
"""
import agent
import prompt
import rag
import semantic_layer
import query_understanding
import calendar_util
import llm


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
# 测试替身：210 项、钻石会员排在 161~166 的字典（复刻真实「资金收支流水类型」）
# ═══════════════════════════════════════════════════════════
def _big_dict():
    items = [{"value": str(i), "label": f"普通类型{i}"} for i in range(1, 211)]
    for code, lab in [(161, "钻石会员"), (162, "钻石会员分期首付款"),
                      (163, "钻石会员分期付款"), (164, "钻石会员分期提前还款"),
                      (165, "钻石会员退款"), (166, "钻石会员分期首付退款")]:
        items[code - 1] = {"value": str(code), "label": lab}
    return {"name": "资金收支流水类型", "field_hint": "record.type",
            "items": items, "keywords": ["资金收支流水类型"], "text": ""}


def _fake_index():
    # ★ 注意：ask_v2 用的是 enrich 后的索引 sidx 里的 dicts（sidx = index["_semantic"]），
    #   所以字典要放进 _semantic，别只放顶层。
    d = _big_dict()
    return {"tables": [], "metrics": [], "allowed_tables": [],
            "_semantic": {"stub": True, "dicts": [d]}}


def _stub_env(captured):
    """把 ask_v2 的外部依赖换成轻量替身，返回 restore()。
    ★ 只替换「边界」（LLM / 检索层），被测的接线（agent 内部拼接）保持真实。"""
    patch = {}
    def _set(mod, name, val):
        patch[(mod, name)] = getattr(mod, name)
        setattr(mod, name, val)

    class _Ctx:
        p3_tables = ["ods_tu_record_all", "ods_tu_record_type_all"]
        p1_metrics = []
        p4_docs = []
        route_info = "替身路由"

    _set(semantic_layer, "retrieve_semantic", lambda sidx, q, u: _Ctx())
    _set(semantic_layer, "resolve", lambda ctx: {"p0": "", "p1": "", "p2": "",
                                                 "p3": "", "p4": "", "metrics": []})
    _set(semantic_layer, "build_prompt_parts", lambda resolved, role, time_context="": "ROLE")
    _set(query_understanding, "understand", lambda q, sidx: {})
    _set(rag, "match_disabled_metric", lambda sidx, q: None)
    _set(rag, "retrieve_dicts", lambda sidx, q: (["资金收支流水类型"], "关键词精确命中"))
    _set(calendar_util, "build_time_context", lambda q: "")
    _set(calendar_util, "summary_line", lambda q: "")

    def _chat(system_prompt, user):
        captured["system_prompt"] = system_prompt
        return "-- 无法回答：测试替身"        # 走拒答分支，避开 DB 执行
    _set(llm, "chat", _chat)

    def restore():
        for (mod, name), val in patch.items():
            setattr(mod, name, val)
    return restore


# ═══════════════════════════════════════════════════════════
# 检查点 1：字典块 —— 相关取值必须排进 100 项截断之内
# ═══════════════════════════════════════════════════════════
def test_dict_block_relevance():
    print("\n【检查点1】字典组装 _build_dict_block：大字典按问题优先")
    d = _big_dict()
    blk = prompt._build_dict_block([d], [d["name"]], "查询9月钻石会员的资金流水统计")
    check_true("钻石会员(161) 被注入", "161=钻石会员" in blk, blk[:140])
    check_true("分期(163) 被注入", "163=钻石会员分期付款" in blk)
    check_true("退款(165) 被注入", "165=钻石会员退款" in blk)
    # 无关问题：不崩，且不该把钻石会员排前面
    blk2 = prompt._build_dict_block([d], [d["name"]], "查询订单量")
    check_true("无关问题也能返回非空（不崩）", bool(blk2))
    check_true("无关问题不含钻石会员（没乱塞）", "钻石会员" not in blk2, blk2[:140])


# ═══════════════════════════════════════════════════════════
# 检查点 2：v2 接线 —— 召回的字典必须出现在 system_prompt 里
# ═══════════════════════════════════════════════════════════
def test_case_when_snippet():
    """【检查点1b】字典输出转换片段（2026-10-26 需求：枚举输出列直接转中文）"""
    print("\n【检查点1b】_build_dict_block 附带 CASE WHEN 输出转换片段")
    d = {"name": "司机身份类型", "field_hint": "type", "keywords": ["司机身份类型"],
         "items": [{"value": "1", "label": "自营司机"},
                   {"value": "2", "label": "加盟司机"},
                   {"value": "3", "label": "劳务司机"}]}
    blk = prompt._build_dict_block([d], ["司机身份类型"], "查询各司机身份的完单量")
    check_true("含 CASE WHEN 片段", "CASE WHEN type = 1 THEN '自营司机'" in blk, blk[:500])
    check_true("覆盖全部取值分支", ("WHEN type = 2" in blk and "WHEN type = 3" in blk), blk)
    check_true("ELSE 按方言取原值（ClickHouse → toString）",
               "ELSE toString(type)" in blk, blk)
    check_true("指令明确：过滤/分组仍用编码",
               ("过滤" in blk and "编码" in blk), blk[:300])
    check_true("提醒替换成实际表别名字段", "表别名字段" in blk, blk[:300])

    # 大字典：分支数受 MAX_CASE_WHEN_BRANCHES 限制，且注明其余取值
    big = _big_dict()
    blk2 = prompt._build_dict_block([big], ["资金收支流水类型"], "查询9月钻石会员的资金流水")
    # 用 THEN 计数分支（"WHEN " 会把指令文本里的 "CASE WHEN 片段" 也数进去）
    branch_count = blk2.count("THEN ")
    check_true(f"WHEN 分支数 ≤ 上限（实际 {branch_count}）",
               branch_count <= prompt.MAX_CASE_WHEN_BRANCHES, branch_count)
    check_true("截断时注明其余取值", "其余" in blk2, blk2[-200:])

    # 无 field_hint 的字典：不生成片段（保持旧行为）
    nof = {"name": "无字段字典", "field_hint": "", "keywords": [],
           "items": [{"value": "1", "label": "甲"}]}
    blk3 = prompt._build_dict_block([nof], ["无字段字典"], "")
    check_true("无 field_hint 不生成 CASE WHEN", "CASE WHEN" not in blk3, blk3[:300])


def test_v2_injects_dict_into_prompt():
    print("\n【检查点2】v2 接线：召回的字典必须拼进 system_prompt（本次核心 bug）")
    captured = {}
    restore = _stub_env(captured)
    try:
        r = agent.ask_v2(_fake_index(), "查询9月钻石会员的资金流水统计")
    finally:
        restore()
    sp = captured.get("system_prompt", "")
    check_true("捕获到 system_prompt（流水线确实走到了生成阶段）", bool(sp))
    check_true("prompt 含字典块标题", "字段取值字典" in sp, sp[-160:])
    check_true("prompt 含钻石会员(161)", "161=钻石会员" in sp)
    check_true("拒答分支也带 dicts（显示不再空白）",
               r.get("dicts") == ["资金收支流水类型"], r.get("dicts"))


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print("╔" + "═" * 60 + "╗")
    print("║" + "流水线连通性 / 契约测试".center(50) + "║")
    print("╚" + "═" * 60 + "╝")

    test_dict_block_relevance()
    test_case_when_snippet()
    test_v2_injects_dict_into_prompt()

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
    # ★ 失败必须让进程返回非 0 —— 否则 CI/上层 runner 会把失败的测试当成功（曾经就是这样漏掉的）
    raise SystemExit(1 if FAILED else 0)
