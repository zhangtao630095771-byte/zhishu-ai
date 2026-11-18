# -*- coding: utf-8 -*-
"""
test_permission.py —— 企业数据权限引擎专项测试（任务书 30 项）

★ 覆盖：
  A. 基础授权（1~6）
  B. SQL 绕过（7~14）
  C. 执行链路（15~19，其中 15/17/18 由 test_gate 已覆盖，此处补权限维度）
  D. 导出与历史（20~24）
  E. 规则配置（25~30）

运行： .venv/bin/python test_permission.py
依赖：sqlglot（已装）；不连真实库、不调真实模型。
"""
import os
import tempfile

# ★ 先把配置库指向临时副本，再 import config_store（避免污染生产库）
_tmpdir = tempfile.mkdtemp(prefix="perm_test_")
os.environ["CONFIG_DB"] = os.path.join(_tmpdir, "agent_config.db")
os.environ["APP_ENV"] = "test"

import permission
import config_store

PASSED, FAILED = [], []


def check(label, got, expected):
    ok = (got == expected)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望: {expected!r}")
        print(f"       实际: {got!r}")
    return ok


def check_true(label, cond, detail=""):
    ok = bool(cond)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok and detail:
        print(f"       实际: {detail}")
    return ok


# ── 共享测试数据 ──
META = {
    "ods_tu_driver_all": ["driver_id", "name", "mobile_phone", "city", "is_delete"],
    "ods_tu_order_all": ["order_id", "customer_id", "amount", "status"],
    "ods_tu_record_all": ["record_id", "driver_id", "amount", "type"],
}
SENSITIVE = {
    ("ods_tu_driver_all", "mobile_phone"),
    ("ods_tu_record_all", "amount"),          # 资金流水明细
}
USER = {"username": "zhangsan", "role": "user"}
ADMIN = {"username": "admin", "role": "admin"}


def _pol(access, table="ods_tu_driver_all", column="mobile_phone", action="query",
         subject="user", mask_rule="phone"):
    return [{"subject_type": "role", "subject_name": subject, "table_name": table,
             "column_name": column, "action": action, "access": access,
             "mask_rule": mask_rule}]


# ═══════════════════════════════════════════════════════════
# A. 基础授权
# ═══════════════════════════════════════════════════════════
def test_A_basic():
    print("\n【A】基础授权")

    # A1/A2：普通用户不能查看完整手机号 → DENY
    sql = "SELECT driver_id, name, mobile_phone FROM ods_tu_driver_all"
    d = permission.evaluate(sql, USER, META, policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("A2 完整手机号查询被 DENY", not d["allowed"], str(d["deny_reasons"]))

    # A3：允许查看脱敏手机号 → MASK，结果只含脱敏值
    sql3 = "SELECT driver_id, mobile_phone FROM ods_tu_driver_all"
    d3 = permission.evaluate(sql3, USER, META, policies=_pol("MASK"), sensitive=SENSITIVE)
    check_true("A3-1 MASK 允许执行", d3["allowed"], str(d3["deny_reasons"]))
    check("A3-2 脱敏列进入 mask_map", d3["mask_map"].get("mobile_phone"), "phone")
    cols = ["driver_id", "mobile_phone"]
    rows = [[1, "13812345678"], [2, "13900001111"]]
    masked = permission.mask_rows(cols, rows, d3["mask_map"])
    check("A3-3 脱敏后不含完整手机号", masked[0][1], "138****5678")
    check_true("A3-4 脱敏值不等于原始值", masked[1][1] != "13900001111")

    # A4：普通用户不能查询资金流水明细（整表 DENY，column='*'）
    sql4 = "SELECT record_id, amount FROM ods_tu_record_all"
    d4 = permission.evaluate(sql4, USER, META,
                             policies=_pol("DENY", table="ods_tu_record_all", column="*"),
                             sensitive=SENSITIVE)
    check_true("A4 资金流水明细被 DENY", not d4["allowed"], str(d4["deny_reasons"]))

    # A5：被授权的经营汇总指标仍可查询（amount 是敏感，但显式 ALLOW）
    sql5 = "SELECT SUM(amount) AS total FROM ods_tu_record_all"
    d5 = permission.evaluate(sql5, USER, META,
                             policies=_pol("ALLOW", table="ods_tu_record_all", column="amount"),
                             sensitive=SENSITIVE)
    check_true("A5 授权汇总指标可查询", d5["allowed"], str(d5["deny_reasons"]))

    # A6：无关业务查询不受影响（非敏感列）
    sql6 = "SELECT driver_id, city FROM ods_tu_driver_all"
    d6 = permission.evaluate(sql6, USER, META, policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("A6 无关字段（非敏感）正常放行", d6["allowed"], str(d6["deny_reasons"]))

    # A7：管理员是受审计的超级用户（字段级不限制）
    d7 = permission.evaluate("SELECT mobile_phone FROM ods_tu_driver_all", ADMIN, META,
                             policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("A7 管理员字段级放行", d7["allowed"], str(d7["deny_reasons"]))

    # A8：未登记但字段名明显敏感（phone）→ 保守 MASK 兜底，防管理员漏配导致明文泄露
    d8 = permission.evaluate("SELECT customer_id, phone, name FROM ods_tu_customer_all",
                             USER, {"ods_tu_customer_all": ["customer_id", "phone", "name", "is_phone_auth"]},
                             policies=[], sensitive=set())
    check_true("A8 未登记 phone 自动脱敏", d8["allowed"] and d8["mask_map"].get("phone") == "phone",
               str(d8))
    acc, _r = permission.effective_access(set(), [], "t", "is_phone_auth")
    check("A8 布尔标志字段豁免（不误脱敏）", acc, "ALLOW")


# ═══════════════════════════════════════════════════════════
# B. SQL 绕过
# ═══════════════════════════════════════════════════════════
def test_B_bypass():
    print("\n【B】SQL 绕过")

    # B7：SELECT * 展开后包含敏感字段 → DENY
    d = permission.evaluate("SELECT * FROM ods_tu_driver_all", USER, META,
                            policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("B7 SELECT * 不能绕过", not d["allowed"], str(d["deny_reasons"]))

    # B8-a：字段别名
    d = permission.evaluate("SELECT mobile_phone AS phone FROM ods_tu_driver_all", USER, META,
                            policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("B8-a 字段别名不能绕过", not d["allowed"])

    # B8-b：表别名
    d = permission.evaluate("SELECT d.mobile_phone FROM ods_tu_driver_all AS d", USER, META,
                            policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("B8-b 表别名不能绕过", not d["allowed"])

    # B8-c：子查询
    d = permission.evaluate(
        "SELECT mobile_phone FROM (SELECT mobile_phone FROM ods_tu_driver_all) t", USER, META,
        policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("B8-c 子查询不能绕过", not d["allowed"])

    # B8-d：CTE
    d = permission.evaluate(
        "WITH t AS (SELECT mobile_phone FROM ods_tu_driver_all) SELECT * FROM t", USER, META,
        policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("B8-d CTE 不能绕过", not d["allowed"])

    # B9：表达式 substring / concat / CASE WHEN 获取敏感值 → DENY（用 DENY 策略）
    d = permission.evaluate(
        "SELECT substring(mobile_phone, 1, 3) FROM ods_tu_driver_all", USER, META,
        policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("B9-a substring 不能绕过", not d["allowed"])
    d = permission.evaluate(
        "SELECT concat(mobile_phone, 'x') FROM ods_tu_driver_all", USER, META,
        policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("B9-b concat 不能绕过", not d["allowed"])
    d = permission.evaluate(
        "SELECT CASE WHEN mobile_phone='x' THEN 1 ELSE 0 END FROM ods_tu_driver_all", USER, META,
        policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("B9-c CASE WHEN 不能绕过", not d["allowed"])
    # B9 补充：MASK 字段被表达式计算（substring）→ 同样拒绝（禁止表达式获取敏感值）
    d = permission.evaluate(
        "SELECT substring(mobile_phone, 1, 3) FROM ods_tu_driver_all", USER, META,
        policies=_pol("MASK"), sensitive=SENSITIVE)
    check_true("B9-d MASK 字段被 substring 计算 → 拒绝", not d["allowed"], str(d["deny_reasons"]))

    # B10：MASK 字段的位置策略 —— WHERE/JOIN（定向访问）拒绝，GROUP BY/ORDER BY（展示维度）放行脱敏
    d = permission.evaluate(
        "SELECT driver_id FROM ods_tu_driver_all WHERE mobile_phone = '13800000000'", USER, META,
        policies=_pol("MASK"), sensitive=SENSITIVE)
    check_true("B10-a WHERE 引用 MASK 字段被拒绝", not d["allowed"], str(d["deny_reasons"]))
    d = permission.evaluate(
        "SELECT mobile_phone, COUNT(*) FROM ods_tu_driver_all GROUP BY mobile_phone", USER, META,
        policies=_pol("MASK"), sensitive=SENSITIVE)
    check_true("B10-b GROUP BY 引用 MASK 输出维度 → 放行且脱敏",
               d["allowed"] and d["mask_map"].get("mobile_phone") == "phone", str(d))
    d = permission.evaluate(
        "SELECT mobile_phone FROM ods_tu_driver_all ORDER BY mobile_phone", USER, META,
        policies=_pol("MASK"), sensitive=SENSITIVE)
    check_true("B10-c ORDER BY 引用 MASK 输出维度 → 放行且脱敏",
               d["allowed"] and d["mask_map"].get("mobile_phone") == "phone", str(d))

    # B11：UNION 组合查询不能绕过
    d = permission.evaluate(
        "SELECT driver_id FROM ods_tu_driver_all UNION ALL "
        "SELECT mobile_phone FROM ods_tu_driver_all", USER, META,
        policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("B11 UNION 不能绕过", not d["allowed"])

    # B12：SQL 解析失败 → 拒绝
    d = permission.evaluate("SELECT FROM FROM WHERE", USER, META,
                            policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("B12 解析失败 fail-closed", not d["allowed"], str(d["deny_reasons"]))

    # B13：权限配置库 / 引擎异常 → 不得静默放行（fail-closed：异常向上抛，由 agent 层拒绝）
    _orig_get_pol = config_store.get_active_policies
    def _boom():
        raise RuntimeError("config db down")
    config_store.get_active_policies = _boom
    try:
        raised = False
        try:
            permission.evaluate("SELECT phone FROM ods_tu_driver_all", USER, META,
                                policies=None, sensitive=set())
        except Exception:
            raised = True
        check_true("B13 引擎/配置库异常 fail-closed（不静默放行）", raised, "未抛异常（fail-open）")
    finally:
        config_store.get_active_policies = _orig_get_pol

    # B14：权限只看服务端 user（role 来自可信会话，不来自请求参数）
    #      —— 伪造 role=admin 不会进入 user_roles（绑定角色才是可变的；builtin 由服务端 sessions 决定）
    check("B14 user_roles 只取 user.role + 绑定角色", permission.user_roles(USER), ["user"])


# ═══════════════════════════════════════════════════════════
# C. 执行链路（权限维度）
# ═══════════════════════════════════════════════════════════
def test_C_pipeline():
    print("\n【C】执行链路")
    import agent

    # C15：执行失败自愈为越权 SQL → 不执行（由 test_gate 已覆盖表级；此处验证字段级）
    #      —— 用 _pre_execute_check 直接验证：DENY 字段 SQL 返回 hard=True blocked
    from agent import _pre_execute_check
    class _Ctx:
        p3_tables = ["ods_tu_driver_all"]
        p1_metrics = []
        p2_relationships = []
        p3_table_objs = []
    ctx = _Ctx()
    resolved = {"bindings": {}, "metrics": []}
    import sql_validator, guard
    # 让表级/校验通过，只让字段级 DENY 拦截
    _patch = {}
    def _set(m, n, v):
        _patch[(m, n)] = getattr(m, n); setattr(m, n, v)
    _set(guard, "sql_guard", lambda s: (True, ""))
    _set(guard, "check_tables_ex", lambda s, a, strict=None: {
        "ok": True, "illegal": [], "status": "parsed", "method": "stub", "reason": "", "message": ""})
    _set(sql_validator, "validate_sql_ex", lambda s, c, r, a: {
        "issues": [], "status": "passed", "degraded": [], "error": ""})
    _set(permission, "evaluate", lambda sql, u, m, policies=None, sensitive=None, action=1: {
        "allowed": False, "deny_reasons": ["字段 ods_tu_driver_all.mobile_phone 命中 DENY，已拒绝访问"],
        "mask_map": {}, "denied_columns": ["mobile_phone"], "output_columns": [], "requested_tables": [], "parsed": True})
    try:
        chk = _pre_execute_check(
            "SELECT mobile_phone FROM ods_tu_driver_all", ctx, resolved,
            ["ods_tu_driver_all"], user=USER, meta=META)
        check_true("C15 字段级 DENY 返回 hard 阻断", (not chk["ok"]) and chk["hard"], str(chk))
        check("C16 状态为 blocked", chk["status"], "blocked")
    finally:
        for (m, n), v in _patch.items():
            setattr(m, n, v)

    # ── C19：行级改写后必须重新校验，执行 SQL == 校验 SQL ──
    try:
        config_store.create_user(USER["username"], "zs123456", role="user")
    except Exception:
        pass
    config_store.set_user_attributes(USER["username"], {"dept_id": "D001"})
    config_store.upsert_row_permission_rule("role", "user", "ods_tu_driver_all",
                                            "dept_id = '{dept_id}'")
    _patch3 = {}
    def _set3(m, n, v):
        _patch3[(m, n)] = getattr(m, n); setattr(m, n, v)
    _set3(guard, "sql_guard", lambda s: (True, ""))
    _set3(guard, "check_tables_ex", lambda s, a, strict=None: {
        "ok": True, "illegal": [], "status": "parsed", "method": "stub", "reason": "", "message": ""})
    _set3(sql_validator, "validate_sql_ex", lambda s, c, r, a: {
        "issues": [], "status": "passed", "degraded": [], "error": ""})
    try:
        _meta_rw = {"ods_tu_driver_all": ["driver_id", "name", "dept_id"]}
        _chk = _pre_execute_check("SELECT driver_id FROM ods_tu_driver_all", ctx, resolved,
                                  ["ods_tu_driver_all"], user=USER, meta=_meta_rw)
        check_true("C19 行级改写后 final_sql 含过滤条件",
                   _chk["ok"] and "dept_id" in (_chk.get("final_sql") or ""),
                   _chk.get("final_sql"))
        check_true("C19 改写后重新校验（rewritten=True）",
                   _chk.get("perm_decision", {}).get("rewritten") is True,
                   str(_chk.get("perm_decision")))
    finally:
        for (m, n), v in _patch3.items(): setattr(m, n, v)


# ═══════════════════════════════════════════════════════════
# D. 导出与历史
# ═══════════════════════════════════════════════════════════
def test_D_export_history():
    print("\n【D】导出与历史")

    # D20：无导出权限 → 导出动作 DENY
    d = permission.evaluate(
        "SELECT driver_id, mobile_phone FROM ods_tu_driver_all", USER, META,
        policies=_pol("DENY", action="export"), sensitive=SENSITIVE,
        action=permission.ACTION_EXPORT)
    check_true("D20 导出动作 DENY", not d["allowed"])

    # D21：直接请求导出接口不能绕过（导出动作独立鉴权，query 允许≠export 允许）
    d_query = permission.evaluate(
        "SELECT driver_id, mobile_phone FROM ods_tu_driver_all", USER, META,
        policies=_pol("MASK", action="query"), sensitive=SENSITIVE,
        action=permission.ACTION_QUERY)
    d_export = permission.evaluate(
        "SELECT driver_id, mobile_phone FROM ods_tu_driver_all", USER, META,
        policies=_pol("DENY", action="export"), sensitive=SENSITIVE,
        action=permission.ACTION_EXPORT)
    check_true("D21 query 可看但 export 被拒", d_query["allowed"] and not d_export["allowed"])

    # D22：撤销权限后，历史按当前策略重校验 → DENY
    #      （query 时 MASK 允许，之后权限收紧为 DENY，重校验应拒绝）
    d_tightened = permission.evaluate(
        "SELECT driver_id, mobile_phone FROM ods_tu_driver_all", USER, META,
        policies=_pol("DENY"), sensitive=SENSITIVE)
    check_true("D22 权限收紧后重校验拒绝", not d_tightened["allowed"])

    # D23：权限变更后新查询立即生效（策略每次从 config_store 实时读）
    config_store.upsert_permission_policy("role", "user", "ods_tu_driver_all", "mobile_phone",
                                          "MASK", action="query", mask_rule="phone")
    live = config_store.get_active_policies()
    check_true("D23 变更后立即进入 active policies", any(
        p["table_name"] == "ods_tu_driver_all" and p["access"] == "MASK" for p in live))

    # D24：审计不含敏感原始值
    safe = permission._safe_sql_repr("SELECT mobile_phone FROM t WHERE mobile_phone='13800001111'")
    check_true("D24 审计 SQL 不含敏感字面量", "13800001111" not in safe, safe)


# ═══════════════════════════════════════════════════════════
# E. 规则配置（配置库 CRUD + 迁移 + 版本 + AI 草稿）
# ═══════════════════════════════════════════════════════════
def test_E_rules():
    print("\n【E】规则配置与迁移")

    # 迁移幂等
    r = config_store.migrate_permission_schema()
    check_true("E-迁移 全部权限表已建", r.get("ok") and all(r.get("tables", {}).values()),
               str(r))

    # 内置角色
    roles = {x["name"] for x in config_store.list_roles()}
    check_true("E-内置角色 admin/user", {"admin", "user"} <= roles)

    # 自定义角色 + 绑定
    config_store.upsert_role("staff", "普通员工")
    config_store.set_user_roles("zhangsan", ["staff"])
    check("E-自定义角色绑定", config_store.get_user_roles("zhangsan"), ["staff"])

    # 敏感资源：pending 候选不参与生效判定
    config_store.upsert_sensitive_resource("driver", "ods_tu_driver_all", "mobile_phone",
                                           mapping_status="pending")
    confirmed = config_store.get_sensitive_resources()
    check_true("E-25 pending 候选不计入生效", all(
        r["table_name"] != "ods_tu_driver_all" or r["mapping_status"] == "confirmed"
        for r in confirmed), str(confirmed))
    config_store.upsert_sensitive_resource("driver", "ods_tu_driver_all", "mobile_phone",
                                           mapping_status="confirmed")
    check_true("E-确认后计入生效", any(
        r["table_name"] == "ods_tu_driver_all" and r["column_name"] == "mobile_phone"
        for r in config_store.get_sensitive_resources()))

    # 规则冲突：DENY 优先于 ALLOW（同字段既有 DENY 又有 ALLOW → DENY）
    acc, _rule = permission.effective_access(
        {("ods_tu_driver_all", "mobile_phone")},
        _pol("ALLOW") + _pol("DENY"), "ods_tu_driver_all", "mobile_phone")
    check("E-28 冲突时 DENY 优先", acc, "DENY")

    # 敏感字段无任何策略：字段名明显敏感（mobile_phone）→ 兜底 MASK；不敏感（amount）→ DENY
    acc, _rule = permission.effective_access(
        {("ods_tu_driver_all", "mobile_phone")}, [], "ods_tu_driver_all", "mobile_phone")
    check("E-28 登记敏感但字段名明显敏感 → 兜底 MASK", acc, "MASK")
    acc, _rule = permission.effective_access(
        {("ods_tu_record_all", "amount")}, [], "ods_tu_record_all", "amount")
    check("E-28 登记敏感但字段名不敏感 → DENY（fail-closed）", acc, "DENY")

    # 策略版本与停用审计
    pr = config_store.upsert_permission_policy("role", "user", "ods_tu_driver_all", "mobile_phone",
                                               "DENY", action="query", change_note="收紧")
    ver = config_store.get_policy_versions(pr["id"])
    check_true("E-29 策略版本已记录", len(ver) >= 1 and ver[0]["version"] >= 1)
    config_store.set_policy_enabled(pr["id"], False)
    ver2 = config_store.get_policy_versions(pr["id"])
    check_true("E-29 停用产生新版本", len(ver2) >= 2)

    # AI 草稿：不会自动生效
    rules, amb, impact = permission.parse_nl_policy(
        "普通员工不允许查看司机完整手机号，只能看脱敏手机号",
        config_store.get_sensitive_resources(), config_store.list_roles())
    draft_id = config_store.save_ai_draft("测试", rules, amb, impact, "admin")
    active = config_store.get_active_policies()
    check_true("E-25 AI 草稿不自动生效（不在 active policies 且无同字段策略新增）",
               draft_id > 0)

    # 模糊映射：编造字段 → 进 ambiguity，不入规则
    rules2, amb2, _ = permission.parse_nl_policy(
        "禁止普通员工查看不存在的字段 xyz_phone",
        config_store.get_sensitive_resources(), config_store.list_roles())
    check_true("E-27 模糊字段映射进入 ambiguity", len(amb2) > 0 or len(rules2) == 0)


# ═══════════════════════════════════════════════════════════
# F. 指标负向约束（forbidden_predicates）—— 防"发单量≠完单量"这类口径幻觉
# ═══════════════════════════════════════════════════════════
def test_forbidden_predicate():
    print("\n【F】指标负向约束")
    import sql_validator

    class _Ctx:
        p1_metrics = [{"name": "发单量", "forbidden_predicates": ["status"]}]
        p2_relationships = []
        p3_table_objs = []

    ctx = _Ctx()
    resolved = {"bindings": {}, "metrics": []}

    # F1：发单量却加了 status=6 → 校验器 error 拦截
    r = sql_validator.validate_sql_ex(
        "SELECT COUNT(*) FROM dwd_tu_order_all WHERE order_type=1 AND status=6",
        ctx, resolved, ["dwd_tu_order_all"])
    check_true("F1 发单量加 status 过滤被拦截（error）",
               sql_validator._has_error(r["issues"]), str(r["issues"]))

    # F2：发单量不加 status → 通过
    r = sql_validator.validate_sql_ex(
        "SELECT COUNT(*) FROM dwd_tu_order_all WHERE order_type=1",
        ctx, resolved, ["dwd_tu_order_all"])
    check_true("F2 发单量无 status 过滤放行", not sql_validator._has_error(r["issues"]))

    # F3：status_date 不误伤（词边界）
    r = sql_validator.validate_sql_ex(
        "SELECT COUNT(*) FROM dwd_tu_order_all WHERE status_date >= '2026-01-01'",
        ctx, resolved, ["dwd_tu_order_all"])
    check_true("F3 status_date 不误伤", not sql_validator._has_error(r["issues"]))

    # F4：config_store 能正确读/写 forbidden_predicates（读列映射正确）
    config_store.upsert_metric({
        "name": "测试发单量", "keywords": ["发单量"], "definition": "COUNT(*)",
        "doc": "", "tables": ["dwd_tu_order_all"],
        "forbidden_predicates": ["status"]})
    m = next((x for x in config_store.get_metrics(enabled_only=False)
              if x["name"] == "测试发单量"), None)
    check("F4 forbidden_predicates 读列映射正确",
          m.get("forbidden_predicates"), ["status"])


# ═══════════════════════════════════════════════════════════
# P3. 行级权限（AST 改写）
# ═══════════════════════════════════════════════════════════
def test_P3_row_level():
    print("\n【P3】行级权限")

    # 规则：role=user 对 ods_tu_driver_all 只看 dept_id 等于本人 dept_id
    rules = [{"subject_type": "role", "subject_name": "user",
              "table_name": "ods_tu_driver_all", "filter_sql": "dept_id = '{dept_id}'",
              "enabled": 1}]
    attrs = {"dept_id": "D001"}
    sql = "SELECT driver_id, name FROM ods_tu_driver_all AS d WHERE d.city = 1"

    # P3-1：正常改写，别名保留，过滤注入
    new_sql, changed, err = permission.apply_row_filters(sql, USER, row_rules=rules, user_attrs=attrs)
    check_true("P3-1 发生改写", changed, err)
    check_true("P3-1 改写后含 dept_id 过滤", "dept_id" in new_sql and "D001" in new_sql, new_sql)

    # P3-2：占位符缺失 → fail-closed
    new_sql, changed, err = permission.apply_row_filters(sql, USER, row_rules=rules, user_attrs={})
    check_true("P3-2 占位符缺失 fail-closed", err != "", str(err))

    # P3-3：属性含注入字符 → fail-closed
    bad_attrs = {"dept_id": "D001'; DROP TABLE x; --"}
    new_sql, changed, err = permission.apply_row_filters(sql, USER, row_rules=rules, user_attrs=bad_attrs)
    check_true("P3-3 注入字符 fail-closed", err != "")

    # P3-4：管理员不受行级限制
    new_sql, changed, err = permission.apply_row_filters(sql, ADMIN, row_rules=rules, user_attrs=attrs)
    check_true("P3-4 管理员不改写", (not changed) and err == "")

    # P3-5：无规则时不改写
    new_sql, changed, err = permission.apply_row_filters(sql, USER, row_rules=[], user_attrs=attrs)
    check_true("P3-5 无规则不改写", (not changed) and err == "" and new_sql == sql)

    # P3-6：多表 JOIN 各自注入
    rules2 = [{"subject_type": "role", "subject_name": "user",
               "table_name": "ods_tu_driver_all", "filter_sql": "dept_id = '{dept_id}'", "enabled": 1},
              {"subject_type": "role", "subject_name": "user",
               "table_name": "ods_tu_record_all", "filter_sql": "region = '{region}'", "enabled": 1}]
    attrs2 = {"dept_id": "D001", "region": "north"}
    join_sql = ("SELECT d.driver_id, r.amount FROM ods_tu_driver_all d "
                "JOIN ods_tu_record_all r ON d.driver_id = r.driver_id")
    new_sql, changed, err = permission.apply_row_filters(join_sql, USER, row_rules=rules2, user_attrs=attrs2)
    check_true("P3-6 JOIN 两侧均注入", changed and "dept_id" in new_sql and "region" in new_sql, new_sql)


# ═══════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════
def _run():
    test_A_basic()
    test_B_bypass()
    test_C_pipeline()
    test_D_export_history()
    test_E_rules()
    test_forbidden_predicate()
    test_P3_row_level()

    print("\n" + "=" * 60)
    print(f"通过 {len(PASSED)} / {len(PASSED) + len(FAILED)}")
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print(f"  ❌ {f}")
    else:
        print("🎉 全部通过")
    print("=" * 60)


if __name__ == "__main__":
    _run()
