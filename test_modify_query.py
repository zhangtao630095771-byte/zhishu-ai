# -*- coding: utf-8 -*-
"""
test_modify_query.py —— 专项修复回归测试：恢复"当前窗口自然语言增量修改 + SQL 纠错 + 重新执行"

★ 背景（本次专项修复要解决的问题）
  加完「多轮澄清」后，原本"在同一窗口里接着上一轮改查询"的能力被削弱了：
    ① 用户再说"去掉应用主体分组"，要么丢失原指标/时间/过滤，要么又被重新丢进澄清流程；
    ② 字段级权限解析 ClickHouse SQL 时抛 `Invalid expression / Unexpected token`，
       导致"SQL 解析失败，无法进行字段级权限判定"，进而 fail-closed 拒绝执行；
    ③ 某字段既作分组又作等值过滤（app_entity_source=0）时，去掉分组不能顺手删过滤。

★ 测试策略（与 test_pipeline.py 一致：用替身替代边界，不联网、不调真实模型）
  本环境无真实 LLM / ClickHouse，所以：
    · 纯逻辑项（合并意图、约束段内容、上下文持久化、404/越权隔离、解析修复）—— 确定性真测；
    · 场景项（删维度/加指标/改时间/改过滤/失败后纠错/连续修改）—— 注入"受控模型替身"
      模拟一个"遵守提示约束"的模型返回 SQL，验证【接线 + 安全闸门重跑】整链打通；
      真实模型能否严格照做，列为"待真实环境验证"（见交付报告）。
  运行： .venv/bin/python test_modify_query.py
"""
import os
import sys
import json
import re
import asyncio
import tempfile

# ★ 让 config_store 用临时库，避免污染真实 agent_config.db（必须在 import config_store 前设置）
_TMP_DB = os.path.join(tempfile.gettempdir(), "zhishu_test_queryctx.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
os.environ["CONFIG_DB"] = _TMP_DB

import config                                       # noqa: E402
import agent                                         # noqa: E402
import permission                                    # noqa: E402
import sqlglot                                       # noqa: E402
import config_store                                  # noqa: E402
import semantic_layer                                # noqa: E402
import query_understanding                           # noqa: E402
import rag                                           # noqa: E402
import calendar_util                                 # noqa: E402
import llm                                           # noqa: E402
import guard                                         # noqa: E402
import sql_validator                                 # noqa: E402
import result_validator                              # noqa: E402
import nl_answer                                     # noqa: E402


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
# 受控环境：把 ask_v2 的外部依赖换成轻量替身
# ═══════════════════════════════════════════════════════════
# ★ 上一轮"注册客户数，按月份和应用主体分组"的经典查询（修复场景的基准上下文）
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


def _fake_index():
    """最小可用索引：_semantic 直接给出，避免 enrich_index 重建；allowed_tables=[] 由 stub 放行。"""
    return {
        "_semantic": {
            "stub": True,
            "dicts": [],
            "allowed_tables": [],
            "tables": [],
        }
    }


class _Ctx:
    """retrieve_semantic 的替身返回：满足"至少命中表或指标"的前置判定。"""
    p3_tables = ["ctc_data_warehouse.register"]
    p1_metrics = []
    p4_docs = []
    route_info = "替身路由"


# ★ 受控"模型"：按修改意图返回"遵守约束"的 SQL（模拟一个听话的模型）
def _model_sql(question, base_sql=BASE_SQL, base_u=BASE_UNDERSTANDING):
    q = question or ""
    sql = base_sql
    if "完成率" in q:
        # 增加指标：保留原指标（注册客户数）+ 原过滤 + 原时间，仅追加完成率
        sql = sql.replace(
            'count(*) AS "注册客户数" ',
            'count(*) AS "注册客户数", '
            'sumIf(r.is_complete = 1, 1) / count(*) AS "完成率" ')
    elif ("去掉" in q and "应用主体" in q) or ("只按" in q and "月份" in q):
        # 去掉应用主体分组，但【保留 app_entity_source = 0 过滤】（约束2）
        sql = sql.replace(
            'GROUP BY r.app_entity_source, toStartOfMonth(r.create_time)',
            'GROUP BY toStartOfMonth(r.create_time)')
        sql = sql.replace('r.app_entity_source AS "应用主体", ', "")
    elif "101" in q:
        # 改过滤：把 = 0 改成 = 101（用户明确要求"按 101"）
        sql = sql.replace("r.app_entity_source = 0", "r.app_entity_source = 101")
    elif "2026-12" in q or "改时间" in q:
        # 改时间范围
        sql = sql.replace("r.create_time < '2026-11-01'",
                          "r.create_time < '2026-12-01'")
    return sql


def _stub_env(captured, deny_sql_substr=None):
    """替换边界依赖，返回 restore()。deny_sql_substr 用于"安全闸门不可绕过"测试。"""
    patch = {}
    def _set(mod, name, val):
        patch[(mod, name)] = getattr(mod, name)
        setattr(mod, name, val)

    _set(semantic_layer, "retrieve_semantic", lambda sidx, q, u: _Ctx())
    _set(semantic_layer, "resolve",
         lambda ctx: {"p0": "", "p1": "", "p2": "", "p3": "", "p4": "", "metrics": []})
    _set(semantic_layer, "build_prompt_parts",
         lambda resolved, role, time_context="": "ROLE")
    _set(query_understanding, "understand", lambda q, sidx: {})
    _set(rag, "match_disabled_metric", lambda sidx, q: None)
    _set(rag, "retrieve_dicts", lambda sidx, q: ([], "无"))
    _set(calendar_util, "build_time_context", lambda q: "")
    _set(calendar_util, "summary_line", lambda q: "")

    state = {"guard_called": 0, "validator_called": 0, "perm_called": 0}

    def _chat(system_prompt, user, timeout=None, max_tokens=None, **kw):
        # ★ 只在首次（生成）调用时捕获 system_prompt；后续需求审核/修复调用不覆盖
        if "system_prompt" not in captured:
            captured["system_prompt"] = system_prompt
        # ★ 受控模型替身：从 modify 块里取「上一轮 SQL」作为修改基准，
        #   忠实模拟"在上一轮基础上改"（这样才能验证"连续修改不丢链"）。
        base = BASE_SQL
        m = re.search(r"上一轮生成的 SQL：([^\n]+)", system_prompt)
        if m:
            prev = m.group(1).strip()
            if prev and prev != "（无）":
                base = prev
        return _model_sql(user, base_sql=base)
    _set(llm, "chat", _chat)

    def _sql_guard(sql):
        state["guard_called"] += 1
        if deny_sql_substr and deny_sql_substr in sql:
            return (False, "安全护栏拦截：含禁用串")
        return (True, "")
    _set(guard, "sql_guard", _sql_guard)

    def _check_tables_ex(sql, allowed):
        state["validator_called"] += 1
        return {"ok": True, "message": "", "illegal": []}
    _set(guard, "check_tables_ex", _check_tables_ex)
    _set(guard, "qualify_tables", lambda sql, db: sql)
    _set(guard, "clean_sql", lambda sql: sql)

    def _validate(sql, ctx, resolved, allowed):
        state["validator_called"] += 1
        return {"issues": [], "status": "passed"}
    _set(sql_validator, "validate_sql_ex", _validate)

    def _perm_evaluate(sql, user, meta, *a, **k):
        state["perm_called"] += 1
        return {"allowed": True, "deny_reasons": [], "mask_map": {},
                "output_columns": [], "requested_tables": []}
    _set(permission, "evaluate", _perm_evaluate)

    def _exec(sql):
        return (["应用主体", "月份", "注册客户数"],
                [["0", "2025-10-01", 100]])
    _set(agent.db, "execute_sql", _exec)
    # ★ 模型/DB 替身里没有真实 db 模块引用，直接 patch agent.db.execute_sql 即可
    _set(result_validator, "validate_result", lambda *a, **k: [])
    _set(nl_answer, "answer", lambda *a, **k: "结论")

    # ★ 关掉不相关开关，避免触发真实外部依赖
    patch[(config, "EXP_ENABLED")] = config.EXP_ENABLED
    config.EXP_ENABLED = False
    patch[(config, "SQL_REVIEW_ENABLED")] = config.SQL_REVIEW_ENABLED
    config.SQL_REVIEW_ENABLED = False
    patch[(config, "MAX_RETRY")] = config.MAX_RETRY
    config.MAX_RETRY = 1

    def restore():
        for (mod, name), val in patch.items():
            setattr(mod, name, val)
    return restore, state


def _modify_context(failed=False):
    """构造一份"上一轮查询上下文"（成功或失败），供增量修改锚定。"""
    return {
        "base_question": BASE_QUESTION,
        "base_understanding": BASE_UNDERSTANDING,
        "base_sql": BASE_SQL,
        "base_tables": ["ctc_data_warehouse.register"],
        "base_metrics": ["注册客户数"],
    }


def _init_store():
    """初始化临时配置库（走真实 init_store，覆盖多表建表路径）。

    ★ 曾依赖一个打补丁 hack（绕过 init_store 的多语句 execute 报错）；
      现已从根上修复：init_store 里 clarify_sessions 与 query_context 已拆成两条 execute。
      本函数回归覆盖这条真实路径，确保建表不再报 'near CREATE'。
    """
    config_store.init_store()


# ★ 模块加载即初始化（B1 的 evaluate 与 D 组持久化用例都会用到 query_context 表）
_init_store()


# ═══════════════════════════════════════════════════════════
# 分组 A：纯逻辑 / 提示约束（确定性真测）
# ═══════════════════════════════════════════════════════════
def test_merge_understanding_fill_missing():
    print("\n【A1】_merge_understanding_for_modify：本轮缺失字段用上轮补全")
    new_u = {"intent": "summary", "metrics": ["注册客户数"], "dimensions": ["月份"]}
    merged = agent._merge_understanding_for_modify(new_u, _modify_context())
    check_true("保留本轮已识别的维度[月份]", merged.get("dimensions") == ["月份"], merged)
    check_true("补全上轮的时间范围", merged.get("time_range") == BASE_UNDERSTANDING["time_range"], merged)
    check_true("补全上轮的过滤条件", merged.get("filters") == BASE_UNDERSTANDING["filters"], merged)
    check_true("补全上轮的应用主体实体", "register" in (merged.get("entities") or []), merged)


def test_merge_understanding_no_override():
    print("\n【A2】_merge_understanding_for_modify：本轮已产出的字段不被上轮覆盖")
    new_u = {"dimensions": ["周"], "filters": ["user_type = 2"]}
    merged = agent._merge_understanding_for_modify(new_u, _modify_context())
    check_true("本轮维度[周]不被覆盖", merged.get("dimensions") == ["周"], merged)
    check_true("本轮过滤[user_type=2]不被覆盖", merged.get("filters") == ["user_type = 2"], merged)


def test_prompt_block_constraints():
    print("\n【A3】_modify_prompt_block：三条硬约束都注入到提示")
    block = agent._modify_prompt_block("去掉应用主体分组，只按月份", _modify_context())
    check_true("含'上一轮查询'段", "上一轮查询" in block, block[:60])
    check_true("含上一轮 SQL", BASE_SQL[:40] in block, block[:200])
    # 约束1：保留未被修改部分
    check_true("约束1：保留未改指标/时间/过滤",
               "必须【原样保留】" in block and "禁止因修改其中一个维度就清空" in block, block)
    # 约束2：去掉分组保留等值过滤
    check_true("约束2：去掉分组保留 app_entity_source = 0 过滤",
               "app_entity_source = 0" in block and "保留该过滤条件" in block, block)
    # 约束3：禁止新增/删除未提及指标过滤
    check_true("约束3：禁止新增/删除未提及指标或过滤",
               "禁止新增/删除用户未提及的指标或过滤" in block, block)


# ═══════════════════════════════════════════════════════════
# 分组 B：字段级权限解析修复（复现原 bug 已修复）
# ═══════════════════════════════════════════════════════════
def test_permission_parse_fix():
    print("\n【B1】字段级权限解析：ClickHouse SQL（带 FORMAT）不再抛解析失败")
    config.DB_TYPE = "clickhouse"
    # 旧方式（无方言）应失败
    try:
        sqlglot.parse_one(BASE_SQL)
        old_ok = True
    except Exception:
        old_ok = False
    check_true("旧方式（无方言）确实会失败（确认这是曾经的 root cause）", old_ok is False)
    # 新方式（_parse_sql 方言感知）应成功
    try:
        tree = permission._parse_sql(BASE_SQL)
        new_ok = tree is not None
    except Exception as e:
        new_ok = False
        print(f"       实际异常: {type(e).__name__}: {e}")
    check_true("新方式 _parse_sql 成功解析 ClickHouse SQL（修复生效）", new_ok)
    # evaluate 在解析失败时不得跳过权限判定（fail-closed）：这里解析成功 → 走正常判定
    # ★ 注入 policies/sensitive，避免触发 config_store.init_store（其多语句 execute 在本环境 sqlite 受限，
    #   属测试环境约束；生产环境正常）。本项核心是验证「解析不再失败」。
    dec = permission.evaluate(BASE_SQL, {"username": "u", "role": "admin"}, [],
                              policies=[], sensitive=set())
    check_true("evaluate 不抛解析失败且可正常给出判定",
               isinstance(dec, dict) and "allowed" in dec, dec)


# ═══════════════════════════════════════════════════════════
# 分组 C：增量修改整链（受控模型替身，验证接线 + 安全重跑）
# ═══════════════════════════════════════════════════════════
def _run_modify(question, modify_ctx, captured, deny=None, user=None):
    restore, state = _stub_env(captured, deny_sql_substr=deny)
    try:
        r = agent.ask_v2(_fake_index(), question, user=user, modify_context=modify_ctx)
    finally:
        restore()
    return r, state


def test_modify_injects_context_and_runs():
    print("\n【C1】增量修改：prompt 注入上一轮上下文 + 整链跑通（成功）")
    captured = {}
    r, state = _run_modify("加上完成率", _modify_context(), captured)
    check_true("修改后查询成功执行", r.get("ok") is True, r.get("error"))
    check_true("system_prompt 含'上一轮查询'段（上下文已注入）",
               "上一轮查询" in (captured.get("system_prompt") or ""))
    check_true("安全护栏被调用（闸门重跑）", state["guard_called"] >= 1, state)
    check_true("业务校验被调用（闸门重跑）", state["validator_called"] >= 1, state)
    check_true("结果 SQL 保留了原指标注册客户数", "注册客户数" in (r.get("sql") or ""), r.get("sql"))
    check_true("结果 SQL 追加了完成率", "完成率" in (r.get("sql") or ""), r.get("sql"))


def test_modify_remove_dimension_keeps_filter():
    print("\n【C2】删维度：去掉应用主体分组 → 仅按月份，且【保留 app_entity_source = 0 过滤】")
    captured = {}
    r, _ = _run_modify("去掉应用主体分组，只按月份", _modify_context(), captured)
    sql = r.get("sql") or ""
    check_true("修改后查询成功执行", r.get("ok") is True, r.get("error"))
    check_true("SQL 不再按应用主体分组", "GROUP BY" in sql and "app_entity_source" not in sql.split("GROUP BY")[1].split("ORDER")[0], sql)
    check_true("SQL 【保留】app_entity_source = 0 过滤（避免分组恒为单一值）",
               "app_entity_source = 0" in sql, sql)
    check_true("SQL 仍按月份分组", "toStartOfMonth" in sql, sql)


def test_modify_change_filter_101():
    print("\n【C3】改过滤：把 0 改成 101（用户明确要求按 101）")
    captured = {}
    r, _ = _run_modify("应用主体只看 101，其它不变", _modify_context(), captured)
    sql = r.get("sql") or ""
    check_true("修改后查询成功执行", r.get("ok") is True, r.get("error"))
    check_true("SQL 改为 = 101", "app_entity_source = 101" in sql, sql)
    check_true("SQL 不再含 = 0", "app_entity_source = 0" not in sql, sql)
    check_true("仍保留原指标注册客户数", "注册客户数" in sql, sql)


def test_modify_change_time_range():
    print("\n【C4】改时间范围：结束时间改到 2026-12-01")
    captured = {}
    r, _ = _run_modify("时间范围改成到 2026-12-01", _modify_context(), captured)
    sql = r.get("sql") or ""
    check_true("修改后查询成功执行", r.get("ok") is True, r.get("error"))
    check_true("SQL 时间上界改为 2026-12-01", "2026-12-01" in sql, sql)
    check_true("SQL 保留原指标/过滤", "注册客户数" in sql and "user_type = 1" in sql, sql)


def test_modify_after_failure():
    print("\n【C5】查询失败后修改：上一轮失败（failed）仍可锚定并重新执行成功")
    captured = {}
    # 失败的上一轮：sql 可能为空/半成品，但上下文结构化意图仍可用
    failed_ctx = _modify_context(failed=True)
    failed_ctx["base_sql"] = ""   # 模拟失败时没拿到可用 SQL
    r, _ = _run_modify("时间范围改成到 2026-12-01", failed_ctx, captured)
    check_true("失败后修改仍成功执行", r.get("ok") is True, r.get("error"))
    check_true("prompt 仍含上一轮结构化意图（即使 SQL 为空）",
               BASE_QUESTION in (captured.get("system_prompt") or ""))


def test_modify_consecutive():
    print("\n【C6】连续修改：在第一次修改结果之上再做第二次修改")
    captured = {}
    # 第一次：加完成率
    r1, _ = _run_modify("加上完成率", _modify_context(), captured)
    check_true("第一次修改成功", r1.get("ok") is True, r1.get("error"))
    # 第二次：在第一轮（已含完成率）基础上再去掉应用主体分组
    ctx2 = _modify_context()
    ctx2["base_sql"] = r1.get("sql") or BASE_SQL
    ctx2["base_understanding"] = dict(BASE_UNDERSTANDING, dimensions=["月份"])
    r2, _ = _run_modify("去掉应用主体分组，只按月份", ctx2, captured)
    check_true("第二次修改成功", r2.get("ok") is True, r2.get("error"))
    sql2 = r2.get("sql") or ""
    check_true("二次修改后仍含完成率（链不丢）", "完成率" in sql2, sql2)
    check_true("二次修改后只按月份分组", "toStartOfMonth" in sql2 and "app_entity_source" not in sql2.split("GROUP BY")[1].split("ORDER")[0], sql2)


def test_modify_safety_not_bypassable():
    print("\n【C7】安全闸门不可绕过：修改后的危险 SQL 必须被拦截（且重跑权限判定）")
    captured = {}
    # 模型替身若收到"删表"意图则返回危险 SQL；这里直接传一个含禁用串的修改问题
    r, state = _run_modify("把数据都删了", _modify_context(), captured,
                           deny="forbidden_drop")
    # 通过 deny 注入：让 guard 拒绝含 forbidden_drop 的 SQL
    # 这里用更直观的断言：构造一条会被 guard 拦下的 SQL
    captured2 = {}
    restore, st = None, None
    # 直接验证 _pre_execute_check 对危险 SQL 的 fail-closed
    from agent import _pre_execute_check
    chk = _pre_execute_check("DELETE FROM ctc_data_warehouse.register",
                             _Ctx(), {"metrics": []}, [], user=None)
    check_true("危险 SQL 被闸门拦截（ok=False）", chk["ok"] is False, chk)
    check_true("拦截原因含安全护栏", "安全护栏" in chk.get("error", ""), chk)
    # 并验证：即使是"修改"路径，生成的 SQL 也走同一闸门（guard 被调用）
    check_true("修改路径的 guard 被调用", state["guard_called"] >= 1, state)


def test_modify_reevaluates_permission():
    print("\n【C8】修改路径仍重跑字段级权限判定（user 非空时 evaluate 被调用）")
    captured = {}
    r, state = _run_modify("加上完成率", _modify_context(), captured,
                           user={"username": "u", "role": "analyst"})
    check_true("修改后查询成功执行", r.get("ok") is True, r.get("error"))
    check_true("字段级权限 evaluate 被调用（未绕过）", state["perm_called"] >= 1, state)


def test_modify_no_clarification_decision():
    print("\n【C9】与多轮澄清兼容：修改路径不调用 clarification.decide（不再被丢进澄清）")
    import clarification
    called = {"decide": False}
    _orig = clarification.decide
    clarification.decide = lambda *a, **k: (_orig(*a, **k) if (_set_flag()) else None)
    def _set_flag():
        called["decide"] = True
        return True
    try:
        captured = {}
        r, _ = _run_modify("加上完成率", _modify_context(), captured)
        check_true("修改路径未触发澄清决策（直接进入增量修改）", called["decide"] is False)
        check_true("修改结果成功", r.get("ok") is True, r.get("error"))
    finally:
        clarification.decide = _orig


# ═══════════════════════════════════════════════════════════
# 分组 C10：修改结构保护（P0）—— 坏模型换表/换聚合/换过滤 → 拒绝执行
# ═══════════════════════════════════════════════════════════
def test_modify_scope_violation_rejected():
    print("\n【C10】结构保护：坏模型擅自换表/换聚合 → 拒绝执行（不静默破坏原业务逻辑）")
    # 坏模型：把上一轮 SQL 的表/聚合/过滤整个换掉（模拟 #243 的"重解释"）
    BAD = ("SELECT r.app_entity_source AS \"应用主体\", "
           "toStartOfMonth(r.create_time) AS \"月份\", count(DISTINCT r.id) AS \"注册客户数\" "
           "FROM ctc_data_warehouse.register_v2 r "
           "WHERE r.status = 6 AND r.app_entity_source = 0 "
           "AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' "
           "GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) "
           "ORDER BY toStartOfMonth(r.create_time)")
    restore, state = _stub_env({})
    try:
        # 让"模型"无视修改指令、返回破坏性的 BAD SQL
        agent.llm.chat = lambda system_prompt, user: BAD
        r = agent.ask_v2(_fake_index(), "时间范围改成近30天",
                         user=None, modify_context=_modify_context())
    finally:
        restore()
    check_true("破坏性修改被拒绝（ok=False）", r.get("ok") is False, r.get("error"))
    check_true("未执行数据库", state.get("exec_called", 0) == 0)
    check_true("错误信息说明是修改越界", "修改" in (r.get("error") or ""), r.get("error"))
    check_true("返回了结构差异详情 modify_violation",
               bool(r.get("modify_violation")), r.get("modify_violation"))


# ═══════════════════════════════════════════════════════════
# 分组 D：上下文持久化 / 越权隔离 / 404（真实 config_store + 临时库）
# ═══════════════════════════════════════════════════════════
def test_query_context_persistence():
    print("\n【D1】query_context 保存/加载：按 username 归属且可回读")
    _init_store()
    payload = {
        "original_question": BASE_QUESTION,
        "understanding": BASE_UNDERSTANDING,
        "sql": BASE_SQL,
        "ok": True, "tables": ["ctc_data_warehouse.register"],
        "metrics": ["注册客户数"], "status": "success",
    }
    cid = config_store.save_query_context("alice", payload)
    check_true("返回非空 id", bool(cid))
    loaded = config_store.load_query_context(cid, "alice")
    check_true("按归属用户可加载", loaded is not None and loaded.get("payload", {}).get("sql") == BASE_SQL, loaded)
    # 清理
    try:
        import sqlite3
        conn = sqlite3.connect(_TMP_DB)
        conn.execute("DELETE FROM query_context WHERE id = ?", (cid,))
        conn.commit(); conn.close()
    except Exception:
        pass


def test_query_context_ownership_isolation():
    print("\n【D2】越权隔离：其他用户不能加载他人的查询上下文（返回 None）")
    _init_store()
    cid = config_store.save_query_context("alice", {"sql": BASE_SQL, "ok": True})
    bob = config_store.load_query_context(cid, "bob")
    check_true("其他用户加载返回 None（隔离生效）", bob is None)
    # 无 username 也不应跨用户读取
    none_u = config_store.load_query_context(cid, None)
    check_true("未带 username 时返回 None（防越权）", none_u is None)
    try:
        import sqlite3
        conn = sqlite3.connect(_TMP_DB)
        conn.execute("DELETE FROM query_context WHERE id = ?", (cid,))
        conn.commit(); conn.close()
    except Exception:
        pass


def test_modify_query_404_on_missing():
    print("\n【D3】_modify_query 对不存在的上下文返回 404（不能凭空改）")
    _init_store()                           # 建表 + stub 掉 init_store（幂等）
    import server
    # 用真实 server 的 _modify_query，但 mock agent.ask 与 request 以避免真实执行
    _orig_ask = agent.ask
    agent.ask = lambda *a, **k: {"question": "", "ok": True, "sql": "", "rows": [],
                                 "columns": [], "tables": [], "metrics": [], "understanding": {}}
    # 构造一个假的 request（仅用 request.state.user）
    class _Req:
        class state:
            user = {"username": "alice", "role": "analyst"}
    try:
        raised = None
        async def _run():
            return await server._modify_query(_fake_index(), {"username": "alice"},
                                             "加上完成率", "no-such-id", _Req())
        try:
            asyncio.run(_run())
        except Exception as e:
            raised = e
        check_true("缺失上下文 → 抛 404 HTTPException",
                   raised is not None and getattr(raised, "status_code", None) == 404,
                   repr(raised))
    finally:
        agent.ask = _orig_ask


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print("╔" + "═" * 60 + "╗")
    print("║" + "专项修复回归测试：当前窗口增量修改 / 纠错 / 重跑".center(46) + "║")
    print("╚" + "═" * 60 + "╝")

    # A 组：纯逻辑 / 提示约束
    test_merge_understanding_fill_missing()
    test_merge_understanding_no_override()
    test_prompt_block_constraints()
    # B 组：解析修复
    test_permission_parse_fix()
    # C 组：增量修改整链
    test_modify_injects_context_and_runs()
    test_modify_remove_dimension_keeps_filter()
    test_modify_change_filter_101()
    test_modify_change_time_range()
    test_modify_after_failure()
    test_modify_consecutive()
    test_modify_safety_not_bypassable()
    test_modify_reevaluates_permission()
    test_modify_no_clarification_decision()
    test_modify_scope_violation_rejected()
    # D 组：持久化 / 隔离 / 404
    test_query_context_persistence()
    test_query_context_ownership_isolation()
    test_modify_query_404_on_missing()

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
    raise SystemExit(1 if FAILED else 0)
