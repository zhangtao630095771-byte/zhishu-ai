# -*- coding: utf-8 -*-
"""
sql_modify.py —— SQL 多轮修改的结构保护（P0）

职责：当用户「修改已有 SQL」时，默认继承原 SQL 全部业务逻辑，只允许修改
用户【明确要求】的部分。本模块提供三件事：
  ① extract_* —— 从 SQL 提取结构（表 / 聚合函数 / 业务 WHERE / SELECT 表达式）
  ② diff_structure —— 对比 base 与 new 的结构差异（AST 结构对比）
  ③ detect_scope / unauthorized_changes —— 从修改指令识别授权范围，判定越界项

★ 复用现有 sqlglot + guard.extract_tables，不重复建设 SQL 解析能力。
★ 只做「结构保护」，不做语法/权限校验（那是 guard / sql_validator 的职责）。
"""
import re

import guard


# ═══════════════════════════════════════════════════════════
# ① 结构提取
# ═══════════════════════════════════════════════════════════

def _parse(sql):
    """解析单条 SQL，返回语法树；失败返回 None。"""
    import sqlglot
    for name in ("clickhouse", "mysql", "postgres", None):
        try:
            return sqlglot.parse_one(sql, read=name)
        except Exception:
            continue
    return None


def extract_tables(sql):
    """FROM / JOIN / 子查询 / CTE 中出现的真实表名集合（去库前缀）。"""
    return set(guard.extract_tables(sql) or [])


def extract_aggregations(sql):
    """聚合函数规范化文本集合（含 DISTINCT 与参数），用于检测 COUNT(DISTINCT)→COUNT 之类变化。

    解析失败返回 None（表示无法判定，diff 里跳过该项，不误报）。
    """
    tree = _parse(sql)
    if tree is None:
        return None
    from sqlglot import exp
    return {_expr_sql(n) for n in tree.find_all(exp.AggFunc)}


def extract_group_by(sql):
    """GROUP BY 表达式规范化集合。"""
    tree = _parse(sql)
    if tree is None:
        return None
    from sqlglot import exp
    out = set()
    for sel in tree.find_all(exp.Select):
        g = sel.args.get("group")
        if g is None:
            continue
        for e in (g.expressions or []):
            out.add(_expr_sql(e))
    return out


def extract_order_by(sql):
    """ORDER BY 表达式规范化集合（含排序方向）。"""
    tree = _parse(sql)
    if tree is None:
        return None
    from sqlglot import exp
    out = set()
    for o in tree.find_all(exp.Order):
        for e in o.expressions:
            out.add(_expr_sql(e))
    return out


def _expr_sql(node):
    """规范化表达式文本（压空白、去括号外空白）。"""
    try:
        return re.sub(r"\s+", " ", node.sql()).strip()
    except Exception:
        return str(node)


def _split_and(node):
    """把 AND 链拆成单个谓词节点。"""
    from sqlglot import exp
    if isinstance(node, exp.And):
        return _split_and(node.this) + _split_and(node.expression)
    return [node]


_TIME_COLS = ("create_time", "ctime", "create_data_time", "reg_time",
              "register_time", "create_date", "register_date", "date")


def _is_time_predicate(text):
    """判断 WHERE 谓词是否「时间谓词」：引用时间列 + 含日期字面量/时间函数。"""
    s = text.lower()
    has_col = any(c in s for c in _TIME_COLS)
    has_date = bool(re.search(r"\d{4}-\d{2}-\d{2}", s)) \
        or "now()" in s or "today(" in s or "tostartof" in s \
        or "interval" in s or "tostartofday" in s
    return has_col and has_date


def extract_business_where(sql):
    """业务 WHERE 谓词（排除时间谓词）的规范化集合。

    ★ 时间谓词被排除，因为「时间范围」是修改任务最常授权变更的部分。
    """
    tree = _parse(sql)
    if tree is None:
        return None
    from sqlglot import exp
    preds = set()
    for w in tree.find_all(exp.Where):
        for clause in _split_and(w.this):
            s = _expr_sql(clause)
            if s and not _is_time_predicate(s):
                preds.add(s)
    return preds


def extract_select_exprs(sql):
    """SELECT 输出表达式规范化集合（含 CASE WHEN 等复杂表达式）。"""
    tree = _parse(sql)
    if tree is None:
        return None
    from sqlglot import exp
    out = set()
    for sel in tree.find_all(exp.Select):
        for e in (sel.expressions or []):
            out.add(_expr_sql(e))
    return out


# ═══════════════════════════════════════════════════════════
# ② 结构差异对比
# ═══════════════════════════════════════════════════════════

def diff_structure(base_sql, new_sql):
    """对比 base 与 new 的结构，返回 {变更键: (base, new)}。

    键名（对应任务书 §P0-3 检查项）：
      tables          FROM/JOIN 表集合
      aggregations    聚合签名
      business_where  业务 WHERE 谓词
      select_exprs    SELECT 输出表达式
    """
    diffs = {}
    bt, nt = extract_tables(base_sql), extract_tables(new_sql)
    if bt != nt:
        diffs["tables"] = (sorted(bt), sorted(nt))

    ba, na = extract_aggregations(base_sql), extract_aggregations(new_sql)
    if ba is not None and na is not None and ba != na:
        diffs["aggregations"] = (sorted(ba), sorted(na))

    bw, nw = extract_business_where(base_sql), extract_business_where(new_sql)
    if bw is not None and nw is not None and bw != nw:
        diffs["business_where"] = (sorted(bw), sorted(nw))

    bs, ns = extract_select_exprs(base_sql), extract_select_exprs(new_sql)
    if bs is not None and ns is not None and bs != ns:
        diffs["select_exprs"] = (sorted(bs), sorted(ns))

    bg, ng = extract_group_by(base_sql), extract_group_by(new_sql)
    if bg is not None and ng is not None and bg != ng:
        diffs["group_by"] = (sorted(bg), sorted(ng))

    bo, no = extract_order_by(base_sql), extract_order_by(new_sql)
    if bo is not None and no is not None and bo != no:
        diffs["order_by"] = (sorted(bo), sorted(no))

    return diffs


# ═══════════════════════════════════════════════════════════
# ③ 授权范围识别与越界判定
# ═══════════════════════════════════════════════════════════

def detect_scope(question):
    """从修改指令识别「授权变更范围」，返回允许变更的键集合。"""
    q = question or ""
    scope = set()
    if re.search(r"时间|日期|近\s*\d+\s*天|近\s*\d+\s*周|最近|上周|上月|本月|今年|范围|天数|区间|到现在|截至目前", q):
        scope.add("time")
    if re.search(r"增加|新增|再加|加上|补充|多一个|还要", q):
        scope.add("add_select")
        scope.add("add_filter")
    if re.search(r"删除|去掉|不要|移除|删掉", q):
        scope.add("remove_select")
    # ★ 过滤条件变更/指定筛选值：只看/换成/筛选/过滤/只要/限定等（明确的过滤信号）
    #   （注意：不含「改成/改为」——那是「将…修改为…」的通用句式，不能当过滤信号）
    if re.search(r"只看|换成|只保留|筛选|过滤|只要|限定|仅看|只统计|仅统计", q):
        scope.add("filter")
    if re.search(r"排序|降序|升序|倒序|从小到大|从大到小", q):
        scope.add("order_by")
    if re.search(r"分组|按.{0,6}统计|按.{0,6}聚合|按.{0,6}看|维度", q):
        scope.add("group_by")
    if re.search(r"口径|计算方式|修改.{0,4}指标|.{0,4}率\b", q):
        scope.add("metric")
    return scope


def unauthorized_changes(diffs, question):
    """返回「未授权变更」的键列表。

    时间修改（time）默认允许变更时间谓词——但时间谓词已被 extract_business_where
    排除，所以 time 授权不会影响 business_where/tables/aggregations 的判定。
    """
    scope = detect_scope(question)
    unauth = []
    # 表变化：几乎任何修改都不该换表（除非明确要求换表）
    if "tables" in diffs and "tables" not in scope:
        unauth.append("tables")
    # 聚合变化：改口径/计算方式，或增删字段（会连带改变聚合集）时允许
    if "aggregations" in diffs and not (scope & {"add_select", "remove_select", "metric"}):
        unauth.append("aggregations")
    # 业务 WHERE 变化：明确增删/变更过滤、或改口径时允许
    if "business_where" in diffs and not (scope & {"add_filter", "filter", "remove_select", "metric"}):
        unauth.append("business_where")
    # SELECT 表达式变化：增删字段/改口径时允许
    if "select_exprs" in diffs and not (scope & {"add_select", "remove_select", "metric"}):
        unauth.append("select_exprs")
    # GROUP BY 变化：仅明确要求改分组时允许
    if "group_by" in diffs and "group_by" not in scope:
        unauth.append("group_by")
    # ORDER BY 变化：仅明确要求改排序时允许
    if "order_by" in diffs and "order_by" not in scope:
        unauth.append("order_by")
    return unauth


def build_repair_prompt(question, base_sql, bad_sql, unauthorized, diffs):
    """受约束修复提示：把越界项明确告诉模型，要求恢复到原结构。"""
    lines = [
        "你在对已有 SQL 做「增量修改」，但你修改了用户【未要求】的部分。",
        "请在上一轮 SQL 基础上，仅修改用户明确要求的部分，其余必须原样保留。",
        "",
        f"用户本轮要求：{question}",
        "",
        "上一轮 SQL（正确基线）：",
        base_sql,
        "",
        "你当前的 SQL（存在越界修改）：",
        bad_sql,
        "",
        "越界项（必须恢复为上一轮 SQL 的结构）：",
    ]
    for k in unauthorized:
        base, new = diffs.get(k, ("", ""))
        lines.append(f"- {k}：不应从「{base}」变成「{new}」")
    lines += [
        "",
        "请只输出修正后的 SQL（不要 ``` 包裹、不要解释）。若本次修改仅涉及时间范围，",
        "只需把上一轮 SQL 里的时间条件改成用户要求的时间，其余一字不改。",
    ]
    return "\n".join(lines)


def format_diff(diffs):
    """把差异字典格式化成可读字符串（用于日志/报告/返回前端）。"""
    if not diffs:
        return "（无结构差异）"
    parts = []
    for k, (base, new) in diffs.items():
        parts.append(f"{k}: {base} → {new}")
    return "；".join(parts)
