# -*- coding: utf-8 -*-
"""
permission.py —— 企业数据权限引擎（统一、可复用、与 AI 生成逻辑完全解耦）

★ 设计原则（对应任务书"功能三 / 功能四"）：
  1. 权限判断全部由服务端确定性执行，不依赖模型是否理解用户意图。
  2. 传统权限配置是唯一正式生效的权限来源；AI 只能产出"待审核草稿"。
  3. 只做【字段级 AST 判定 + 结果脱敏 + 审计】，不写 SQL、不改 SQL（v1 保守：DENY/MASK 违规即拒绝）。
  4. fail-closed：解析失败、表元数据缺失、策略加载失败 → 拒绝敏感查询，绝不默认放行。

★ 与 guard / sql_validator 的分工：
  guard.sql_guard          → 只读 + 高危子句（语句级）
  guard.check_tables_ex    → 表级白名单（表级）
  sql_validator            → 业务口径 / Schema 存在性（业务级）
  permission.evaluate      → 字段级 ALLOW/MASK/DENY + 行级（本模块，字段级）

★ 为什么不能只靠前面几道？
  表级白名单拦不住"表可见、但字段敏感"（如订单表可见、但手机号字段敏感）。
  必须把检查下沉到字段，并在 AST 层面识别 SELECT *、别名、子查询、表达式引用。
"""
import json
import re
import time

import config
import config_store


# ═══════════════════════════════════════════════════════════
# ① 归一化（表名/字段名：去库前缀、小写、去引号）
# ═══════════════════════════════════════════════════════════
_IDENT_QUOTES = "`\"'[]"


def normalize_table(name):
    """`ctc_data_warehouse.ods_tu_driver_all` / `ods_tu_driver_all` → `ods_tu_driver_all`"""
    if not name:
        return ""
    s = str(name).strip().strip(_IDENT_QUOTES)
    if "." in s:
        s = s.split(".")[-1]
    return s.lower()


def normalize_column(name):
    """`mobile_phone` / `Mobile_Phone` / `` `mobile_phone` `` → `mobile_phone`"""
    if not name:
        return ""
    return str(name).strip().strip(_IDENT_QUOTES).lower()


# ═══════════════════════════════════════════════════════════
# ② 脱敏规则（确定性、服务端执行）
# ═══════════════════════════════════════════════════════════
def mask_value(value, rule="default"):
    """
    对单个值做确定性脱敏。rule 为脱敏规则名。

    ★ 为什么必须是确定性函数？
      同一个手机号无论何时、经哪个接口，脱敏结果必须一致；
      这样"页面看到的"和"导出得到的"不会出现两种不同形态。
    """
    if value is None:
        return None
    s = str(value)
    if rule == "phone" and len(s) >= 7:
        # 手机号：保留前 3 后 4 → 138****1234
        return s[:3] + "****" + s[-4:]
    if rule == "id_card" and len(s) >= 8:
        # 身份证：保留前 4 后 2 → 1101**********12
        return s[:4] + "****" + s[-2:]
    if rule == "bank_card" and len(s) >= 8:
        # 银行卡：只保留后 4 位
        return "****" + s[-4:]
    if rule == "name":
        # 姓名：保留首字 → 张*
        return (s[:1] + "*") if s else s
    # default：全量打码（保留长度感）
    return "***"


# ═══════════════════════════════════════════════════════════
# ③ 生效策略解析（用户 → 合并后的字段级访问策略）
# ═══════════════════════════════════════════════════════════
# 访问动作 → 位组合（一个策略可同时约束 query / export）
ACTION_QUERY = 1
ACTION_EXPORT = 2
ACTION_BOTH = ACTION_QUERY | ACTION_EXPORT


def _action_bit(action):
    a = (action or "query").lower()
    if a == "export":
        return ACTION_EXPORT
    if a == "both":
        return ACTION_BOTH
    return ACTION_QUERY


def user_roles(user):
    """
    用户全部角色：内置角色（users.role）+ 自定义角色绑定。

    ★ 内置角色 admin / user 一定在；绑定表里的自定义角色追加。
      返回去重后的角色名列表。
    """
    roles = []
    if user:
        builtin = user.get("role")
        if builtin:
            roles.append(builtin)
        for r in (config_store.get_user_roles(user.get("username")) or []):
            if r not in roles:
                roles.append(r)
    return roles


# ── 明显敏感字段名兜底（未登记时的保守脱敏，防"管理员漏配"导致明文泄露）──
#   ★ 这不是"自动发布敏感资源"（那仍禁止），而是运行时保守兜底：
#     字段名一看就是手机号/身份证/银行卡，即使敏感资源表里没登记，也默认 MASK，
#     而不是明文放行。符合"未映射的敏感数据访问必须采取安全失败策略"。
#   ★ 豁免：phone_brand/phone_model（手机品牌/型号）等"设备属性"不是手机号本身，不脱敏。
_SENSITIVE_EXEMPT_PREFIXES = ("is_", "has_", "flag_", "type_", "status_",
                              "count_", "num_", "auth_", "verified_", "valid_")


def _hint_mask_rule(column):
    """按字段名推断"明显敏感"时的脱敏规则；不敏感返回 None。"""
    c = (column or "").lower()
    for pre in _SENSITIVE_EXEMPT_PREFIXES:
        if c.startswith(pre):
            return None
    # 手机号类：以 phone/mobile/tel/telephone 结尾（含 phone1/phone_2 这类"手机号+数字"）
    #   —— 用"结尾"而非"包含"，避免 phone_brand/phone_model（设备属性）误伤。
    if re.search(r"(phone|mobile|tel|telephone)[_\d]*$", c):
        # phone_ 开头 + 品牌/型号等设备属性 → 不是手机号
        if re.match(r"^(phone|mobile)_(brand|model|type|version|os|system)", c):
            return None
        return "phone"
    if re.search(r"(idcard|id_card|身份证)$", c):
        return "id_card"
    if re.search(r"(bankcard|bank_card|银行卡)$", c):
        return "bank_card"
    return None


def effective_access(sensitive, policies, table, column, action=ACTION_QUERY):
    """
    计算某个 (table, column) 对"当前主体"的最终访问级别。

    返回 "ALLOW" / "MASK" / "DENY"，以及命中的 mask_rule（MASK 时有效）。

    ★ 判定顺序（最小权限 + 显式拒绝优先）：
      1) 该字段不是登记敏感资源，且字段名不"明显敏感" → 默认 ALLOW；
      2) 字段名明显敏感（phone/mobile/id_card/bank_card 等）但未登记
         → 默认 MASK（保守兜底，防管理员漏配导致明文泄露）；
      3) 是登记敏感资源：
         - 有 DENY 命中 → DENY（显式拒绝恒优先）；
         - 有 MASK 命中 → MASK（最小权限）；
         - 有 ALLOW 命中 → ALLOW；
         - 无任何策略命中 → DENY（敏感字段 fail-closed：未授权即拒绝）。

    ★ 通配符支持：table '*' 作用于所有表；column '*' 作用于整表。
    """
    # 该字段是否被登记为敏感资源
    is_sensitive = sensitive and (
        (table, column) in sensitive or (table, "*") in sensitive)
    if not is_sensitive:
        # ★ 未登记，但字段名明显敏感 → 保守 MASK 兜底（不依赖管理员记得登记）
        hint = _hint_mask_rule(column)
        if hint:
            return "MASK", hint
        return "ALLOW", None

    deny = mask = allow = False
    mask_rule = "default"
    for p in policies:
        pt = normalize_table(p.get("table_name") or "")
        pc = normalize_column(p.get("column_name") or "")
        if pt != "*" and pt != table:
            continue
        if pc != "*" and pc != column:
            continue
        if not (_action_bit(p.get("action")) & action):
            continue
        acc = (p.get("access") or "DENY").upper()
        if acc == "DENY":
            deny = True
        elif acc == "MASK":
            mask = True
            mask_rule = p.get("mask_rule") or "default"
        elif acc == "ALLOW":
            allow = True
    if deny:
        return "DENY", None
    if mask:
        return "MASK", mask_rule
    if allow:
        return "ALLOW", None
    # 敏感字段无任何授权：字段名明显敏感 → 兜底 MASK（如 phone 脱敏展示）；
    # 否则 → DENY（fail-closed，如资金流水 amount 未授权即拒绝）。
    hint = _hint_mask_rule(column)
    if hint:
        return "MASK", hint
    return "DENY", None          # 敏感字段无任何授权 → 拒绝（fail-closed）


# ═══════════════════════════════════════════════════════════
# ④ AST 字段级解析（sqlglot）
# ═══════════════════════════════════════════════════════════
def _sqlglot():
    try:
        import sqlglot
        from sqlglot import exp
        return sqlglot, exp
    except ImportError:
        return None, None


def _meta_map(meta):
    """
    把表元数据规整成 {表名: {列名集合}}，供 SELECT * 展开与字段归属判断。
    meta 可为：dict{表: [列...]} 或 list[{name, column_list}]。
    """
    out = {}
    if isinstance(meta, dict):
        for t, cols in meta.items():
            out[normalize_table(t)] = {normalize_column(c) for c in (cols or [])}
    elif isinstance(meta, list):
        for item in meta or []:
            t = item.get("name") or item.get("table_name")
            cols = item.get("column_list") or item.get("columns") or []
            out[normalize_table(t)] = {normalize_column(c) for c in cols}
    return out


def _resolve_alias_map(tree, exp):
    """表别名 → 真实表名（sqlglot Table.name 已去库前缀）"""
    alias_map = {}
    for t in tree.find_all(exp.Table):
        real = normalize_table(t.name)
        if not real:
            continue
        alias_map[real] = real
        alias = normalize_table(t.alias_or_name)
        if alias and alias != real:
            alias_map[alias] = real
    return alias_map


def _walk_columns(node, exp):
    """收集子树里所有列引用节点（返回 list，供后续分类）"""
    if node is None:
        return []
    return list(node.find_all(exp.Column))


def evaluate(sql, user, meta, policies=None, sensitive=None, action=ACTION_QUERY):
    """
    ★ 核心：字段级权限判定。

    参数：
      sql       —— 待执行的 SQL（已清洗/补前缀）
      user      —— 可信用户（dict，含 username / role）
      meta      —— 表元数据（dict{表:[列]} 或 list），用于 SELECT * 展开
      policies  —— 生效策略列表（测试可注入；None 时从 config_store 读）
      sensitive —— 敏感资源集合（测试可注入；None 时从 config_store 读）
      action    —— query / export（决定用哪类策略）

    返回 PermissionDecision dict：
      allowed       bool    是否允许执行
      deny_reasons  list    拒绝原因（allowed=False 时非空）
      mask_map      dict    {结果列名: 脱敏规则}（仅输出列的 MASK）
      output_columns list   最终输出列名（按序）
      requested_tables list 涉及的归一化表名
      parsed        bool    是否成功解析（False 表示无法可靠判定 → 拒绝）

    ★ fail-closed：
      - 解析失败 / 无法归属的表或字段 → allowed=False；
      - 表元数据无法确认（SELECT * 无法展开）→ allowed=False；
      - 敏感资源集合或策略加载失败 → 对该查询视为"无法判定"→ 拒绝。
    """
    decision = {
        "allowed": True, "deny_reasons": [], "mask_map": {},
        "denied_columns": [], "output_columns": [], "requested_tables": [], "parsed": False,
    }

    if policies is None:
        policies = config_store.get_active_policies()
    if sensitive is None:
        sensitive = _load_sensitive_set()

    # ★ 过滤到当前用户：只保留"该用户角色 / 本人"的策略，避免误用他人策略
    roles = set(user_roles(user)) if user else set()
    uname = (user or {}).get("username")
    policies = [p for p in policies
                if (p.get("subject_type") == "role" and p.get("subject_name") in roles)
                or (p.get("subject_type") == "user" and p.get("subject_name") == uname)]

    # ★ 管理员是受审计的超级用户：字段级不做 ALLOW/MASK/DENY 限制（但仍解析表/列写审计）。
    #   依据：管理员是权限规则的唯一发布者；"管理员的敏感数据访问也应保留审计记录"。
    is_admin = bool(user) and (user or {}).get("role") == "admin"

    sqlglot, exp = _sqlglot()
    if sqlglot is None:
        decision["allowed"] = False
        decision["deny_reasons"].append("SQL 解析器不可用，无法进行字段级权限判定")
        return decision

    try:
        tree = sqlglot.parse_one(sql)
    except Exception as e:
        decision["allowed"] = False
        decision["deny_reasons"].append(f"SQL 解析失败，无法进行字段级权限判定：{str(e)[:100]}")
        return decision
    if tree is None:
        decision["allowed"] = False
        decision["deny_reasons"].append("SQL 解析为空，无法进行字段级权限判定")
        return decision

    meta_map = _meta_map(meta)
    alias_map = _resolve_alias_map(tree, exp)

    # ★ 本次查询真正涉及的表（裸列归属判定用；不含 CTE 别名）
    cte_names = {normalize_table(c.alias_or_name) for c in tree.find_all(exp.CTE)}
    scope_tables = set()
    for t in tree.find_all(exp.Table):
        rt = normalize_table(t.name)
        if rt and rt not in cte_names:
            scope_tables.add(rt)

    # 找出最外层查询（UNION 时根是 Union，取第一个分支为输出列基准）
    outer = tree
    if isinstance(outer, exp.Union):
        outer = outer.this

    # ── 收集全部列引用，区分"输出列 / 表达式 / 过滤 / 分组排序"四类 ──
    # 输出列：最外层 SELECT 的投影列表（含 * 展开）
    output_cols = []            # [(结果列名, 真实表或None, 源列或None)]
    star_expanded = True
    for proj in (outer.expressions if isinstance(outer, exp.Select) else []):
        if isinstance(proj, exp.Star):
            # SELECT * 或 t.*
            star_table = normalize_table(proj.table) if getattr(proj, "table", None) else None
            expanded = _expand_star(star_table, alias_map, meta_map, tree, exp)
            if expanded is None:
                star_expanded = False
                continue
            output_cols.extend(expanded)
        else:
            alias = proj.alias_or_name
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(inner, exp.Column):
                src_tbl = _resolve_column_table(inner, alias_map)
                output_cols.append((alias, src_tbl, normalize_column(inner.name)))
            else:
                # 表达式/函数列：其中引用的敏感字段禁止被计算（substring/concat/CASE 绕过）
                output_cols.append((alias, None, None))
                for c in _walk_columns(inner, exp):
                    src_tbl = _resolve_column_table(c, alias_map)
                    _judge_column(decision, c.name, src_tbl, meta_map, sensitive, policies,
                                  action, scope_tables, is_admin=is_admin, clause="expr")
    if not star_expanded:
        decision["allowed"] = False
        decision["deny_reasons"].append("SELECT * 无法展开（表元数据缺失），已按安全策略拒绝")
        decision["parsed"] = True
        return decision

    decision["output_columns"] = [name for name, _t, _c in output_cols]
    decision["parsed"] = True

    # 输出列逐列判定（纯字段引用）
    for name, tbl, col in output_cols:
        if col is None:
            continue        # 表达式列已在上面按 clause=expr 判定过
        _judge_column(decision, col, tbl, meta_map, sensitive, policies, action,
                      scope_tables, is_admin=is_admin, clause="output", output_name=name)

    # 过滤类列（WHERE / HAVING / JOIN ON）与分组排序列（GROUP BY / ORDER BY）
    #   —— 遍历整棵树（含子查询），按子句类型区分对待。
    filter_cols, dim_cols = _collect_clause_cols(tree, exp)
    for c in filter_cols:
        src_tbl = _resolve_column_table(c, alias_map)
        _judge_column(decision, c.name, src_tbl, meta_map, sensitive, policies, action,
                      scope_tables, is_admin=is_admin, clause="filter")
    for c in dim_cols:
        src_tbl = _resolve_column_table(c, alias_map)
        _judge_column(decision, c.name, src_tbl, meta_map, sensitive, policies, action,
                      scope_tables, is_admin=is_admin, clause="dimension")

    # ★ UNION 右分支 / 子查询里其它 SELECT 的投影列也必须检查（都是最终输出的一部分）。
    #   clause="middle"：DENY 拒绝；MASK 放行但不登记（最终脱敏由最外层输出列重新判定）。
    outer_id = id(outer)
    for sel in tree.find_all(exp.Select):
        if id(sel) == outer_id:
            continue
        for proj in sel.expressions:
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(inner, exp.Column):
                src_tbl = _resolve_column_table(inner, alias_map)
                _judge_column(decision, inner.name, src_tbl, meta_map, sensitive, policies,
                              action, scope_tables, is_admin=is_admin, clause="middle")
            else:
                for c in _walk_columns(inner, exp):
                    src_tbl = _resolve_column_table(c, alias_map)
                    _judge_column(decision, c.name, src_tbl, meta_map, sensitive, policies,
                                  action, scope_tables, is_admin=is_admin, clause="expr")

    # 涉及的归一化表名
    decision["requested_tables"] = sorted(scope_tables)

    return decision


def _collect_clause_cols(tree, exp):
    """
    收集整棵树里的「过滤类列」与「分组/排序类列」。
    返回 (filter_cols, dim_cols)。
    """
    filter_cols, dim_cols = [], []
    for node in list(tree.find_all(exp.Where)) + list(tree.find_all(exp.Having)):
        filter_cols.extend(_walk_columns(node.this, exp))
    for node in tree.find_all(exp.Join):
        on = node.args.get("on")
        if on is not None:
            filter_cols.extend(_walk_columns(on, exp))
    for node in list(tree.find_all(exp.Group)) + list(tree.find_all(exp.Order)):
        dim_cols.extend(_walk_columns(node, exp))
    return filter_cols, dim_cols


def _expand_star(star_table, alias_map, meta_map, tree, exp):
    """
    展开 SELECT * / t.*。
    返回 [(列名, 表, 列), ...]；无法确认时返回 None（触发 fail-closed）。
    """
    # 若未指定表：取 FROM 里的所有表
    if not star_table:
        real_tables = {alias_map[a] for a in alias_map}
        # 排除 CTE（CTE 是临时结果集，无元数据可展开）
        cte_names = {normalize_table(c.alias_or_name) for c in tree.find_all(exp.CTE)}
        real_tables = {t for t in real_tables if t not in cte_names}
    else:
        real = alias_map.get(star_table, star_table)
        real_tables = {real}

    out = []
    for t in sorted(real_tables):
        cols = meta_map.get(t)
        if not cols:
            return None                 # 表元数据缺失 → 无法展开 → 拒绝
        for c in sorted(cols):
            out.append((c, t, c))
    return out


def _resolve_column_table(col, alias_map):
    """把带表前缀的列归一到真实表；裸列返回 None（由调用方按上下文兜底）。"""
    tbl = normalize_table(col.table) if getattr(col, "table", None) else ""
    if not tbl:
        return None
    return alias_map.get(tbl, tbl)


def _judge_column(decision, col_name, table, meta_map, sensitive, policies, action,
                  scope_tables, is_admin=False, clause="output", output_name=None):
    """
    对单个 (table, column) 做 ALLOW/MASK/DENY 判定，并把结论写回 decision。

    clause 表示该列引用出现的位置，决定 MASK 是否放行：
      "output"    纯字段引用输出列 → MASK 允许，登记 mask_map（结果脱敏）
      "dimension" GROUP BY / ORDER BY 里的分组/排序键 → MASK 允许（作为展示维度）
      "expr"      表达式/函数内（substring/concat/CASE/聚合参数）→ MASK 拒绝
      "filter"    WHERE / HAVING / JOIN ON → MASK 拒绝（定向访问风险）

    ★ 为什么区分 filter 与 dimension？
      「WHERE phone = 已知值」是定向查询某个手机号 → 泄露，必须拒绝；
      「GROUP BY phone + 输出脱敏手机号」只是按手机号聚合后展示脱敏值 → 属展示场景，应放行。
    ★ DENY 字段：任何位置一律拒绝。
    ★ 裸列（table 为 None）→ 在本查询涉及的表中逐个归属：
      - 任一表 DENY → 拒绝；
      - 任一表 MASK（且无 DENY）→ 按 clause 处理；
      - 其余 ALLOW。无法归属时按所有有元数据的表保守兜底（宁可误拒，不可漏放）。
    ★ is_admin=True（管理员）→ 不做字段级限制，直接放行（但审计仍在 evaluate 层记录）。
    """
    if is_admin:
        return

    col = normalize_column(col_name)

    # ── 裸列：归属未知，逐候选表判定 ──
    if table is None:
        candidates = sorted(scope_tables) or sorted(meta_map)
        deny_hit = False
        mask_rule = None
        for t in candidates:
            acc, _rule = effective_access(sensitive, policies, t, col, action)
            if acc == "DENY":
                deny_hit = True
                break
            if acc == "MASK":
                mask_rule = mask_rule or (_rule or "default")
        if deny_hit:
            decision["allowed"] = False
            decision["deny_reasons"].append(f"字段 {col} 命中 DENY，已拒绝访问")
            if clause == "output":
                decision["denied_columns"].append(output_name or col)
            return
        if mask_rule:
            if clause in ("expr", "filter"):
                decision["allowed"] = False
                decision["deny_reasons"].append(
                    f"MASK 字段 {col} 出现在{'表达式计算' if clause == 'expr' else '过滤/关联条件'}中，已拒绝（保守策略）")
                return
            if clause == "output":
                decision["mask_map"][output_name or col] = mask_rule
            # dimension → 作为分组/排序键，放行
        return

    # ── 带表前缀的列 ──
    acc, mask_rule = effective_access(sensitive, policies, table, col, action)
    if acc == "DENY":
        decision["allowed"] = False
        decision["deny_reasons"].append(f"字段 {table}.{col} 命中 DENY，已拒绝访问")
        if clause == "output":
            decision["denied_columns"].append(output_name or col)
        return
    if acc == "MASK":
        if clause in ("expr", "filter"):
            decision["allowed"] = False
            decision["deny_reasons"].append(
                f"字段 {table}.{col} 命中 MASK，但出现在{'表达式计算' if clause == 'expr' else '过滤/关联条件'}中，已拒绝（保守策略）")
            return
        if clause == "output":
            decision["mask_map"][output_name or col] = mask_rule
        # dimension → 作为分组/排序键，放行
    # ALLOW：不处理


def _load_sensitive_set():
    """从 config_store 读敏感资源，规整成 {(table, column)} 集合（含整表 '*'）。"""
    s = set()
    try:
        for r in config_store.get_sensitive_resources():
            t = normalize_table(r.get("table_name"))
            c = normalize_column(r.get("column_name"))
            if t and c:
                s.add((t, c))
    except Exception:
        return set()          # 读取失败 → 空集 → 无字段视为敏感 → 交由表级闸门兜底
    return s


# ═══════════════════════════════════════════════════════════
# ⑤ 结果脱敏（执行后、返回前，确定性）
# ═══════════════════════════════════════════════════════════
def mask_rows(columns, rows, mask_map):
    """
    对结果行做脱敏。mask_map = {列名: 脱敏规则}。
    返回新 rows（不修改原对象），脱敏列逐一 mask_value。
    """
    if not mask_map or not columns:
        return rows
    idx = {normalize_column(c): i for i, c in enumerate(columns)}
    out = []
    for row in rows:
        new_row = list(row)
        for name, rule in mask_map.items():
            i = idx.get(normalize_column(name))
            if i is not None:
                new_row[i] = mask_value(new_row[i], rule)
        out.append(new_row)
    return out


def mask_column_values(columns, rows, mask_map):
    """兼容别名：等价 mask_rows（保留两个名字便于调用方阅读）。"""
    return mask_rows(columns, rows, mask_map)


# ═══════════════════════════════════════════════════════════
# ⑥ 审计
# ═══════════════════════════════════════════════════════════
def audit(user, decision, action="query", question="", request_id="",
          tables=None, columns=None, final_sql="", ok=True, policy_version=None):
    """
    写一条权限判定审计（不落敏感原始值）。
    ★ 只记录字段名/判定结果，不记录手机号等具体值。
    """
    try:
        config_store.add_permission_audit({
            "username": (user or {}).get("username", ""),
            "roles": user_roles(user),
            "request_id": request_id,
            "question": (question or "")[:500],
            "tables": tables or [],
            "columns": columns or [],
            "action": action,
            "decision": "deny" if not decision["allowed"] else ("mask" if decision["mask_map"] else "allow"),
            "deny_reason": ("；".join(decision["deny_reasons"]))[:500],
            "rule_ids": decision.get("rule_ids", []),
            "policy_version": policy_version,
            "rewritten": 0,
            "final_sql": _safe_sql_repr(final_sql),
            "returned_columns": decision.get("output_columns") or [],
            "exported_columns": [],
            "ok": 1 if ok else 0,
        })
    except Exception:
        pass        # 审计失败不影响主流程


def _safe_sql_repr(sql):
    """SQL 的安全审计表示：只保留结构（表/列关键字），去掉字符串字面量。"""
    if not sql:
        return ""
    # 把单/双引号包裹的字面量替换成 ?，避免手机号等敏感值进审计
    return re.sub(r"'[^']*'|\"[^\"]*\"", "?", sql)


# ★ 公开别名（整改任务7）：供 server._audit_permission 等审计方复用，
#   语义与 _safe_sql_repr 完全一致——只保留 SQL 结构，字面量一律替换为 ?。
safe_sql_repr = _safe_sql_repr


# ═══════════════════════════════════════════════════════════
# ⑧ 行级权限（P3）：把管理员定义的可信过滤条件注入 SQL（AST 改写）
# ═══════════════════════════════════════════════════════════
_INJECT_RE = re.compile(r"[';]|--|/\*|\*/")     # 属性值禁止出现的注入字符


def resolve_row_filters(user, row_rules=None, user_attrs=None):
    """
    解析某用户对【各表】生效的行级过滤条件。

    返回 dict{table: [resolved_filter_sql, ...]}；任一占位符无法解析 → 返回 None（fail-closed）。

    ★ 信任边界：
      - filter_sql 由管理员定义（可信），可含 {attr} 占位符（如 dept_id = '{dept_id}'）；
      - 占位符值来自用户【已验证属性】（仅管理员设置），解析时做注入字符校验；
      - 绝不让用户自己提交的输入直接拼进过滤条件。
    """
    if row_rules is None:
        try:
            row_rules = config_store.list_row_permission_rules()
        except Exception:
            return None
    if user_attrs is None:
        try:
            user_attrs = config_store.get_user_attributes((user or {}).get("username")) or {}
        except Exception:
            user_attrs = {}

    roles = set(user_roles(user)) if user else set()
    uname = (user or {}).get("username")
    out = {}
    for r in row_rules or []:
        if not r.get("enabled"):
            continue
        st = r.get("subject_type")
        sn = r.get("subject_name")
        if st == "role" and sn not in roles:
            continue
        if st == "user" and sn != uname:
            continue
        template = r.get("filter_sql") or ""
        resolved = _resolve_template(template, user_attrs)
        if resolved is None:
            return None          # 无法解析 → 无法安全注入 → 调用方拒绝
        tbl = normalize_table(r.get("table_name"))
        out.setdefault(tbl, []).append(resolved)
    return out


def _resolve_template(template, attrs):
    """把 {attr} 占位符替换成已验证属性值；缺失/含注入字符 → None。"""
    placeholders = re.findall(r"\{(\w+)\}", template)
    resolved = template
    for ph in placeholders:
        val = attrs.get(ph)
        if val is None:
            return None
        s = str(val)
        if _INJECT_RE.search(s):
            return None
        resolved = resolved.replace("{" + ph + "}", s)
    return resolved


def apply_row_filters(sql, user, row_rules=None, user_attrs=None):
    """
    ★ 行级权限 AST 改写：把每个受保护基表包成「带过滤的子查询」。

    返回三元组 (rewritten_sql, changed, error)：
    - 无需改写      → (原 sql, False, "")；
    - 改写成功      → (新 sql, True, "")；
    - fail-closed   → (None, False, 原因) —— 调用方必须据此拒绝执行。

    ★ 安全要点（任务书"功能四"）：
      改写后必须由上层【重新解析、重新校验完整 SQL】，确保执行 SQL == 校验 SQL。
      本函数只做确定性改写，不改变列/聚合语义（等价于给基表追加过滤）。
    """
    if user is None or (user or {}).get("role") == "admin":
        return sql, False, ""          # 管理员不受行级限制

    filters_by_table = resolve_row_filters(user, row_rules=row_rules, user_attrs=user_attrs)
    if filters_by_table is None:
        return None, False, "行级过滤条件无法安全解析（占位符缺失或策略库异常）"
    if not filters_by_table:
        return sql, False, ""

    sqlglot, exp = _sqlglot()
    if sqlglot is None:
        return None, False, "SQL 解析器不可用，无法注入行级过滤"
    try:
        tree = sqlglot.parse_one(sql)
    except Exception:
        return None, False, "SQL 解析失败，无法注入行级过滤"

    cte_names = {normalize_table(c.alias_or_name) for c in tree.find_all(exp.CTE)}
    changed = False
    # 一次性快照所有 Table 节点，避免替换后又把新建的内层表重复包装
    for t in list(tree.find_all(exp.Table)):
        tbl = normalize_table(t.name)
        if tbl in cte_names or tbl not in filters_by_table:
            continue
        cond_sql = " AND ".join(filters_by_table[tbl])
        try:
            cond = sqlglot.parse_one(cond_sql)
        except Exception:
            return None, False, f"行级过滤条件非法：{cond_sql}"
        alias = t.alias_or_name
        inner = t.copy()
        sub = exp.Subquery(this=exp.select("*").from_(inner).where(cond))
        if alias and alias != t.name:
            sub = sub.as_(alias)
        t.replace(sub)
        changed = True

    if not changed:
        return sql, False, ""
    try:
        dialect = "clickhouse" if getattr(config, "DB_TYPE", "") == "clickhouse" else None
        return tree.sql(dialect=dialect), True, ""
    except Exception:
        return None, False, "改写后的 SQL 重新生成失败"


# ═══════════════════════════════════════════════════════════
# ⑦ AI 自然语言策略解析（P2：只草拟，不直接生效）
# ═══════════════════════════════════════════════════════════
_NL_PARSE_PROMPT = """你是权限配置解析器。把管理员的自然语言要求，转成【结构化字段访问规则】，只输出 JSON。

输入包含两部分：
1) 敏感资源清单（已知的表与字段，含中文注释）
2) 已知角色列表

请把要求解析成 JSON 数组，每项形如：
{{"role": "角色名", "table": "表名", "column": "字段名", "action": "query|export|both", "access": "ALLOW|MASK|DENY", "mask_rule": "phone|id_card|bank_card|name|default"}}

规则：
- 只使用"敏感资源清单"里存在的表/字段名（用英文物理名，小写）；找不到就标记为 unknown，不要瞎猜。
- 角色名只能来自已知角色列表；用 subject_name 字段存角色名。
- "不允许查看完整手机号但可看脱敏手机号" → access=MASK + mask_rule=phone（query），另可 DENY（export）。
- "不能查看资金流水明细但可看汇总指标" → 资金流水明细表整表 DENY（table 用表名，column 用 *）。
- 无法确定的表/字段/语义 → 放进 ambiguities 数组（说明原因），不要自行编造后当作已确定。
- 只输出 JSON：{{"rules": [...], "ambiguities": [{"issue": "..."}]}}

敏感资源清单：
{sensitive}

已知角色：{roles}
"""


def parse_nl_policy(text, sensitive, roles):
    """
    用 LLM 把自然语言权限要求解析成结构化规则草稿（不落库、不生效）。
    返回 (rules, ambiguities, impact)。

    ★ 安全边界：本函数只产出"待审核草稿"；真正落库/生效必须经管理员审批发布。
    ★ 资源映射校验：LLM 输出的表/字段若不在敏感资源清单里，一律转成 ambiguity，
      绝不允许"猜一个字段名"就进入草稿规则。
    """
    if not text.strip():
        return [], [{"issue": "输入为空"}], {}

    # 敏感资源清单：物理名 + 中文说明（给 LLM 提供上下文）
    sensitive_lines = []
    for r in sensitive or []:
        t = r.get("table_name") or r.get("table")
        c = r.get("column_name") or r.get("column")
        note = r.get("note") or ""
        sensitive_lines.append(f"- {t}.{c}（{note}）")
    sensitive_text = "\n".join(sensitive_lines) or "（空）"

    role_names = [r.get("name") for r in (roles or [])] or ["admin", "user"]
    prompt = (_NL_PARSE_PROMPT.replace("{sensitive}", sensitive_text)
              .replace("{roles}", ", ".join(role_names)))

    try:
        import llm
        raw = llm.chat("", prompt + "\n\n要求原文：\n" + text)
    except Exception as e:
        return [], [{"issue": f"LLM 解析失败：{str(e)[:120]}"}], {}

    # 容错解析 JSON
    parsed = {}
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            parsed = json.loads(m.group(0))
    except Exception:
        parsed = {}

    rules = parsed.get("rules") or []
    ambiguities = list(parsed.get("ambiguities") or [])

    # 资源映射校验 + 角色校验
    sensitive_keys = {(r.get("table_name"), r.get("column_name"))
                      for r in (sensitive or [])}
    valid_roles = set(role_names)
    clean_rules = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        role = r.get("role") or r.get("subject_name")
        table = normalize_table(r.get("table") or "")
        column = normalize_column(r.get("column") or "")
        access = (r.get("access") or "DENY").upper()
        # 角色校验
        if role and role not in valid_roles:
            ambiguities.append({"issue": f"角色「{role}」不在已知角色列表"})
            continue
        # 表/字段映射校验（column='*' 表示整表，允许）
        if column != "*" and (table, column) not in sensitive_keys:
            ambiguities.append({"issue": f"字段 {table}.{column} 不在敏感资源清单，需管理员确认映射"})
            continue
        if column == "*" and not any(t == table for t, _c in sensitive_keys):
            ambiguities.append({"issue": f"表 {table} 不在敏感资源清单，需管理员确认"})
            continue
        clean_rules.append({
            "subject_type": "role",
            "subject_name": role,
            "table_name": table,
            "column_name": column,
            "action": r.get("action") or "query",
            "access": access,
            "mask_rule": r.get("mask_rule") or ("default" if access == "MASK" else None),
        })

    # 影响分析：命中敏感资源数 + 可能受影响的字段数
    impact = {
        "rule_count": len(clean_rules),
        "affected_tables": sorted({r["table_name"] for r in clean_rules}),
        "deny_count": sum(1 for r in clean_rules if r["access"] == "DENY"),
        "mask_count": sum(1 for r in clean_rules if r["access"] == "MASK"),
    }
    return clean_rules, ambiguities, impact
