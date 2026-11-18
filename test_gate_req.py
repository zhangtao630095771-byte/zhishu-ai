# -*- coding: utf-8 -*-
"""
test_gate_req.py —— 需求一致性审核的【接线】集成测试（P1）

★ 与 test_requirement_review.py 的分工：
    - 那个测「规则本身判得对不对」（纯函数，不碰 agent）；
    - 这个测「规则接进 ask_v2 之后，闸门行为对不对」（用替身替代模型/数据库/权限）。

★ 测试的 8 条不变量（对应 docs 的 5.2 第 11~18 条）：
    ⑪ 审核 fail → 回灌模型 → 修好 → 执行（exec=1，llm≥2）
    ⑫ 审核 fail 且修复用尽 → 拒绝执行（exec=0），且说清原因
    ⑬ 审核器判不了（degraded）→ 记 skipped 且【不阻断】（skipped ≠ pass）
    ⑭ 审核 fail 但 SQL 越权 → 走安全分支先拒，【不进入需求协商】
    ⑮ 失败结果不得带数据行（沿用全项目不变量）
    ⑯ BLOCK=1 时：ok=True ⇒ requirement_verdict ∈ {pass, skipped}
    ⑰ 修好之后必须【重新审核】（不是"修完就放行"）
    ⑱ 开关全关 → 行为与改造前完全一致

  运行： .venv/bin/python test_gate_req.py
"""
import os
import sys
import tempfile

# ★ 用临时配置库：避免 agent → config_store 的初始化碰到真实库（必须在 import config_store 前设置）
_TMP_DB = os.path.join(tempfile.gettempdir(), "zhishu_test_gate_req.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
os.environ["CONFIG_DB"] = _TMP_DB
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config                  # noqa: E402
import agent                   # noqa: E402
import db                      # noqa: E402
import guard                   # noqa: E402
import llm                     # noqa: E402
import prompt                  # noqa: E402  （保留：agent 内部会用到）
import rag                     # noqa: E402
import semantic_layer          # noqa: E402
import sql_validator           # noqa: E402
import query_understanding     # noqa: E402
import calendar_util           # noqa: E402
import requirement_review as RR   # noqa: E402
import sql_experience          # noqa: E402  （㉕㉖：经验摘要 / 失败回流）
import config_store            # noqa: E402  （㉖：临时库建表）

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
    if not ok and detail != "":
        print(f"       实际: {detail}")
    return ok


# ═══════════════════════════════════════════════════════════
# 基准场景：上一轮"按应用主体+月份统计注册客户数"，本轮"只看 101"
# ═══════════════════════════════════════════════════════════
BASE_SQL = (
    'SELECT o.app_entity_source AS "应用主体", '
    'toStartOfMonth(o.create_time) AS "月份", '
    'count(*) AS "注册客户数" '
    'FROM ods_valet_order_all o '
    'WHERE o.app_entity_source = 0 '
    'GROUP BY o.app_entity_source, toStartOfMonth(o.create_time)'
)
# 错误写法：替换被写成追加（本轮审核必须抓到的核心场景）
BAD_SQL = BASE_SQL.replace("o.app_entity_source = 0",
                           "(o.app_entity_source = 0 OR o.app_entity_source = 101)")
# 正确写法：真正的替换
GOOD_SQL = BASE_SQL.replace("o.app_entity_source = 0", "o.app_entity_source = 101")

BASE_U = {
    "intent": "summary",
    "metrics": ["注册客户数"],
    "entities": ["注册客户数"],
    "dimensions": ["月份"],
    "filters": ["app_entity_source = 0"],
    "time_range": "",
}
MODIFY_CTX = {
    "base_question": "注册客户数，按月份和应用主体分组",
    "base_understanding": BASE_U,
    "base_sql": BASE_SQL,
    "base_tables": ["ods_valet_order_all"],
    "base_metrics": ["注册客户数"],
}
# 本轮查询理解（用户只说"只看 101"，指标/维度靠上轮补全）
CUR_U = {"intent": "summary", "metrics": [], "dimensions": [], "filters": [], "time_range": ""}


class _Ctx:
    """semantic_layer.retrieve_semantic 的替身返回。"""
    p3_tables = ["ods_valet_order_all"]
    p1_metrics = []
    p2_relationships = []
    p3_table_objs = []
    p4_docs = []
    route_info = "替身路由"


def _fake_index():
    return {"_semantic": {"stub": True, "dicts": [], "allowed_tables": ["ods_valet_order_all"],
                          "tables": []},
            "allowed_tables": ["ods_valet_order_all"]}


def _install(patch, understanding=None):
    """把 ask_v2 的外部依赖换成替身；返回 _set 用于逐项覆盖。

    ★ 每个被替换的属性都记下原值，测试结束必须还原 ——
      否则测试之间会互相污染（这是 test_gate 已验证过的写法）。
    """
    def _set(mod, name, val):
        key = (mod, name)
        if key not in patch:
            patch[key] = getattr(mod, name)
        setattr(mod, name, val)

    _set(semantic_layer, "retrieve_semantic", lambda s, q, u: _Ctx())
    _set(semantic_layer, "resolve", lambda c: {"p0": "", "p1": "", "p2": "", "p3": "",
                                               "p4": "", "metrics": [], "bindings": {}})
    _set(semantic_layer, "build_prompt_parts", lambda r, role, time_context="": "ROLE")
    _set(query_understanding, "understand", lambda q, s: dict(understanding or CUR_U))
    _set(rag, "match_disabled_metric", lambda s, q: None)
    _set(rag, "retrieve_dicts", lambda s, q: ([], "无字典"))
    _set(calendar_util, "build_time_context", lambda q: "")
    _set(calendar_util, "summary_line", lambda q: "")
    _set(guard, "sql_guard", lambda sql: (True, ""))
    _set(guard, "check_tables_ex",
         lambda sql, allowed, strict=None: {"ok": True, "illegal": [], "status": "parsed",
                                            "method": "stub", "reason": "", "message": ""})
    _set(sql_validator, "validate_sql_ex",
         lambda sql, ctx, resolved, allowed: {"issues": [], "status": "passed",
                                              "degraded": [], "error": ""})
    # ★ 关掉"事后逻辑自审"：它会额外调一次 llm.chat，导致下面统计的调用次数不准
    _set(config, "SQL_REVIEW_ENABLED", False)
    # ★ 语义层默认替身：返回 pass（真实模型不参与单测）。
    #   要看 fail/unsure/超时，用 _run(semantic=...) 覆盖 —— 见 ㉑㉒㉓。
    _set(RR, "review_semantic", _sem_pass)
    return _set


def _sem_pass(sql, contract, **kw):
    """语义层替身：判通过。"""
    return {"verdict": "pass", "issues": [], "assumptions": [],
            "degraded": False, "source": "stub"}


# ★ 记下真实的语义层实现：㉓ 要测"调用失败怎么降级"，必须用真身而不是替身
_ORIG_REVIEW_SEMANTIC = RR.review_semantic


def _restore(patch):
    for (mod, name), val in patch.items():
        setattr(mod, name, val)


def _run(scripts, block=True, enabled=True, modify_ctx=None, understanding=None,
         check_tables=None, semantic=None):
    """跑一次 ask_v2，返回 (llm 调用次数, 执行次数, 结果, 实际执行的 SQL 列表)。

    scripts : 模型按顺序返回的 SQL 列表（用完后重复最后一条）。
    semantic: 语义层替身；传 dict 作为固定裁决，传函数则按调用自定义。
    """
    patch = {}
    _set = _install(patch, understanding)
    calls = {"llm": 0, "exec": 0}
    executed_sql = []

    def fake_llm(sp, user):
        i = min(calls["llm"], len(scripts) - 1)
        calls["llm"] += 1
        return scripts[i]

    def fake_exec(sql):
        calls["exec"] += 1
        executed_sql.append(sql)
        return (["c"], [[1]])

    _set(llm, "chat", fake_llm)
    _set(db, "execute_sql", fake_exec)
    if check_tables is not None:
        _set(guard, "check_tables_ex", check_tables)
    if semantic is not None:
        if callable(semantic):
            _set(RR, "review_semantic", semantic)
        else:
            _set(RR, "review_semantic",
                 lambda sql, contract, **kw: dict(semantic))
    # 需求审核开关（每个用例单独设置，结束还原）
    _set(config, "REQ_REVIEW_ENABLED", enabled)
    _set(config, "REQ_REVIEW_BLOCK", block)
    _set(config, "REQ_REVIEW_MODE", "modify_only")
    _set(config, "REQ_FAIL_RECORD", False)   # 单测不写经验库（避免污染，见 ㉖ 单独测）
    try:
        r = agent.ask_v2(_fake_index(), "应用主体只看 101",
                         modify_context=modify_ctx if modify_ctx is not None else MODIFY_CTX)
    finally:
        _restore(patch)
    return calls["llm"], calls["exec"], r, executed_sql


# ═══════════════════════════════════════════════════════════
# ⑪ 审核 fail → 回灌 → 修好 → 执行
# ═══════════════════════════════════════════════════════════
def test_repair_then_execute():
    print("\n【⑪】首版把替换写成追加 → 审核 fail → 回灌修复 → 重审通过 → 执行")
    n_llm, n_exec, r, sqls = _run([BAD_SQL, GOOD_SQL], block=True)
    check("⑪ 模型被调用了至少 2 次（生成 + 修复）", n_llm >= 2, True)
    check("⑪ 最终执行了 1 次", n_exec, 1)
    check("⑪ ok=True", r.get("ok"), True)
    chain = r.get("status_chain") or {}
    check("⑪ requirement_verdict=pass", chain.get("requirement_verdict"), "pass")
    check("⑪ 修复计数=1", chain.get("requirement_attempts"), 1)
    check_true("⑪ 实际执行的是【修好后】的 SQL（含 101、不含 OR）",
               "101" in (sqls[-1] if sqls else "") and " OR " not in (sqls[-1] or ""),
               sqls[-1] if sqls else "")


# ═══════════════════════════════════════════════════════════
# ⑫ 修复用尽 → 拒绝执行
# ═══════════════════════════════════════════════════════════
def test_blocked_after_retries():
    print("\n【⑫】一直写成追加、修复 1 次仍 fail → 拒绝执行，且原因说清是【需求】不是安全")
    n_llm, n_exec, r, _ = _run([BAD_SQL], block=True)
    check("⑫ 未执行数据库", n_exec, 0)
    check("⑫ ok=False", r.get("ok"), False)
    chain = r.get("status_chain") or {}
    check("⑫ requirement_verdict=fail", chain.get("requirement_verdict"), "fail")
    check("⑫ gate_passed=True（安全校验其实是通过的）", chain.get("gate_passed"), True)
    check("⑫ executed=False", chain.get("executed"), False)
    check_true("⑫ 错误信息指明是需求未满足", "需求" in (r.get("error") or ""), r.get("error"))
    # 修复轮次：初始 1 次 + 1 次修复 = 2（MAX_AUTO_FIX=1，任务书要求最多一次定点修复）
    check("⑫ 模型调用 2 次（含 1 次修复）", n_llm, 2)


# ═══════════════════════════════════════════════════════════
# ⑬ 审核器判不了 → skipped，不阻断（skipped ≠ pass）
# ═══════════════════════════════════════════════════════════
def test_degraded_skipped_not_pass():
    print("\n【⑬】审核器无法判定（degraded）→ 记 skipped 且照常执行（不得伪装成 pass）")
    orig_analyze = RR.analyze
    RR.analyze = lambda sql: {"degraded": True}      # 模拟"这次判不了"
    try:
        n_llm, n_exec, r, _ = _run([BAD_SQL], block=True)
    finally:
        RR.analyze = orig_analyze
    check("⑬ 仍然执行（判不了不等于 SQL 有问题）", n_exec, 1)
    chain = r.get("status_chain") or {}
    check("⑬ requirement_verdict=skipped（不是 pass）",
          chain.get("requirement_verdict"), "skipped")
    check("⑬ ok=True", r.get("ok"), True)


# ═══════════════════════════════════════════════════════════
# ⑭ 越权 SQL：安全分支先拒，不进需求协商
# ═══════════════════════════════════════════════════════════
def test_security_precedes_requirement():
    print("\n【⑭】SQL 引用未授权表 → 必须被安全闸门先拒，不得进入需求审核/修复流程")
    bad_table = "SELECT count(*) AS \"注册客户数\" FROM secret_table"
    deny = lambda sql, allowed, strict=None: (       # noqa: E731
        {"ok": False, "illegal": ["secret_table"], "status": "parsed", "method": "stub",
         "reason": "", "message": "SQL 引用了未授权的表：['secret_table']"}
        if "secret_table" in sql else
        {"ok": True, "illegal": [], "status": "parsed", "method": "stub",
         "reason": "", "message": ""})
    n_llm, n_exec, r, _ = _run([bad_table], block=True, check_tables=deny)
    check("⑭ 未执行", n_exec, 0)
    check("⑭ 只调了 1 次模型（没有进入需求修复）", n_llm, 1)
    check("⑭ 没有需求审核结果（req_review=None）", r.get("req_review"), None)
    chain = r.get("status_chain") or {}
    check("⑭ gate_passed=False", chain.get("gate_passed"), False)
    check("⑭ requirement_verdict=skipped（根本没跑）",
          chain.get("requirement_verdict"), "skipped")


# ═══════════════════════════════════════════════════════════
# ⑮ 失败结果不带数据行
# ═══════════════════════════════════════════════════════════
def test_failure_has_no_rows():
    print("\n【⑮】需求审核拒绝时，结果里不得夹带数据行（不能「说失败、下面却有数字」）")
    _, n_exec, r, _ = _run([BAD_SQL], block=True)
    check("⑮ 未执行", n_exec, 0)
    check("⑮ rows 为空", list(r.get("rows") or []), [])
    check("⑮ columns 为空", list(r.get("columns") or []), [])


# ═══════════════════════════════════════════════════════════
# ⑯ 状态不变量：ok=True ⇒ 需求审核通过或未启用
# ═══════════════════════════════════════════════════════════
def test_status_invariant():
    print("\n【⑯】BLOCK=1 时：ok=True 必须意味着 requirement_verdict ∈ {pass, skipped}")
    scenarios = [
        ("正确 SQL", [GOOD_SQL]),
        ("修复后正确", [BAD_SQL, GOOD_SQL]),
        # 一直错 → 拒绝执行，本不该进入下面的"ok=True"断言，用 ok=False 单独校验
        ("一直错", [BAD_SQL]),
    ]
    for name, scripts in scenarios:
        _, _, r, _ = _run(scripts, block=True)
        chain = r.get("status_chain") or {}
        if r.get("ok"):
            check_true(f"⑯ {name}：ok=True 时 verdict={chain.get('requirement_verdict')}",
                       chain.get("requirement_verdict") in ("pass", "skipped"),
                       chain.get("requirement_verdict"))
        else:
            check_true(f"⑯ {name}：ok=False 时 verdict 必为 fail",
                       chain.get("requirement_verdict") == "fail",
                       chain.get("requirement_verdict"))


# ═══════════════════════════════════════════════════════════
# ⑰ 修好后必须重新审核（不是"修完就放行"）
# ═══════════════════════════════════════════════════════════
def test_revalidate_after_repair():
    print("\n【⑰】修复后的 SQL 必须重新过需求审核（审核次数 = 修复次数 + 1）")
    orig = RR.review
    calls = {"n": 0}

    def counting(sql, contract, **kw):
        calls["n"] += 1
        return orig(sql, contract, **kw)

    RR.review = counting
    try:
        n_llm, n_exec, r, _ = _run([BAD_SQL, GOOD_SQL], block=True)
    finally:
        RR.review = orig
    check("⑰ 审核被调用 2 次（首版 1 次 + 修复后 1 次）", calls["n"], 2)
    check("⑰ 最终执行", n_exec, 1)


# ═══════════════════════════════════════════════════════════
# ⑱ 总开关关闭 → 行为与改造前一致
# ═══════════════════════════════════════════════════════════
def test_disabled_is_noop():
    print("\n【⑱】REQ_REVIEW_ENABLED=0 → 不建契约、不审核，行为回到改造前")
    _, n_exec, r, _ = _run([BAD_SQL], block=False, enabled=False)
    check("⑱ 照常执行（不因需求问题拦截）", n_exec, 1)
    check("⑱ ok=True", r.get("ok"), True)
    check("⑱ req_review=None", r.get("req_review"), None)
    chain = r.get("status_chain") or {}
    check("⑱ requirement_verdict=skipped",
          chain.get("requirement_verdict"), "skipped")
    check("⑱ generated=True / executed=True",
          (chain.get("generated"), chain.get("executed")), (True, True))


# ═══════════════════════════════════════════════════════════
# ⑲（附加）灰度模式：只告警不阻断，状态如实记录
# ═══════════════════════════════════════════════════════════
def test_advisory_mode_records_honestly():
    print("\n【⑲】BLOCK=0（灰度）→ 有问题也照常执行，但状态必须【如实】记 fail")
    n_llm, n_exec, r, _ = _run([BAD_SQL], block=False)
    chain = r.get("status_chain") or {}
    # ★ 这里不能断言 llm 调用次数：执行成功后会再调一次模型生成自然语言结论。
    #   "有没有触发需求修复"要看 requirement_attempts，而不是模型调用数。
    check("⑲ 未触发需求修复（修复计数=0）", chain.get("requirement_attempts"), 0)
    check("⑲ 照常执行", n_exec, 1)
    check("⑲ verdict=fail（没有粉饰成 pass）",
          chain.get("requirement_verdict"), "fail")
    check("⑲ 同时 executed=True（两个状态各自独立记录）",
          chain.get("executed"), True)
    check_true("⑲ req_review 里能看到具体问题",
               bool((r.get("req_review") or {}).get("issues")),
               (r.get("req_review") or {}).get("issues"))


# ═══════════════════════════════════════════════════════════
# ⑳（附加）静态不变量：守住"只审核、不改写、不越权"的边界
# ═══════════════════════════════════════════════════════════
def test_static_invariants():
    print("\n【⑳】静态检查：审核模块不得碰执行入口；需求审核必须排在安全闸门之后")
    root = os.path.dirname(os.path.abspath(__file__))
    rr_src = open(os.path.join(root, "requirement_review.py"), encoding="utf-8").read()
    ag_src = open(os.path.join(root, "agent.py"), encoding="utf-8").read()

    check_true("⑳ requirement_review 不依赖 db 模块",
               "\nimport db" not in rr_src and "\nimport db " not in rr_src, "命中 import db")
    check_true("⑳ requirement_review 不含任何执行调用",
               "execute_sql(" not in rr_src, "命中 execute_sql(")
    check_true("⑳ requirement_review 不含写操作关键字",
               not any(k in rr_src.upper() for k in ("DELETE FROM", "UPDATE ", "INSERT INTO")),
               "命中写操作字符串")

    # ★ 顺序不变量：安全闸门必须先于需求审核被"写在代码里"
    pos_gate = ag_src.find("chk = _pre_execute_check(")
    pos_req = ag_src.find("requirement_review.review(")
    check_true("⑳ agent 里同时存在闸门与需求审核调用",
               pos_gate > 0 and pos_req > 0, (pos_gate, pos_req))
    check_true("⑳ 安全闸门在需求审核之前", pos_gate < pos_req, (pos_gate, pos_req))


# ═══════════════════════════════════════════════════════════
# ㉑ 语义层判 fail → 回灌 → 修好 → 执行
# ═══════════════════════════════════════════════════════════
_SEM_FAIL = {
    "verdict": "fail",
    "issues": [{"severity": "error", "tier": "REQ-LLM", "rule": "intent_misread",
                "message": "把「完成率」理解成了「完成单量」，口径不同",
                "suggestion": "按完成率的分子/分母口径计算", "evidence": "SELECT …"}],
    "assumptions": [], "degraded": False, "source": "stub",
}


def test_semantic_fail_then_repair():
    print("\n【㉑】确定性层没发现问题，语义层判 fail（答非所问）→ 回灌修复 → 通过")
    state = {"n": 0}

    def sem(sql, contract, **kw):
        state["n"] += 1
        # 第一次判 fail，修复后判 pass（模拟"改对了就过"）
        if state["n"] == 1:
            return dict(_SEM_FAIL)
        return {"verdict": "pass", "issues": [], "assumptions": [],
                "degraded": False, "source": "stub"}

    n_llm, n_exec, r, _ = _run([GOOD_SQL], block=True, semantic=sem)
    chain = r.get("status_chain") or {}
    check("㉑ 触发了修复（模型调用 ≥2）", n_llm >= 2, True)
    check("㉑ 最终执行 1 次", n_exec, 1)
    check("㉑ requirement_verdict=pass", chain.get("requirement_verdict"), "pass")
    check("㉑ 修复计数=1", chain.get("requirement_attempts"), 1)
    check("㉑ 语义层被调用 2 次（首版 + 修复后）", state["n"], 2)


# ═══════════════════════════════════════════════════════════
# ㉒ 语义层判 unsure → 不修 SQL，直接问用户
# ═══════════════════════════════════════════════════════════
def test_semantic_unsure_asks_user():
    print("\n【㉒】语义层判 unsure（需求有歧义）→ 不做无意义的修复，改为向用户确认")
    sem = {"verdict": "unsure", "issues": [{"severity": "warning", "tier": "REQ-LLM",
                                            "rule": "intent_misread",
                                            "message": "无法确定是替换还是追加",
                                            "suggestion": "", "evidence": ""}],
           "assumptions": ["只统计 101，不再包含 0"], "degraded": False, "source": "stub"}
    n_llm, n_exec, r, _ = _run([GOOD_SQL], block=True, semantic=sem)
    check("㉒ 不执行", n_exec, 0)
    check("㉒ 没有盲目修复（模型只调 1 次）", n_llm, 1)
    chain = r.get("status_chain") or {}
    check("㉒ requirement_verdict=unsure", chain.get("requirement_verdict"), "unsure")
    check_true("㉒ 错误说明强调【歧义】而非「失败」", "歧义" in (r.get("error") or ""),
               r.get("error"))
    nc = r.get("needs_clarification") or {}
    check_true("㉒ 给了待确认问题", bool(nc.get("questions")), nc)
    check_true("㉒ 把模型的假设变成了问句",
               any("只统计 101" in q for q in (nc.get("questions") or [])), nc.get("questions"))


# ═══════════════════════════════════════════════════════════
# ㉓ 语义层不可用（超时/解析失败）→ skipped，不阻断
# ═══════════════════════════════════════════════════════════
def test_semantic_unavailable_is_skipped():
    print("\n【㉓】语义层超时 → verdict=skipped（不是 pass），且不阻断执行")
    patch = {}
    _set = _install(patch)
    # ★ 换回真实语义层实现：本用例要验证"调不通时如何降级"，替身测不出来
    _set(RR, "review_semantic", _ORIG_REVIEW_SEMANTIC)

    def boom(sp, user, timeout=None):
        raise TimeoutError("timed out")

    _set(llm, "chat", boom)
    try:
        RR.clear_semantic_cache()
        c = RR.build_contract("应用主体只看 101", {}, MODIFY_CTX)
        sem = RR.review_semantic(GOOD_SQL, c, base_question="上轮问题", base_sql=BASE_SQL)
        check("㉓ review_semantic 返回 skipped", sem["verdict"], "skipped")
        check("㉓ source=error", sem["source"], "error")
        check("㉓ degraded=True", sem["degraded"], True)

        res = RR.review(GOOD_SQL, c)
        check("㉓ review 合并后 verdict=skipped（不是 pass）", res["verdict"], "skipped")
        check("㉓ review 标记 degraded", res["degraded"], True)
    finally:
        _restore(patch)


# ═══════════════════════════════════════════════════════════
# ㉔ 确定性层已判死 → 不调语义层（省一次模型调用）
# ═══════════════════════════════════════════════════════════
def test_no_llm_when_deterministic_fails():
    print("\n【㉔】确定性层已抓到 error → 不再调语义层（能确定性判死的不花钱再问一遍）")
    calls = {"n": 0}

    def sem(sql, contract, **kw):
        calls["n"] += 1
        return {"verdict": "pass", "issues": [], "assumptions": [],
                "degraded": False, "source": "stub"}

    _, n_exec, r, _ = _run([BAD_SQL], block=False, semantic=sem)
    check("㉔ 语义层零调用", calls["n"], 0)
    check("㉔ 仍然执行（灰度模式只告警）", n_exec, 1)
    check("㉔ verdict=fail", (r.get("status_chain") or {}).get("requirement_verdict"), "fail")


# ═══════════════════════════════════════════════════════════
# ㉕ 经验摘要（审核侧的证据输入）
# ═══════════════════════════════════════════════════════════
def test_audit_digest():
    print("\n【㉕】经验摘要：把成功写法与已知错误模式压成审核可用的短证据")
    m = sql_experience.ExperienceMatch()
    m.cases = [{"case_no": "C-0001", "trust_zh": "黄金", "question": "按应用主体统计注册数",
                "grain": "一行=一个应用主体", "sql_raw": "SELECT a AS x FROM t"}]
    m.failures = [{"case_no": "F-0002", "failure_type": "wrong_status",
                   "failure_symptom": "漏了状态条件", "corrected_sql": "SELECT b FROM t2"}]
    d = m.audit_digest()
    check_true("㉕ 含防照搬声明", "以本次要求为准" in d, d[:80])
    check_true("㉕ 含成功案例编号与写法", "C-0001" in d and "SELECT a AS x FROM t" in d, d)
    check_true("㉕ 含失败模式与修正写法", "F-0002" in d and "SELECT b FROM t2" in d, d)
    # 空经验时不得产出空段落
    check("㉕ 无经验时返回空串", sql_experience.ExperienceMatch().audit_digest(), "")


# ═══════════════════════════════════════════════════════════
# ㉖ 失败经验回流（需求类错误能被沉淀且幂等）
# ═══════════════════════════════════════════════════════════
def test_record_requirement_failure():
    print("\n【㉖】把「未满足需求」的 SQL 沉淀为失败经验（幂等、可被检索到）")
    conn = config_store._connect()
    sql_experience.migrate_schema(conn.cursor())
    conn.commit()

    cid, msg = sql_experience.record_requirement_failure(
        "应用主体只看 101", BAD_SQL,
        [{"message": "替换写成了追加", "rule": "req_replace_appended"}],
        tables=["ods_valet_order_all"], metrics=["注册客户数"])
    check_true("㉖ 写入成功", bool(cid), msg)
    case = sql_experience.get_case(cid)
    check("㉖ failure_type=requirement_mismatch", case["failure_type"], "requirement_mismatch")
    check("㉖ exec_ok=True（跑得通但结果不对，这类最该被记住）", case["exec_ok"], 1)
    check_true("㉖ 存了表与指标（否则以后永远召不回来）",
               "ods_valet_order_all" in (case["tables"] or [])
               and "注册客户数" in (case["metrics"] or []), case)

    # 幂等：同一条错 SQL 再写一次，应更新同一条而不是新增
    cid2, _ = sql_experience.record_requirement_failure(
        "应用主体只看 101", BAD_SQL,
        [{"message": "替换写成了追加", "rule": "req_replace_appended"}],
        tables=["ods_valet_order_all"], metrics=["注册客户数"])
    check("㉖ 幂等（同一条 SQL 不重复膨胀）", cid2, cid)


def main():
    for fn in (test_repair_then_execute, test_blocked_after_retries,
               test_degraded_skipped_not_pass, test_security_precedes_requirement,
               test_failure_has_no_rows, test_status_invariant,
               test_revalidate_after_repair, test_disabled_is_noop,
               test_advisory_mode_records_honestly, test_static_invariants,
               test_semantic_fail_then_repair, test_semantic_unsure_asks_user,
               test_semantic_unavailable_is_skipped, test_no_llm_when_deterministic_fails,
               test_audit_digest, test_record_requirement_failure):
        try:
            fn()
        except Exception as e:
            import traceback
            FAILED.append(f"{fn.__name__} 异常：{e}")
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
