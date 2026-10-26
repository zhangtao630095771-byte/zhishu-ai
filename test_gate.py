# -*- coding: utf-8 -*-
"""
test_gate.py —— 「正确性闸门」专项测试

★ 背景（代码审查报告 P0-2 / P0-3 / P1-1）：
  校验器若抽不出闸门、降级若静默绕过校验，就会出现
  【看起来有数、其实口径错】的结果 —— 比直接报错危险得多。
  这里把这几条闸门用自动化测试钉住：

  ① 校验器重试耗尽仍有 error → 绝不执行 DB（P0-2）
  ② V2 抛【不可恢复】异常 → fail-closed，不回退 legacy（P0-3）
  ③ V2 抛白名单内可恢复故障 → 允许回退，且结果留痕（P0-3）
  ④ 指标硬约束（required_predicates/columns）缺失 → error，且「同族任一满足」不误拦（P1-1）

运行： .venv/bin/python test_gate.py   （不联网、不调模型、秒级）
"""
import ast
import os
import re
import subprocess
import sys
import tempfile

import agent
import config_store
import semantic_layer
import query_understanding
import calendar_util
import sql_validator
import rag
import llm
import guard
import db
import config


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
# 测试替身：把 ask_v2 的外部依赖替换成轻量桩
# ═══════════════════════════════════════════════════════════
class _Ctx:
    p3_tables = ["ods_valet_order_all"]
    p1_metrics = []
    p2_relationships = []
    p3_table_objs = []
    p4_docs = []
    route_info = "替身路由"


def _stubs(patch):
    def _set(mod, name, val):
        key = (mod, name)
        # ★ 只记录【第一次】替换前的原值：同一属性被替换两次时，
        #   若每次都覆盖 patch，恢复时会把第一次的替身当成原值（测试互相污染）。
        if key not in patch:
            patch[key] = getattr(mod, name)
        setattr(mod, name, val)

    _set(semantic_layer, "retrieve_semantic", lambda s, q, u: _Ctx())
    _set(semantic_layer, "resolve", lambda c: {"p0": "", "p1": "", "p2": "", "p3": "",
                                               "p4": "", "metrics": [], "bindings": {}})
    _set(semantic_layer, "build_prompt_parts", lambda r, role, time_context="": "ROLE")
    _set(query_understanding, "understand", lambda q, s: {})
    _set(rag, "match_disabled_metric", lambda s, q: None)
    _set(rag, "retrieve_dicts", lambda s, q: ([], "无字典"))
    _set(calendar_util, "build_time_context", lambda q: "")
    _set(calendar_util, "summary_line", lambda q: "")
    _set(llm, "chat", lambda sp, u: "SELECT count(*) FROM ods_valet_order_all")
    _set(guard, "sql_guard", lambda sql: (True, ""))
    _set(guard, "check_tables", lambda sql, allowed: (True, []))
    # ★ 统一闸门改造后，agent 走的是 ex 版本（带状态），两个都要替身
    _set(guard, "check_tables_ex",
         lambda sql, allowed, strict=None: {
             "ok": True, "illegal": [], "status": "parsed",
             "method": "stub", "reason": "", "message": ""})
    _set(sql_validator, "validate_sql_ex",
         lambda sql, ctx, resolved, allowed: {
             "issues": [], "status": "passed", "degraded": [], "error": ""})
    return _set


def _restore(patch):
    for (mod, name), val in patch.items():
        setattr(mod, name, val)


def _fake_index():
    return {"_semantic": {"stub": True, "allowed_tables": ["ods_valet_order_all"]},
            "allowed_tables": ["ods_valet_order_all"]}


# ═══════════════════════════════════════════════════════════
# ① P0-2：校验 error 重试耗尽 → 不执行
# ═══════════════════════════════════════════════════════════
def test_validator_gate_blocks_execution():
    print("\n【P0-2】校验器重试耗尽仍有 error → 必须拒绝执行（不得调用 db.execute_sql）")
    patch = {}
    _set = _stubs(patch)
    executed = {"n": 0}
    _set(sql_validator, "validate_sql_ex",
         lambda sql, ctx, resolved, allowed: {
             "issues": [{"severity": "error", "tier": "P1", "rule": "stub",
                         "message": "替身：故意报一个 error", "suggestion": "改"}],
             "status": "failed", "degraded": [], "error": ""})
    _set(db, "execute_sql", lambda sql: (executed.__setitem__("n", executed["n"] + 1), ([], []))[1])
    try:
        r = agent.ask_v2(_fake_index(), "查询代驾完单量")
    finally:
        _restore(patch)
    check("① 未执行数据库（execute_sql 调用次数）", executed["n"], 0)
    check("① 返回 ok=False", r.get("ok"), False)
    check("① 附带 validation 报告", bool(r.get("validation")), True)


# ═══════════════════════════════════════════════════════════
# ② P0-3：不可恢复异常 → fail-closed（不回退 legacy）
# ═══════════════════════════════════════════════════════════
def test_fail_closed_on_unrecoverable():
    print("\n【P0-3】V2 抛不可恢复异常 → fail-closed，不得回退 legacy")
    orig_v2, orig_legacy = agent.ask_v2, agent.ask_legacy
    calls = {"legacy": 0}

    def boom(index, q):
        raise ValueError("语义层逻辑错误（不可恢复）")

    agent.ask_v2 = boom
    agent.ask_legacy = lambda index, q: (calls.__setitem__("legacy", calls["legacy"] + 1), {"ok": True})[1]
    try:
        r = agent.ask({}, "q")
    finally:
        agent.ask_v2, agent.ask_legacy = orig_v2, orig_legacy
    check("② 未回退 legacy（legacy 调用次数）", calls["legacy"], 0)
    check("② 返回 ok=False", r.get("ok"), False)
    check("② degraded=False", r.get("degraded"), False)
    check("② 记录了降级原因", bool(r.get("degrade_reason")), True)


# ═══════════════════════════════════════════════════════════
# ③ P0-3：白名单内可恢复故障 → 允许回退 + 留痕
# ═══════════════════════════════════════════════════════════
def test_recoverable_falls_back():
    print("\n【P0-3】V2 抛白名单内可恢复故障（超时）→ 允许回退，且结果留痕")
    orig_v2, orig_legacy = agent.ask_v2, agent.ask_legacy
    calls = {"legacy": 0}

    def boom(index, q):
        raise TimeoutError("connection timed out")

    agent.ask_v2 = boom
    agent.ask_legacy = lambda index, q: (calls.__setitem__("legacy", calls["legacy"] + 1), {"ok": True})[1]
    try:
        r = agent.ask({}, "q")
    finally:
        agent.ask_v2, agent.ask_legacy = orig_v2, orig_legacy
    check("③ 回退 legacy 一次", calls["legacy"], 1)
    check("③ pipeline=legacy", r.get("pipeline"), "legacy")
    check("③ degraded=True", r.get("degraded"), True)
    check("③ 记录降级原因", bool(r.get("degrade_reason")), True)


# ═══════════════════════════════════════════════════════════
# ④ P1-1：指标硬约束（缺失→error；同族任一满足不误拦）
# ═══════════════════════════════════════════════════════════
def test_metric_hard_constraints():
    print("\n【P1-1】指标硬约束 required_predicates：缺失→error；同族任一满足→通过")

    def _ctx(metrics):
        c = _Ctx()
        c.p1_metrics = metrics
        return c

    def _has(sql, metrics):
        return sql_validator._has_error(
            sql_validator.validate_sql(sql, _ctx(metrics), {"bindings": {}},
                                       ["ods_valet_order_all"]))

    dj = [{"name": "代驾完单量", "required_predicates": ["status = 40"]}]
    check("④ 缺 status=40 → error", _has("SELECT count(*) FROM ods_valet_order_all", dj), True)
    check("④ 有 status=40 → 无 error",
          _has("SELECT count(*) FROM ods_valet_order_all WHERE status = 40", dj), False)
    check("④ status IN (40) 亦满足",
          _has("SELECT count(*) FROM ods_valet_order_all WHERE status IN (40)", dj), False)

    # 同族：完单量(status=6) + 代驾完单量(status=40)，SQL 用 40 → 任一代满足 → 不报 error
    fam = [{"name": "完单量", "required_predicates": ["status = 6"]},
           {"name": "代驾完单量", "required_predicates": ["status = 40"]}]
    check("④ 同族任一满足 → 不误拦",
          _has("SELECT count(*) FROM ods_valet_order_all WHERE status = 40", fam), False)
    check("④ 同族全不满足 → error",
          _has("SELECT count(*) FROM ods_valet_order_all", fam), True)


# ═══════════════════════════════════════════════════════════
# ⑤ 拒答闸门：模型说"无法回答"时【不得执行】
#    真实事故：模型写成块注释 `/* 无法回答：… */`，旧代码只认 `--`，
#    结果"已声明无法回答却照样跑出个数字"。
# ═══════════════════════════════════════════════════════════
def test_refusal_recognition():
    print("\n【拒答识别】行注释 / 块注释 / 纯文字 都要认；正常 SQL 不能误判")
    cases = [
        ("-- 无法回答：缺少时间字段", True),
        ("/* 无法回答：现有表结构不足以计算 */", True),
        ("/* 缺少钻石司机等级字段，无法识别 */\nSELECT 1", True),
        ("-- 信息不足\nSELECT 1", True),
        ("无法回答：表不在授权范围", True),
        ("SELECT count(*) FROM t", False),
        ("WITH a AS (SELECT 1) SELECT * FROM a", False),
        ("/* 查询9月完单量 */ SELECT count(*) FROM t", False),
    ]
    for sql, should in cases:
        got = bool(agent._refusal_reason(sql))
        check(f"拒答识别 {sql[:26]!r}", got, should)


def test_refusal_blocks_execution():
    print("\n【拒答闸门】模型以块注释拒答 → 必须不执行 DB 并返回 ok=False")
    patch = {}
    _set = _stubs(patch)
    executed = {"n": 0}
    _set(llm, "chat", lambda sp, u: "/* 无法回答：现有表结构不足以计算（缺少钻石司机等级字段） */\n"
                                    "SELECT COALESCE(SUM(r.money), 0) FROM ods_valet_order_all r")
    _set(db, "execute_sql", lambda sql: (executed.__setitem__("n", executed["n"] + 1), ([], []))[1])
    try:
        r = agent.ask_v2(_fake_index(), "查询钻石司机资金流水里的提现金额汇总")
    finally:
        _restore(patch)
    check("⑤ 未执行数据库", executed["n"], 0)
    check("⑤ 返回 ok=False", r.get("ok"), False)
    check("⑤ error 带出拒答原因", "无法回答" in (r.get("error") or ""), True)


# ═══════════════════════════════════════════════════════════
# ⑥ ★ 静态契约：执行 SQL 只能走唯一出口 _exec_sql
#    这条是"防止同类 bug 再发生"的机制 —— 以后任何人新增一条执行路径，
#    只要绕开 _exec_sql，这个测试立刻失败。
# ═══════════════════════════════════════════════════════════
def test_single_execution_chokepoint():
    print("\n【静态契约】agent.py 中执行 SQL 必须只经由 _exec_sql（不得有裸 db.execute_sql）")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)                      # ★ 用 AST 而不是正则：文档字符串里提到
    #                                              `db.execute_sql(...)` 不该被算成调用

    def _is_exec_call(node):
        f = getattr(node, "func", None)
        return (isinstance(node, ast.Call) and isinstance(f, ast.Attribute)
                and f.attr == "execute_sql" and isinstance(f.value, ast.Name)
                and f.value.id == "db")

    all_calls = [n for n in ast.walk(tree) if _is_exec_call(n)]
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_exec_sql"), None)
    check_true("能找到 _exec_sql 定义", fn is not None)
    inside = len([n for n in ast.walk(fn) if _is_exec_call(n)]) if fn else 0
    check("agent.py 中 db.execute_sql 全部在 _exec_sql 内（总数=内部数）", len(all_calls), inside)
    check("_exec_sql 内恰好一次真正执行", inside, 1)
    # 反向确认：chokepoint 确实先过拒答判定（否则它只是个转发壳）
    fn_src = ast.get_source_segment(src, fn) if fn else ""
    check_true("_exec_sql 会先过拒答判定", "_refusal_reason(sql)" in fn_src,
               fn_src[-200:])


# ═══════════════════════════════════════════════════════════
# ⑦ ★ 闸门清单：每一种"判定不执行"都必须真的不执行（逐条注入验证）
#    并附带一条不变量：**失败结果不得带数据**（不能"说失败、下面却有数字"）
# ═══════════════════════════════════════════════════════════
def _ask_with(llm_reply, validate=None):
    """用给定 SQL 跑一遍 ask_v2（其余依赖替身），返回 (执行次数, 结果)"""
    patch = {}
    _set = _stubs(patch)
    executed = {"n": 0}
    _set(llm, "chat", lambda sp, u: llm_reply)
    _set(db, "execute_sql", lambda sql: (executed.__setitem__("n", executed["n"] + 1), (["c"], [[1]]))[1])
    if validate:
        # ★ 兼容旧签名：把 issues 列表包成 validate_sql_ex 的状态结构
        def _validate_ex(sql, ctx, resolved, allowed):
            issues = validate(sql, ctx, resolved, allowed)
            has_err = any(i.get("severity") == "error" for i in issues)
            return {"issues": issues,
                    "status": "failed" if has_err else "passed",
                    "degraded": [], "error": ""}
        _set(sql_validator, "validate_sql_ex", _validate_ex)
    try:
        r = agent.ask_v2(_fake_index(), "q")
    finally:
        _restore(patch)
    return executed["n"], r


def test_gate_checklist():
    print("\n【闸门清单】拒答的各种写法都必须【不执行】，且失败结果不得带数据")
    cases = [
        ("拒答·行注释", "-- 无法回答：缺少司机等级字段"),
        ("拒答·块注释", "/* 无法回答：现有表结构不足以计算 */\nSELECT count(*) FROM ods_valet_order_all"),
        ("拒答·纯文字", "无法回答：所需数据表不在已授权范围内"),
        ("拒答·信息不足", "-- 信息不足：未提供提现类型编码\nSELECT 1"),
        ("非查询语句", "抱歉，我无法确定你要问哪张表"),
    ]
    for name, sql in cases:
        n, r = _ask_with(sql)
        check(f"{name} → 未执行数据库", n, 0)
        check(f"{name} → ok=False", r.get("ok"), False)
        check(f"{name} → 失败结果不带数据行", list(r.get("rows") or []), [])

    # 对照组：正常 SQL 必须照常执行（防止"一刀切全拦"）
    n, r = _ask_with("SELECT count(*) FROM ods_valet_order_all")
    check("对照·正常 SQL → 照常执行", n >= 1, True)

    # 校验器报 error → 不执行（重试耗尽后仍拦）
    n, r = _ask_with("SELECT count(*) FROM ods_valet_order_all",
                     validate=lambda sql, ctx, resolved, allowed: [
                         {"severity": "error", "tier": "P1", "rule": "x",
                          "message": "替身 error", "suggestion": "s"}])
    check("校验 error → 未执行数据库", n, 0)
    check("校验 error → 失败结果不带数据行", list(r.get("rows") or []), [])


# ═══════════════════════════════════════════════════════════
# ⑧ S1 回归：自愈 SQL 必须重新过闸门
# ═══════════════════════════════════════════════════════════
def test_repair_must_revalidate():
    print("\n【S1-A】执行失败自愈出的 SQL，必须重新过只读/权限/口径校验")
    patch = {}
    _set = _stubs(patch)
    calls = {"llm": 0, "exec": 0, "check": 0, "sql": []}

    def fake_llm(sp, u):
        calls["llm"] += 1
        # 第一次：合法 SQL；之后：自愈成未授权表
        return ("SELECT count(*) FROM ods_valet_order_all"
                if calls["llm"] == 1 else "SELECT * FROM secret_table")

    def fake_check(sql, allowed, strict=None):
        calls["check"] += 1
        if "secret_table" in sql:
            return {"ok": False, "illegal": ["secret_table"], "status": "parsed",
                    "method": "stub", "reason": "",
                    "message": "SQL 引用了未授权的表：['secret_table']"}
        return {"ok": True, "illegal": [], "status": "parsed",
                "method": "stub", "reason": "", "message": ""}

    def fake_exec(sql):
        calls["exec"] += 1
        calls["sql"].append(sql)
        if calls["exec"] == 1:
            raise RuntimeError("simulated execution error")
        return (["c"], [[1]])

    _set(llm, "chat", fake_llm)
    _set(guard, "check_tables_ex", fake_check)
    _set(db, "execute_sql", fake_exec)
    try:
        r = agent.ask_v2(_fake_index(), "查询代驾完单量")
    finally:
        _restore(patch)

    check("⑧ 自愈 SQL 也过了权限校验（check 调用次数 ≥2）", calls["check"] >= 2, True)
    check("⑧ 未授权 SQL 没有真正执行（exec 次数）", calls["exec"], 1)
    check("⑧ 返回 ok=False", r.get("ok"), False)
    check("⑧ 错误信息说明未授权", "未授权" in (r.get("error") or ""), True)
    check("⑧ validation_status=blocked", r.get("validation_status"), "blocked")


# ═══════════════════════════════════════════════════════════
# ⑨ S1 回归：表名解析不可信 → fail-closed
# ═══════════════════════════════════════════════════════════
def test_table_parse_fail_closed():
    print("\n【S1-B】解析器不可用/抛错 → 不得把空集合当成「没有越权表」")
    allowed = ["ods_valet_order_all"]
    orig_has = guard._HAS_SQLGLOT
    orig_fn = guard._extract_by_parser

    try:
        # ① 解析器不可用 + 反引号标识符（正则会漏）
        guard._HAS_SQLGLOT = False
        r1 = guard.check_tables_ex("SELECT * FROM `secret_table`", allowed, strict=True)
        check("⑨ sqlglot 缺失 + 反引号 → 拒绝", r1["ok"], False)
        check("⑨ 状态为 failed", r1["status"], "failed")

        # ② 解析器抛错 + 反引号
        guard._HAS_SQLGLOT = True
        guard._extract_by_parser = lambda sql, dialect: (_ for _ in ()).throw(
            ValueError("simulated parser failure"))
        r2 = guard.check_tables_ex("SELECT * FROM `secret_table`", allowed, strict=True)
        check("⑨ 解析抛错 + 反引号 → 拒绝", r2["ok"], False)
        check("⑨ 状态为 failed", r2["status"], "failed")

        # ③ 解析成功时正常放行（防止一刀切）
        guard._extract_by_parser = orig_fn
        r3 = guard.check_tables_ex(
            "SELECT count(*) FROM ods_valet_order_all", allowed, strict=True)
        check("⑨ 正常 SQL → 放行", r3["ok"], True)
        check("⑨ 状态为 parsed", r3["status"], "parsed")

        # ④ 正常 SQL 引用未授权表 → 仍按 illegal 拦截
        r4 = guard.check_tables_ex("SELECT * FROM secret_table", allowed, strict=True)
        check("⑨ 正常解析的越权表 → 拒绝", r4["ok"], False)
        check("⑨ 越权表名正确", r4["illegal"], ["secret_table"])
    finally:
        guard._HAS_SQLGLOT = orig_has
        guard._extract_by_parser = orig_fn


# ═══════════════════════════════════════════════════════════
# ⑩ S1 回归：校验器状态化 + 前导注释不再跳过
# ═══════════════════════════════════════════════════════════
def test_validator_status_and_comment():
    print("\n【S1-C】校验器必须区分 passed/failed/error/degraded；前导注释不得跳过校验")

    class _Ctx:
        p1_metrics = []
        p2_relationships = []
        p3_table_objs = []

    allowed = ["ods_valet_order_all"]

    # ① 前导行注释 + 未授权表 → 必须报 table_exist
    r = sql_validator.validate_sql_ex(
        "-- 正常取数\nSELECT * FROM `secret_table`", _Ctx(), {"bindings": {}}, allowed)
    check("⑩ 前导注释不再返回空报告", r["status"], "failed")
    check("⑩ 报出未授权表", any(i["rule"] == "table_exist" for i in r["issues"]), True)

    # ② 空 SQL → failed
    r = sql_validator.validate_sql_ex("   ", _Ctx(), {"bindings": {}}, allowed)
    check("⑩ 空 SQL → failed", r["status"], "failed")

    # ③ 校验器自身抛异常 → status=error，且产生 error 级 issue（不得静默通过）
    orig_p0 = sql_validator._check_p0

    def boom(sql, issues):
        raise RuntimeError("simulated checker crash")

    sql_validator._check_p0 = boom
    try:
        r = sql_validator.validate_sql_ex(
            "SELECT count(*) FROM ods_valet_order_all", _Ctx(), {"bindings": {}}, allowed)
    finally:
        sql_validator._check_p0 = orig_p0
    check("⑩ 校验器崩溃 → status=error", r["status"], "error")
    check("⑩ 崩溃被记录为 checker_error",
          any(i["rule"] == "checker_error" for i in r["issues"]), True)
    check("⑩ 崩溃被当作 error 级（会阻断执行）",
          sql_validator._has_error(r["issues"]), True)

    # ④ 子查询别名 + 引号中文别名：不得误报「裸中文别名」
    sql_ok = ('SELECT t.id FROM (SELECT id FROM ods_valet_order_all) AS t '
              'GROUP BY "信息费默认比例"')
    r = sql_validator.validate_sql_ex(sql_ok, _Ctx(), {"bindings": {}}, allowed)
    check("⑩ 子查询别名不误报裸中文",
          any(i["rule"] == "alias_backtick" for i in r["issues"]), False)

    # ⑤ 真正的裸中文别名仍要报错
    r = sql_validator.validate_sql_ex(
        "SELECT count(*) AS 中文别名 FROM ods_valet_order_all",
        _Ctx(), {"bindings": {}}, allowed)
    check("⑩ 裸中文别名仍被拦截",
          any(i["rule"] == "alias_backtick" for i in r["issues"]), True)


# ═══════════════════════════════════════════════════════════
# ⑪ S2：生产环境禁止 always 降级
# ═══════════════════════════════════════════════════════════
def test_production_blocks_always_fallback():
    print("\n【S2-A】生产环境 LEGACY_FALLBACK_MODE=always 不得回退弱校验路径")
    orig_v2, orig_legacy = agent.ask_v2, agent.ask_legacy
    orig_mode = getattr(config, "LEGACY_FALLBACK_MODE", None)
    orig_env = getattr(config, "APP_ENV", None)
    calls = {"legacy": 0}

    def boom(index, q):
        raise ValueError("业务逻辑错误（不可恢复）")

    def fake_legacy(index, q):
        calls["legacy"] += 1
        return {"ok": True, "pipeline": "legacy"}

    agent.ask_v2, agent.ask_legacy = boom, fake_legacy
    try:
        config.APP_ENV = "production"
        config.LEGACY_FALLBACK_MODE = "always"
        r = agent.ask({}, "q")
        check("⑪ 生产 + always + 业务异常 → 不回退 legacy", calls["legacy"], 0)
        check("⑪ 返回 ok=False", r.get("ok"), False)

        # 对照组：开发环境保留 always 的便利（避免误伤本地调试）
        config.APP_ENV = "dev"
        r2 = agent.ask({}, "q")
        check("⑪ 开发 + always → 允许回退（对照）", calls["legacy"], 1)
        check("⑪ 开发回退带 degrade_kind=forced", r2.get("degrade_kind"), "forced")
    finally:
        agent.ask_v2, agent.ask_legacy = orig_v2, orig_legacy
        if orig_mode is not None:
            config.LEGACY_FALLBACK_MODE = orig_mode
        if orig_env is not None:
            config.APP_ENV = orig_env


# ═══════════════════════════════════════════════════════════
# ⑫ S2：/api/status 不对普通用户暴露表清单
# ═══════════════════════════════════════════════════════════
def test_status_hides_tables_for_non_admin():
    print("\n【S2-B】/api/status：普通用户只给数量，不给表清单")
    import server
    idx = {"tables": [1, 2], "metrics": [1], "dicts": [],
           "allowed_tables": ["secret_a", "secret_b"]}

    u = server._status_payload(idx, {"role": "user"})
    check("⑫ 普通用户无 allowed_tables 字段", "allowed_tables" in u, False)
    check("⑫ 普通用户仍能看到表数量", u.get("allowed_table_count"), 2)

    a = server._status_payload(idx, {"role": "admin"})
    check("⑫ 管理员可见完整清单", a.get("allowed_tables"), ["secret_a", "secret_b"])

    n = server._status_payload(idx, None)
    check("⑫ 未登录（防御性）也不返回清单", "allowed_tables" in n, False)


# ═══════════════════════════════════════════════════════════
# ⑬ S2：高危 SQL 子句
# ═══════════════════════════════════════════════════════════
def test_sql_guard_dangerous_clauses():
    print("\n【S2-C】INTO OUTFILE / SETTINGS / SYSTEM 管理命令必须拦截")
    bad = [
        ("导出文件", "SELECT * FROM ods_valet_order_all INTO OUTFILE '/tmp/x'"),
        ("SETTINGS 覆盖参数", "SELECT * FROM ods_valet_order_all SETTINGS max_threads=1"),
        ("SYSTEM 管理命令", "SELECT 1; SYSTEM FLUSH LOGS"),
        ("OPTIMIZE TABLE", "SELECT 1; OPTIMIZE TABLE ods_valet_order_all"),
    ]
    for label, sql in bad:
        ok, why = guard.sql_guard(sql)
        check(f"⑬ {label} → 拦截", ok, False)

    ok, why = guard.sql_guard("SELECT count(*) FROM ods_valet_order_all")
    check("⑬ 正常查询 → 放行", ok, True)


# ═══════════════════════════════════════════════════════════
# ⑭ S2：ACL 默认策略按环境区分
# ═══════════════════════════════════════════════════════════
def test_acl_default_by_env():
    print("\n【S2-D】ACL 默认：生产默认拒绝，开发默认可见")
    check("⑭ 生产默认白名单（False）", config._default_acl_visible("production"), False)
    check("⑭ 开发默认黑名单（True）", config._default_acl_visible("dev"), True)
    check("⑭ 测试默认黑名单（True）", config._default_acl_visible("test"), True)


# ═══════════════════════════════════════════════════════════
# ⑮ S2：默认管理员与自助改密
# ═══════════════════════════════════════════════════════════
def test_password_change_flow():
    print("\n【S2-E】初始账号必须标记改密；自助改密需校验原密码并清除标记")
    check("⑮ DEFAULT_ADMIN 不再硬编码 password 字段",
          "password" in config_store.DEFAULT_ADMIN, False)

    pwd, generated = config_store._initial_admin_password()
    check("⑮ 初始密码不为空", bool(pwd), True)
    if not os.getenv("ADMIN_INITIAL_PASSWORD"):
        check("⑮ 未配置环境变量时随机生成", generated, True)
        check("⑮ 随机密码不少于 8 位", len(pwd) >= 8, True)

    # ★ 用临时库跑完整改密流程，绝不碰生产账号
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    code = (
        "import config_store as cs\n"
        "cs.create_user('u_test', 'oldpass1')\n"
        "print('FLAG1', cs.get_user('u_test')['must_change_password'])\n"
        "cs.change_own_password('u_test', 'oldpass1', 'newpass1')\n"
        "print('FLAG2', cs.get_user('u_test')['must_change_password'])\n"
        "print('NEW_OK', bool(cs.verify_user('u_test', 'newpass1')))\n"
        "print('OLD_OK', bool(cs.verify_user('u_test', 'oldpass1')))\n"
        "try:\n"
        "    cs.change_own_password('u_test', 'wrong', 'another1')\n"
        "    print('WRONG', 'no-error')\n"
        "except ValueError:\n"
        "    print('WRONG', 'error')\n"
        "# 模拟老库：管理员仍用公开默认口令 admin123，应被迁移标记\n"
        "cs.create_user('admin2', 'admin123', 'admin')\n"
        "conn = cs._connect()\n"
        "conn.execute(\"UPDATE users SET must_change_password=0 WHERE username='admin2'\")\n"
        "conn.commit(); conn.close()\n"
        "print('FLAG_LEGACY', cs.flag_default_password_admins() >= 1)\n"
        "print('ADMIN_FLAG', cs.get_user('admin2')['must_change_password'])\n"
    )
    try:
        env = {**os.environ, "CONFIG_DB": path}
        r = subprocess.run(
            [sys.executable, "-c", code], env=env,
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.abspath(__file__)), timeout=60)
        out = (r.stdout or "") + (r.stderr or "")
        check("⑮ 新账号初始 must_change=True", "FLAG1 True" in out, True)
        check("⑮ 改密后 must_change=False", "FLAG2 False" in out, True)
        check("⑮ 新密码可登录", "NEW_OK True" in out, True)
        check("⑮ 旧密码失效", "OLD_OK False" in out, True)
        check("⑮ 原密码错误 → 拒绝改密", "WRONG error" in out, True)
        check("⑮ 老库 admin123 → 自动标记改密", "FLAG_LEGACY True" in out, True)
        check("⑮ 被标记管理员 must_change=True", "ADMIN_FLAG True" in out, True)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════
# ⑯ S3：实体主表必须并入 P3（司机问题固定回归）
# ═══════════════════════════════════════════════════════════
def test_entity_main_table_merged():
    print("\n【S3】P2 命中实体后，其主表必须进入 P3 与最终上下文")

    ent_raw = {"name": "司机信息表", "table_name": "ods_tu_driver_all",
               "grain": "司机", "comment": "司机主表",
               "keywords": ["司机", "司机信息表"], "enabled": True}
    ent_item = {"name": "司机信息表", "keywords": ["司机", "司机信息表"],
                "doc": "司机信息表（司机）：主表 ods_tu_driver_all",
                "raw": ent_raw}
    driver_tbl = {
        "name": "ods_tu_driver_all", "keywords": ["ods_tu_driver_all"],
        "doc": "ods_tu_driver_all：司机明细表",
        "columns": "- default_information_fee_ratio (Decimal) 信息费默认比例\n"
                   "- activation_reduce_ratio (Decimal) 信息费降低比例",
        "column_list": ["default_information_fee_ratio", "activation_reduce_ratio"],
    }
    noise_tbl = {"name": "ods_valet_fee_detail_all", "keywords": ["x"],
                 "doc": "代驾费用", "columns": "- service_fee (Decimal)",
                 "column_list": ["service_fee"]}
    idx = {
        "model": object(),
        "entities": [ent_item], "entity_vectors": [[0.0]],
        "metrics": [], "metric_vectors": [],
        "relationships": [],
        "tables": [driver_tbl, noise_tbl], "table_vectors": [[0.0], [0.0]],
        "docs": [], "doc_vectors": [],
    }

    # ① 数据契约：两种形态都能取到同一张主表
    check("⑯ 裸实体取主表", semantic_layer.entity_table_name(ent_raw), "ods_tu_driver_all")
    check("⑯ 包装实体取主表", semantic_layer.entity_table_name(ent_item), "ods_tu_driver_all")

    # ② 检索链路：P3 只召回噪声表，实体主表靠合并进入
    orig_hr = rag._hybrid_retrieve
    orig_named = rag.named_tables
    orig_prompt = semantic_layer.config_store.get_prompt

    def fake_hr(question, items, vectors, model, threshold, top_k, query_prefix=None):
        if items and items[0].get("name") == "司机信息表":
            return ["司机信息表"], [], "stub-entity"
        return ["ods_valet_fee_detail_all"], [], "stub-table"

    rag._hybrid_retrieve = fake_hr
    rag.named_tables = lambda q, tables: ([], {})
    semantic_layer.config_store.get_prompt = lambda key: ""
    try:
        ctx = semantic_layer.retrieve_semantic(
            idx, "司机的信息费默认比例、降低比例有几种搭配",
            {"metrics": [], "entities": [], "dimensions": []})
    finally:
        rag._hybrid_retrieve = orig_hr
        rag.named_tables = orig_named
        semantic_layer.config_store.get_prompt = orig_prompt

    check("⑯ P2 命中司机信息表", [e.get("name") for e in ctx.p2_entities], ["司机信息表"])
    check("⑯ 司机表进入 P3", "ods_tu_driver_all" in ctx.p3_tables, True)
    objs = [t for t in ctx.p3_table_objs if t["name"] == "ods_tu_driver_all"]
    check("⑯ 司机表字段对象已注入", len(objs), 1)
    if objs:
        check("⑯ 比例字段进入上下文",
              "default_information_fee_ratio" in objs[0]["columns"], True)


# ═══════════════════════════════════════════════════════════
# ⑰ S4：问题类型 → 指标类型降噪
# ═══════════════════════════════════════════════════════════
def test_metric_intent_filter():
    print("\n【S4】问比例时不召回金额指标；用户点名/计数指标保留")

    metrics = [
        {"name": "大板信息费", "metric_type": None},
        {"name": "代驾信息费", "metric_type": None},
        {"name": "司机补缴信息费次数", "metric_type": "count"},
        {"name": "完单率", "metric_type": None},
    ]

    # ① 分类：问“比例”不应因为含“费”而被判成金额问题
    check("⑰ 信息费比例 → ratio", semantic_layer.question_measure_kinds("信息费比例"), {"ratio"})
    check("⑰ 信息费金额 → amount",
          semantic_layer.question_measure_kinds("信息费金额"), {"amount"})

    # ② 比例问题：金额指标被丢，计数/比率指标保留
    kept, dropped = semantic_layer.filter_metrics_for_question(
        "司机的信息费默认比例、降低比例有几种搭配", metrics)
    check("⑰ 比例问题丢弃金额指标", sorted(dropped), ["代驾信息费", "大板信息费"])
    check("⑰ 比例问题保留计数指标",
          any(m["name"] == "司机补缴信息费次数" for m in kept), True)
    check("⑰ 比例问题保留比率指标",
          any(m["name"] == "完单率" for m in kept), True)

    # ③ 金额问题：纯比率指标被丢
    kept, dropped = semantic_layer.filter_metrics_for_question(
        "查询大板信息费金额", metrics)
    check("⑰ 金额问题丢弃比率指标", "完单率" in dropped, True)
    check("⑰ 金额问题保留金额指标",
          any(m["name"] == "大板信息费" for m in kept), True)

    # ④ 用户点名 → 即使类型不匹配也保留
    kept, _ = semantic_layer.filter_metrics_for_question(
        "大板信息费比例是多少", metrics)
    check("⑰ 点名指标不被过滤",
          any(m["name"] == "大板信息费" for m in kept), True)

    # ⑤ 无度量信号 → 不过滤
    kept, dropped = semantic_layer.filter_metrics_for_question(
        "查询代驾订单明细", metrics)
    check("⑰ 无度量信号不过滤", len(kept), len(metrics))
    check("⑰ 无度量信号不丢弃", dropped, [])

    # ⑤-b 问“总数” → 金额指标不参与
    kept, dropped = semantic_layer.filter_metrics_for_question(
        "查询代驾订单总数",
        [{"name": "代驾信息费", "metric_type": "amount"},
         {"name": "代驾订单量", "metric_type": "count"}])
    check("⑰ 总数问题丢弃金额指标", dropped, ["代驾信息费"])
    check("⑰ 总数问题保留计数指标", [m["name"] for m in kept], ["代驾订单量"])

    # ⑥ 已识别实体时，按业务线表交集过滤
    line_metrics = [
        {"name": "大板信息费", "tables": ["ods_tu_large_handcart_order_fee_detail_all"],
         "metric_type": "amount"},
        {"name": "司机补缴信息费次数",
         "tables": ["ods_offline_order_driver_location_statistical_all", "ods_tu_pay_log_all"],
         "metric_type": "count"},
        {"name": "完单率", "tables": ["dwd_tu_order_all"], "metric_type": "ratio"},
        {"name": "大板完单率", "tables": ["ods_tu_large_handcart_order_all"],
         "metric_type": "ratio"},
    ]
    kept, dropped = semantic_layer.filter_metrics_by_entity(
        line_metrics, {"ods_tu_driver_all"}, "司机的信息费比例有几种搭配")
    check("⑰ 实体过滤保留通用比率", [m["name"] for m in kept], ["完单率"])
    check("⑰ 实体过滤丢弃跨业务线指标", set(dropped),
          {"大板信息费", "大板完单率", "司机补缴信息费次数"})

    kept, dropped = semantic_layer.filter_metrics_by_entity(
        line_metrics, {"ods_valet_order_all"}, "代驾完单率")
    check("⑰ 代驾线保留代驾指标（此处无代驾指标则全丢大板）",
          [m["name"] for m in kept], ["完单率"])
    check("⑰ 代驾线丢弃大板专属指标", "大板完单率" in dropped, True)

    # ⑦ 配置分布类问题：不套默认时间窗
    check("⑰ 比例搭配问题 → 不加时间过滤",
          agent.needs_no_time_filter("司机的信息费默认比例、提升比例有几种搭配"), True)
    check("⑰ 明确月份 → 保留时间条件",
          agent.needs_no_time_filter("查询2025年9月信息费比例有几种搭配"), False)
    check("⑰ 普通金额问题 → 不受该规则影响",
          agent.needs_no_time_filter("查询2025年9月大板信息费总额"), False)


# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print("╔" + "═" * 60 + "╗")
    print("║" + "正确性闸门专项测试（P0-2 / P0-3 / P1-1 / 拒答 / 执行出口）".center(52) + "║")
    print("╚" + "═" * 60 + "╝")

    test_validator_gate_blocks_execution()
    test_fail_closed_on_unrecoverable()
    test_recoverable_falls_back()
    test_metric_hard_constraints()
    test_refusal_recognition()
    test_refusal_blocks_execution()
    test_single_execution_chokepoint()
    test_gate_checklist()
    test_repair_must_revalidate()
    test_table_parse_fail_closed()
    test_validator_status_and_comment()
    test_production_blocks_always_fallback()
    test_status_hides_tables_for_non_admin()
    test_sql_guard_dangerous_clauses()
    test_acl_default_by_env()
    test_password_change_flow()
    test_entity_main_table_merged()
    test_metric_intent_filter()

    print()
    print("=" * 60)
    total = len(PASSED) + len(FAILED)
    if not FAILED:
        print(f"🎉 全部通过（{len(PASSED)}/{total}）")
    else:
        print(f"⚠️ 通过 {len(PASSED)}/{total}，失败项：")
        for f in FAILED:
            print(f"   ❌ {f}")
    print("=" * 60)
    # ★ 失败必须让进程返回非 0 —— 否则 CI/上层 runner 会把失败的测试当成功（曾经就是这样漏掉的）
    raise SystemExit(1 if FAILED else 0)
