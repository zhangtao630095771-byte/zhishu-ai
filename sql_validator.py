# -*- coding: utf-8 -*-
"""
sql_validator.py —— SQL 生成后、执行前的多维度校验（SQL Validator）

★ 五维校验：
    P0 规则        —— 禁 dateTrunc / SETTINGS / 裸中文别名 / 写操作（防御性，与 guard 互补）
    P1 指标口径    —— 命中的权威指标定义，其关键条件（status=N / 费用字段）必须体现在 SQL 里
    P2 Join 规则   —— 一对多关联必须预聚合后再 1:1 JOIN，禁止「明细×明细」笛卡尔放大
    P3 Schema      —— SQL 引用的表/字段必须在本次召回范围内（防编造表/越权）
    Metric 比率    —— 比率列必须 COALESCE 包裹 + *1.0，避免 NULL / 整数截断

★ 返回：issues 列表，每条 {severity: "error"|"warning", tier, rule, message, suggestion}
    error   → 必须回灌 LLM 自愈后重校验
    warning → 仅提示，不阻断
"""
import re
import guard


# ── 工具 ──
def _has_error(issues):
    return any(i["severity"] == "error" for i in issues)


def _norm(sql):
    return (sql or "").lower()


def _strip_conditional_aggregation(sql):
    """把条件聚合表达式从 SQL 中剔除，只保留「行过滤」层谓词。

    用于 forbidden_predicates 负向校验：同一条 SQL 里为【其它指标】
    （如 完单量 = CASE WHEN status=6 ...）写的条件聚合，并不会过滤
    「发单量」等指标所统计的行范围，因此不应触发负向约束。
    典型写法：CASE WHEN ... END、if(...)、multiIf(...)。
    """
    if not sql:
        return sql
    s = str(sql)
    # ① 反复删除最内层 CASE ... END（支持嵌套）
    prev = None
    while s != prev:
        prev = s
        s = re.sub(r"\bcase\b(?:(?!\bcase\b).)*?\bend\b", "", s, flags=re.I | re.S)
    # ② 删除 if(...)/multiIf(...) 函数式条件聚合（括号平衡删除）
    s = _strip_paren_funcs(s, ("if", "multiif"))
    return s


def _strip_paren_funcs(sql, names):
    out, i, n, low = [], 0, len(sql), sql.lower()
    while i < n:
        matched = False
        for name in names:
            # 仅匹配「非单词字符(或开头) + name + (」的函数调用，避免误删 ifnull 等
            prev_ch = "" if i == 0 else low[i - 1]
            if (prev_ch == "" or not (prev_ch.isalnum() or prev_ch == "_")) \
                    and low.startswith(name + "(", i):
                depth, k = 0, i + len(name)          # k 指向 '('
                while k < n:
                    if sql[k] == "(":
                        depth += 1
                    elif sql[k] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                i = k + 1
                matched = True
                break
        if not matched:
            out.append(sql[i])
            i += 1
    return "".join(out)


# ═══════════════════════════════════════════════════════════
# P0 · 系统规则（正则防御）
# ═══════════════════════════════════════════════════════════
def _check_p0(sql, issues):
    s = _norm(sql)
    if re.search(r"\bdatetrunc\b", s):
        # ★ 注意：s 已经过 _norm() 转为【小写】，此处正则必须用小写，
        #   否则（写成 dateTrunc）会因大小写不匹配而永不命中 —— 该规则曾因此形同虚设。
        issues.append({"severity": "error", "tier": "P0", "rule": "no_dateTrunc",
                       "message": "SQL 含 dateTrunc()，应使用 toStartOf* 系列",
                       "suggestion": "改为 toStartOfMonth/toStartOfDay 等"})
    if re.search(r"\bsettings\b", s):
        issues.append({"severity": "error", "tier": "P0", "rule": "no_settings",
                       "message": "SQL 含 SETTINGS 子句，执行参数由系统统一控制",
                       "suggestion": "删除 SETTINGS，只保留纯 SELECT 体"})
    if re.search(r"\b(delete|update|drop|insert|truncate|alter)\b", s):
        issues.append({"severity": "error", "tier": "P0", "rule": "readonly",
                       "message": "SQL 含写操作关键字", "suggestion": "只能是 SELECT 查询"})
    # 裸中文别名（AS 后紧跟中文且既非反引号也非双引号包裹）
    #   ★ ClickHouse 中 `中文` 与 "中文" 都是合法引号标识符，均允许；
    #     只有「裸中文」（无引号）才是 code 62 错误。
    #   ★ 只取 AS 后的【第一个 token】：旧正则允许空格，会把
    #     `AS t GROUP BY "中文别名"` 整段吞进来，误报成裸中文别名。
    for m in re.finditer(r"\bas\s+([^\s,()]+)", s):
        alias = m.group(1).strip()
        if alias.startswith(("`", '"')):
            continue
        if re.search(r"[\u4e00-\u9fff]", alias):
            issues.append({"severity": "error", "tier": "P0", "rule": "alias_backtick",
                           "message": f"列别名含裸中文：AS {alias}",
                           "suggestion": "用反引号或双引号包裹，如 AS `中文名`"})


# ═══════════════════════════════════════════════════════════
# P1 · 指标口径一致性
# ═══════════════════════════════════════════════════════════
def _check_p1(sql, ctx, resolved, issues):
    """
    P1 指标口径一致性（warning 级，避免误伤）。
    ★ 采用「同族满足」策略：一次问题常同时命中「通用指标」与「业务线专属指标」
      （如 完单量=status=6 与 代驾完单量=status=40），二者口径冲突是正常的——
      只要 SQL 命中了同族中【任意一个】要求的取值（status 值 / 费用字段），
      就视为满足，不再逐条硬要求，避免对正确 SQL 误报 error。
    """
    s = _norm(sql)
    status_vals = set()
    fee_fields = set()
    for metric_name, (definition, _tier) in (resolved.get("bindings") or {}).items():
        if not definition or metric_name.startswith("join:"):
            continue
        for m in re.finditer(r"status\s*=\s*(\d+)", definition, re.I):
            status_vals.add(m.group(1))
        # ★ 只关注费用专属字段（避免 order_no/total/amount 等通用字段产生噪音）
        for f in re.findall(r"\b(information_fee|service_fee|actual_information_fee|deduct_service_fee)\b", definition, re.I):
            fee_fields.add(f.lower())
    # ★ 补充：从命中指标的 sql_template（权威口径 SQL 片段）也提取 status/费用字段，
    #   比 definition 自然语言更精确；无 sql_template 时行为与旧版一致。
    for m in (ctx.p1_metrics or []):
        tmpl = m.get("sql_template") or ""
        if not tmpl:
            continue
        for mm in re.finditer(r"status\s*=\s*(\d+)", tmpl, re.I):
            status_vals.add(mm.group(1))
        for f in re.findall(r"\b(information_fee|service_fee|actual_information_fee|deduct_service_fee)\b", tmpl, re.I):
            fee_fields.add(f.lower())
    if status_vals and not any(re.search(r"status\s*=\s*%s" % re.escape(v), s) for v in status_vals):
        issues.append({
            "severity": "warning", "tier": "P1", "rule": "metric_status",
            "message": f"命中指标的权威口径要求使用 status∈{{{', '.join(sorted(status_vals))}}}，但 SQL 中未发现对应 status 过滤",
            "suggestion": "按指标定义把正确的 status 值写进 SQL（如 代驾完单=status=40、通用完单=status=6）",
        })
    if fee_fields and not any(f in s for f in fee_fields):
        issues.append({
            "severity": "warning", "tier": "P1", "rule": "metric_fee_field",
            "message": f"命中指标涉及费用字段 {sorted(fee_fields)}，但 SQL 中未发现",
            "suggestion": "按口径取对应的费用字段（代驾=service_fee，大板=information_fee）",
        })

    # ★★ 硬约束（error 级，可阻断执行）：指标权威定义里【人工显式声明】的必要条件/字段
    #   （metric 元数据 required_predicates / required_columns）。
    #   与上面的启发式 warning 不同 —— 这是人工确认过的必要条件，缺失即为口径错误，
    #   必须由执行闸门拦下（severity=error → agent 拒绝执行）。
    #   ★ 采用「同族任一满足」：命中多个同族指标时（如 完单量=status6 与 代驾完单量=status40），
    #     只要【有一个】指标的声明条件全部满足即通过；全都不满足才报 error。
    #     —— 否则同族指标会互相冲突，必然误拦。
    def _squash(x):
        return re.sub(r"\s+", "", (x or "").lower())

    def _satisfied(pred, target=None):
        """过滤片段是否出现在 SQL 中（空格不敏感；`status = 6` 亦接受 `status IN (6)`）。

        target 指定要在哪份 SQL 里检测：默认整条 SQL（用于 required 校验，
        CASE WHEN 里的 status=6 也算满足）；负向校验传入已剥离条件聚合的
        s_forbidden，避免条件聚合被误判。
        """
        base = sql if target is None else target
        t = _squash(base)
        p = _squash(pred)
        if not p:
            return True
        if p in t:
            return True
        m2 = re.match(r"^([a-z_][\w]*)=(\d+)$", p)
        if m2:
            f, v = m2.group(1), m2.group(2)
            if re.search(re.escape(f) + r"in\([^)]*\b" + re.escape(v) + r"\b", t):
                return True
        return False

    s_squash = _squash(s)
    # ★ 负向约束专用：剥离条件聚合后的 SQL（CASE WHEN / if / multiIf 不影响行范围）
    s_forbidden = _norm(_strip_conditional_aggregation(sql))
    declared = []          # [(指标名, [未满足项描述, ...]), ...]
    for m in (ctx.p1_metrics or []):
        preds = [p for p in (m.get("required_predicates") or []) if p]
        cols = [c for c in (m.get("required_columns") or []) if c]
        if not preds and not cols:
            continue
        missing = [f"过滤 `{p}`" for p in preds if not _satisfied(p)]
        missing += [f"字段 `{c}`" for c in cols
                    if not re.search(r"\b" + re.escape(c.lower().strip()) + r"\b", s)]
        declared.append((m.get("name") or "", missing))
    if declared and all(missing for _n, missing in declared):
        detail = "；".join(f"「{n}」需 {'、'.join(miss)}" for n, miss in declared if miss)
        issues.append({
            "severity": "error", "tier": "P1", "rule": "metric_required",
            "message": f"命中指标的权威口径必要条件均未满足：{detail}",
            "suggestion": "按指标定义补上必要的过滤条件/字段（约束由指标元数据声明，缺失即为口径错误）",
        })

    # ★★ 负向约束（error 级，可阻断）：指标权威定义里【禁止出现】的字段/过滤
    #   （metric 元数据 forbidden_predicates）。
    #   针对「发单量 ≠ 完单量」这类口径幻觉：发单量不限订单状态，SQL 里出现 status 过滤
    #   即口径错误（模型常把"发单"臆想成"完单"，擅自加 status=6）。
    def _forbidden_hit(pred):
        p = _squash(pred)
        if not p:
            return False
        # 裸字段名（如 status）→ 词边界匹配，避免误伤 status_date / order_status 等
        #   ★ 只在「行过滤」层（已剥离条件聚合的 s_forbidden）判断，
        #     避免同 SQL 中其它指标的条件聚合（CASE WHEN status=6）被误判。
        if re.fullmatch(r"[a-z_][\w]*", p):
            return bool(re.search(r"\b" + re.escape(p) + r"\b", s_forbidden))
        # 片段（如 status = 6）→ 在剥离条件聚合后的 SQL 里做片段匹配
        return _satisfied(pred, s_forbidden)

    for m in (ctx.p1_metrics or []):
        forb = [f for f in (m.get("forbidden_predicates") or []) if f]
        if not forb:
            continue
        hit = [f for f in forb if _forbidden_hit(f)]
        if hit:
            issues.append({
                "severity": "error", "tier": "P1", "rule": "metric_forbidden",
                "message": (f"命中指标「{m.get('name')}」的口径禁止出现这些过滤：{hit}，"
                            f"但 SQL 中已出现（发单≠完单，只有完单量/完单率才需要 status 过滤）"),
                "suggestion": "去掉禁止的过滤条件；如确需按状态统计，应改用对应状态指标（完单量=status=6）",
            })


# ═══════════════════════════════════════════════════════════
# P2 · Join 规则（防笛卡尔放大）
# ═══════════════════════════════════════════════════════════
# ★ 明细表命名启发式（整改任务3 / docs/risk-remediation）：
#   仅用于「未登记关联」的保守告警（warning），绝不单独作为 JOIN 基数断言。
#   匹配方式：表名小写后补一个尾部下划线再查子串（如 dwd_order_fee → "dwd_order_fee_"
#   含 "_fee_"），这样 _all 结尾的真实表名（ods_tu_order_fee_detail_all）也能命中，
#   而 dim_city / dwd_order 这类主表/维表不会误伤。
_DETAIL_TABLE_HINTS = ("_fee_", "_detail_", "_record_", "_history_",
                       "_log_", "_item_", "_line_", "_flow_")


def _joined_tables(sql):
    """
    用 sqlglot 提取 SQL 中出现在【JOIN 子句】里的真实表名（子查询别名 / CTE 不算）。

    ★ 为什么只取 JOIN 子句：FROM 主表不是"被关联方"，一对多放大只会发生在
      JOIN 进来的表上；把 FROM 也算进来会对所有单表查询产生噪音。
    ★ 解析失败返回 []：本函数只服务 warning 级告警，静默降级不影响主流程
      （表级权限 / 五维校验的 fail-closed 由 guard / validate_sql_ex 负责，不在此层）。
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return []
    for name in ("clickhouse", "sqlite", "mysql"):
        try:
            tree = sqlglot.parse_one(sql, read=name)
        except Exception:
            continue
        if tree is None:
            continue
        cte = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        out = []
        for j in tree.find_all(exp.Join):
            for t in j.find_all(exp.Table):
                n = (t.name or "").lower()
                if n and n not in cte and n not in out:
                    out.append(n)
        return out
    return []


def _check_p2(sql, ctx, issues):
    for rel in ctx.p2_relationships:
        if not rel.get("pre_agg_required"):
            continue
        # 取 JOIN 右侧表（费用明细表）
        m = re.search(r"\bJOIN\s+(?:`?[\w]+`?\.)?([\w]+)", rel.get("join_sql", ""), re.I)
        if not m:
            continue
        right = m.group(1).lower()
        # 直接 JOIN（笛卡尔放大风险）
        direct = re.search(
            r"\bjoin\s+(?:`?[\w]+`?\.)?" + re.escape(right) + r"\b", sql, re.I)
        # 已预聚合：存在 (SELECT ... FROM right ... GROUP BY 的子查询
        subq = re.search(
            r"\(\s*select\b.*?\bfrom\s+(?:`?[\w]+`?\.)?" + re.escape(right)
            + r"\b.*?group\s+by", sql, re.I | re.S)
        if direct and not subq:
            issues.append({
                "severity": "error", "tier": "P2", "rule": "join_pre_agg",
                "message": f"表 {right} 是一对多明细表，被直接 JOIN（会产生笛卡尔放大，污染所有聚合指标）",
                "suggestion": f"先按关联键 GROUP BY 预聚合 {right} 再 1:1 JOIN：{rel.get('join_sql')}",
            })

    # ★ 未登记关联的启发式告警（整改任务3，warning 不阻断）：
    #   已登记关系只覆盖管理员手工登记的表对；真实数仓大量一对多表对未登记，
    #   直接 JOIN 时此前完全漏检（C-01，test_join_cardinality.py 复现）。
    #   这里对「疑似明细表 + 未登记 + 直接 JOIN 且未预聚合」给出 warning，
    #   明确表达"关联基数未确认"，而不是假装已准确识别一对多。
    #   ★ 表名关键词只是启发式线索，不作为基数断言；绝不因此无差别阻断所有 JOIN。
    known_right = set()
    for rel in ctx.p2_relationships:
        m2 = re.search(r"\bJOIN\s+(?:`?[\w]+`?\.)?([\w]+)", rel.get("join_sql", ""), re.I)
        if m2:
            known_right.add(m2.group(1).lower())
    for t in _joined_tables(sql):
        if t in known_right:
            continue                      # 已登记 = 业务已确认基数，不重复告警
        probe = t + "_"
        if not any(h in probe for h in _DETAIL_TABLE_HINTS):
            continue                      # 非明细命名（主表/维表），不告警
        # 已预聚合（子查询 GROUP BY）则写法安全，不告警
        subq2 = re.search(
            r"\(\s*select\b.*?\bfrom\s+(?:`?[\w]+`?\.)?" + re.escape(t)
            + r"\b.*?group\s+by", sql, re.I | re.S)
        if subq2:
            continue
        issues.append({
            "severity": "warning", "tier": "P2", "rule": "join_unregistered_risk",
            "message": (f"表 {t} 按命名疑似明细表，但未在知识库登记关联关系，"
                        "直接 JOIN 的一对多放大风险未确认"),
            "suggestion": (f"先按关联键 GROUP BY 预聚合 {t} 后再 1:1 JOIN；"
                           "或在知识库登记该关联（含基数与是否需预聚合）后重建索引"),
        })


# ═══════════════════════════════════════════════════════════
# P3 · Schema（表/字段存在性）
# ═══════════════════════════════════════════════════════════
def _extract_table_columns(sql):
    """
    用 sqlglot 提取 SQL 中的列引用。解析失败返回 None。

    返回 (table_cols, bare_cols, output_aliases)：
        table_cols     = {真实表名: set(带表前缀的列)} —— 可精确归属校验
        bare_cols      = set(裸列名)                —— 没带前缀的列
        output_aliases = set(SELECT 输出别名/列名)  —— 用于排除 GROUP BY/ORDER BY 里
                         引用别名导致的误报

    ★ 裸列也要抓：模型生成的单表 SQL 大多不带表前缀（如 WHERE actual_pay_information_fee>0），
      之前只抓带前缀列会漏掉「猜错字段」这类错误，所以裸列必须纳入。
    ★ 但 SELECT 的输出别名（如 AS "统计日期"）在 GROUP BY/ORDER BY 里会被 sqlglot
      解析成裸列，必须用 output_aliases 排除，否则中文别名被误判成「不存在的列」。
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return None
    if not (sql or "").strip():
        return {}, set(), set()
    try:
        tree = sqlglot.parse_one(sql)
    except Exception:
        return None
    if tree is None:
        return None
    # 别名/表名 → 真实表名（sqlglot 的 Table.name 已去掉库前缀）
    alias_map = {}
    for t in tree.find_all(exp.Table):
        real = (t.name or "").lower()
        if not real:
            continue
        alias_map[real] = real
        alias = (t.alias_or_name or "").lower()
        if alias and alias != real:
            alias_map[alias] = real
    table_cols = {}
    bare_cols = set()
    for c in tree.find_all(exp.Column):
        if not c.name:
            continue
        tbl = (c.table or "").lower()
        if tbl:
            real = alias_map.get(tbl, tbl)
            table_cols.setdefault(real, set()).add(c.name.lower())
        else:
            bare_cols.add(c.name.lower())
    # SELECT 输出别名（exp.Alias.alias）——GROUP BY/ORDER BY 引用它们时不算真实列
    output_aliases = set()
    for a in tree.find_all(exp.Alias):
        if a.alias:
            output_aliases.add(a.alias.lower())
    return table_cols, bare_cols, output_aliases


def _check_columns(sql, ctx, issues):
    """
    列级存在性校验：
      ① 带表前缀的列 → 精确归属到该表，检查是否在其 column_list 中；
      ② 裸列         → 检查是否在任何召回表的列并集中。
    二者都不在 → error「列不存在，疑似猜错字段」。
    """
    table_cols_map = {}
    all_cols = set()
    for t in (ctx.p3_table_objs or []):
        cols = t.get("column_list") or []
        if cols:
            lower = {str(c).lower() for c in cols}
            table_cols_map[t["name"].lower()] = lower
            all_cols.update(lower)
    if not table_cols_map:
        # ★ 降级必须留痕：老索引/异常索引没有 column_list 时，
        #   列级校验无法执行。不能让调用方误以为"列都校验过了"。
        issues.append({
            "severity": "warning", "tier": "P3", "rule": "column_check_skipped",
            "message": "本次上下文缺少结构化列清单，列级存在性校验已跳过",
            "suggestion": "重建索引（/api/index/rebuild）后重试，以启用列级校验",
        })
        return
    extracted = _extract_table_columns(sql)
    if extracted is None:
        # ★ 解析失败同样要留痕（由表级校验兜底，但不能静默）
        issues.append({
            "severity": "warning", "tier": "P3", "rule": "column_check_degraded",
            "message": "SQL 解析失败，列级存在性校验已降级跳过",
            "suggestion": "检查 SQL 方言/语法；表级权限校验仍然生效",
        })
        return
    table_cols, bare_cols, output_aliases = extracted

    def _add(tbl, col):
        issues.append({
            "severity": "error", "tier": "P3", "rule": "column_exist",
            "message": f"SQL 引用了表 {tbl} 中不存在的列：{col}（疑似猜错字段）",
            "suggestion": f"请核对 {tbl} 的实际字段名，或使用该表真实存在的列",
        })

    for tbl, cols in table_cols.items():
        allowed = table_cols_map.get(tbl)
        if allowed is None:
            continue                    # 表不在本次召回范围（表级校验已拦）→ 跳过
        for col in cols:
            if col not in allowed:
                _add(tbl, col)

    for col in bare_cols:
        if col in output_aliases:
            continue                    # SELECT 输出别名（GROUP BY/ORDER BY 引用），不是真实列
        if col not in all_cols:
            _add("(本次召回的表)", col)


def _check_p3(sql, ctx, allowed_tables, issues):
    # ★ 不再吞异常：提取失败意味着"表/列是否越权"无法确认，
    #   必须由上层按 fail-closed 处理（见 validate_sql_ex）。
    used = guard.extract_tables(sql)
    allowed = set(allowed_tables or [])
    for t in used:
        if t not in allowed:
            issues.append({
                "severity": "error", "tier": "P3", "rule": "table_exist",
                "message": f"SQL 引用了未授权/不存在的表：{t}",
                "suggestion": "仅使用本次召回范围内的表",
            })
    # ★ 列级校验（新增）：拦截「表对但列名猜错」的静默算错/执行失败
    _check_columns(sql, ctx, issues)


# ═══════════════════════════════════════════════════════════
# Metric · 比率写法
# ═══════════════════════════════════════════════════════════
def _check_metric(sql, ctx, resolved, issues):
    s = _norm(sql)
    # 命中了比率类指标，才检查比率写法（优先 metric_type=='ratio'，回退名称启发式）
    ratio_names = []
    for m in (ctx.p1_metrics or []):
        if m.get("metric_type") == "ratio" or "率" in m["name"] or "ratio" in m["name"].lower():
            ratio_names.append(m["name"])
    if not ratio_names:
        return
    # 在 SELECT 列表里找除法表达式
    select_block = ""
    m = re.search(r"select\s+(.*?)\bfrom\b", s, re.I | re.S)
    if m:
        select_block = m.group(1)
    if "/" in select_block:
        # 比率列应被 COALESCE( ... , 0) 包裹
        if "coalesce" not in select_block:
            issues.append({
                "severity": "warning", "tier": "Metric", "rule": "ratio_coalesce",
                "message": "比率列未用 COALESCE 包裹，空结果会返回 NULL",
                "suggestion": "用 COALESCE(分子 * 1.0 / 分母, 0) 包裹",
            })
        if "* 1.0" not in select_block and "1.0 *" not in select_block:
            issues.append({
                "severity": "warning", "tier": "Metric", "rule": "ratio_decimal",
                "message": "比率除法未见 *1.0，整数相除会被截断成 0",
                "suggestion": "除法前乘 1.0 保证出小数",
            })


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════
def validate_sql_ex(sql, ctx, resolved, allowed_tables):
    """
    五维校验主入口（带状态）。

    返回 dict：
        issues   : list[dict]   同旧版格式
        status   : "passed" | "failed" | "error" | "degraded"
                   passed   = 全部通过（可能含 warning）
                   failed   = 有 error 级问题（口径/Schema 不合格）
                   error    = 校验器自身抛异常（结果不可信，必须阻断）
                   degraded = 通过，但有检查项降级跳过（已记录原因）
        degraded : list[str]    降级原因
        error    : str          校验器异常摘要（无异常时为空）

    ★ 为什么要有 status？
      旧接口只返回 issues 列表，"校验器崩了"和"没有问题"都表现为空列表，
      调用方无法区分 —— 这正是"异常被当成通过"的根源。
    """
    issues = []
    degraded = []
    errors = []

    if not (sql or "").strip():
        issues.append({
            "severity": "error", "tier": "P0", "rule": "empty_sql",
            "message": "SQL 为空", "suggestion": "重新生成 SQL",
        })
        return {"issues": issues, "status": "failed", "degraded": [], "error": ""}

    # ★ 前导注释不再"整体跳过校验"：先剥掉注释判断是否真有 SQL 主体，
    #   但后续校验仍针对完整 SQL（注释里的危险词也会被 P0 看到，宁可误报）。
    _body = re.sub(r"^\s*(?:--[^\n]*\n?)+", "", sql, count=1)
    _body = re.sub(r"^\s*/\*.*?\*/", "", _body, count=1, flags=re.S)
    if not _body.strip():
        issues.append({
            "severity": "error", "tier": "P0", "rule": "empty_sql",
            "message": "SQL 只有注释、没有查询主体", "suggestion": "重新生成 SQL",
        })
        return {"issues": issues, "status": "failed", "degraded": [], "error": ""}

    checks = [
        ("P0", lambda: _check_p0(sql, issues)),
        ("P1", lambda: _check_p1(sql, ctx, resolved, issues)),
        ("P2", lambda: _check_p2(sql, ctx, issues)),
        ("P3", lambda: _check_p3(sql, ctx, allowed_tables, issues)),
        ("Metric", lambda: _check_metric(sql, ctx, resolved, issues)),
    ]
    for tier, fn in checks:
        try:
            fn()
        except Exception as e:
            # ★ fail-closed：关键校验跑不起来，不能当作"没问题"。
            err = f"{tier} 校验异常：{type(e).__name__}: {e}"
            errors.append(err)
            issues.append({
                "severity": "error", "tier": tier, "rule": "checker_error",
                "message": err,
                "suggestion": "校验器异常意味着结果不可信，已阻断执行；请检查日志修复",
            })

    # 降级项（目前只有列级校验会降级）——必须让调用方看得见
    degraded = [i["message"] for i in issues
                if i.get("rule") in ("column_check_skipped", "column_check_degraded")]

    if errors:
        status = "error"
    elif _has_error(issues):
        status = "failed"
    elif degraded:
        status = "degraded"
    else:
        status = "passed"

    return {
        "issues": issues,
        "status": status,
        "degraded": degraded,
        "error": errors[0] if errors else "",
    }


def validate_sql(sql, ctx, resolved, allowed_tables):
    """
    兼容旧调用：只返回 issues 列表。
    ★ 新代码请用 validate_sql_ex()，以区分 passed/failed/error/degraded。
    """
    return validate_sql_ex(sql, ctx, resolved, allowed_tables)["issues"]
