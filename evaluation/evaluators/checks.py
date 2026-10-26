# -*- coding: utf-8 -*-
"""
evaluation/evaluators/checks.py —— 多维判分器

把 agent.ask() 的返回结果与 Golden 用例的期望【逐维对比】。
每个 check 返回 (status, detail)：
    status = True  → 通过
    status = False → 失败
    status = None  → 不适用 / 跳过（不计入该维度分母）

★ 判分原则：
  ① 只做「可判定的弱断言」，避免误报；
  ② 用例没提供某维度期望 → 该维度记 None，跳过，不拉低也不抬高；
  ③ 幻觉判定必须保守（宁可漏报，不可误报），否则指标失去意义。
"""
import re


def _l(x):
    return str(x).lower()


# ═══════════════════════════════════════════════════════════
# ① 执行成功 / 拒答
# ═══════════════════════════════════════════════════════════
def check_ok(r, c):
    """系统是否按期望执行（expected_ok 默认 True）"""
    exp = c.get("expected_ok", True)
    err = (r.get("error") or "")[:80]
    return (bool(r.get("ok")) == bool(exp),
            f"ok={r.get('ok')} 期望={exp} error={err}")


def check_abstain(r, c):
    """期望拒答时，系统必须不执行（ok=False）；期望回答时，系统必须不拒答。"""
    if "expected_abstain" not in c:
        return (None, "")
    exp = bool(c["expected_abstain"])
    abstained = not r.get("ok")
    return (abstained == exp, f"abstain={abstained} 期望={exp}")


# ═══════════════════════════════════════════════════════════
# ② 业务语义：表 / 指标 / SQL 谓词
# ═══════════════════════════════════════════════════════════
def check_tables(r, c):
    """期望召回/使用的表是否命中（Table Retrieval Recall 的基础）"""
    exp = c.get("expected_tables") or []
    if not exp:
        return (None, "")
    got = {_l(t) for t in (r.get("tables") or [])}
    miss = [t for t in exp if _l(t) not in got]
    return (not miss, f"缺表={miss} 实际={r.get('tables')}")


def check_metrics(r, c):
    exp = c.get("expected_metrics") or []
    if not exp:
        return (None, "")
    got = set(r.get("metrics") or [])
    miss = [m for m in exp if m not in got]
    return (not miss, f"缺指标={miss} 实际={r.get('metrics')}")


def check_forbidden_metrics(r, c):
    """易混淆场景：不该召回的指标（跨业务线误召回）"""
    exp = c.get("forbidden_metrics") or []
    if not exp:
        return (None, "")
    got = set(r.get("metrics") or [])
    bad = [m for m in exp if m in got]
    return (not bad, f"误召回指标={bad}")


def check_sql_contains(r, c):
    exp = c.get("expected_sql_contains") or []
    if not exp:
        return (None, "")
    sql = _l(r.get("sql") or "")
    miss = [s for s in exp if _l(s) not in sql]
    return (not miss, f"SQL缺谓词={miss}")


def check_sql_not_contains(r, c):
    exp = c.get("expected_sql_not_contains") or []
    if not exp:
        return (None, "")
    sql = _l(r.get("sql") or "")
    bad = [s for s in exp if _l(s) in sql]
    return (not bad, f"SQL含禁止谓词={bad}")


# ═══════════════════════════════════════════════════════════
# ③ Grounding：SQL 引用的表/字段是否有依据
# ═══════════════════════════════════════════════════════════
_SQL_TBL_RE = re.compile(r"\b(?:from|join)\s+([`\"]?[\w.]+[`\"]?)", re.I)
_SQL_NOISE = {"select", "unnest", "numbers", "values"}
_CTE_RE = re.compile(r"(?:\bwith\b|,)\s*([a-zA-Z_]\w*)\s+as\s*\(", re.I)


def extract_ctes(sql):
    """提取 CTE 名（WITH x AS ( … )）——CTE 是查询内部别名，不是真实表，不应计入无依据表。"""
    return {m.lower() for m in _CTE_RE.findall(sql or "")}


def extract_sql_tables(sql):
    out = []
    for m in _SQL_TBL_RE.findall(sql or ""):
        t = m.strip("`\"").split(".")[-1]
        if t and t.lower() not in _SQL_NOISE and t not in out:
            out.append(t)
    return out


def check_grounding(r, c, allowed_tables=None, schema=None):
    """
    SQL 引用的每张表，都必须出现在【召回表 ∪ 授权表白名单】中，否则视为无依据。
    若提供了 schema（{表: set(列)}），再校验字段是否有依据（仅当该表在 schema 中时）。
    """
    sql = r.get("sql") or ""
    if not sql.strip() or sql.strip().startswith("--"):
        return (True, "无 SQL（拒答/未生成）")
    allowed = {_l(t) for t in (allowed_tables or [])} | {_l(t) for t in (r.get("tables") or [])}
    if not allowed:
        return (None, "无表集合可比对")
    ctes = extract_ctes(sql)
    used = [t for t in extract_sql_tables(sql) if _l(t) not in ctes]
    bad = [t for t in used if _l(t) not in allowed]
    return (not bad, f"无依据表={bad}")


# ═══════════════════════════════════════════════════════════
# ④ 幻觉检测（保守口径）
# ═══════════════════════════════════════════════════════════
def check_hallucination(r, c, allowed_tables=None):
    """
    判定为「有幻觉风险」的情形（任一）：
      a) 执行成功但 SQL 为空
      b) SQL 引用了无依据的表（grounding 失败）
      c) 执行成功但结果为空行，而自然语言答案仍给出了非零数字结论
    """
    sql = r.get("sql") or ""
    if r.get("ok"):
        if not sql.strip():
            return (False, "ok=True 但 SQL 为空")
        g, gd = check_grounding(r, c, allowed_tables)
        if g is False:
            return (False, f"SQL 引用无依据表：{gd}")
        rows = r.get("rows") or []
        ans = r.get("answer")
        if not rows and isinstance(ans, str):
            nums = [n for n in re.findall(r"\d+(?:\.\d+)?", ans) if n not in ("0",)]
            if nums:
                return (False, "结果为空但答案含非零数字")
    return (True, "")


# ═══════════════════════════════════════════════════════════
# ⑤ 结果准确性（用例提供 expected_result 时才判定）
# ═══════════════════════════════════════════════════════════
def _norm_cell(v, precision=4):
    """
    单元格规范化（整改任务4 / C-04）：
      ① NULL 归一：None、""、"None"、"null" 一律视为 NULL（兼容 CK 空串与 SQLite NULL）
      ② 数值归一：int/float/数字字符串统一 round(float(v), precision)
         —— 解决旧实现 "10" ≠ "10.0" 的精度误报
      ③ 其余按字符串 strip 比较（日期等时间类型按字面文本比较，够用且不引入时区歧义）
    """
    if v is None:
        return None
    if isinstance(v, str) and v.strip().lower() in ("", "none", "null"):
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), precision)
    s = str(v).strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return round(float(s), precision)
    return s


def _check_result_structured(rows, exp):
    """
    结构化比较（expected_result = {"rows": [...], "columns": [...], "precision": N}）：
      ① 行数必须一致 —— 聚合结果少算/多算一行都是口径错误（旧弱比较会漏判）
      ② 无序逐行比较 —— 每行转规范化元组后排序对齐，允许 GROUP BY 输出顺序不同
      ③ 列数必须一致 —— 防止"期望数字碰巧命中无关列"的假阳性
      ④ 失败信息给出第一个不匹配的行（期望 vs 实际），供报告定位
    """
    exp_rows = exp.get("rows") or []
    precision = exp.get("precision", 4)
    if len(rows) != len(exp_rows):
        return (False, f"行数不符：期望 {len(exp_rows)} 行，实际 {len(rows)} 行")
    if exp_rows and rows and len(rows[0]) != len(exp_rows[0]):
        return (False, f"列数不符：期望 {len(exp_rows[0])} 列，实际 {len(rows[0])} 列")
    want = sorted((tuple(_norm_cell(v, precision) for v in row) for row in exp_rows),
                  key=lambda t: tuple(str(x) for x in t))
    got = sorted((tuple(_norm_cell(v, precision) for v in row) for row in rows),
                 key=lambda t: tuple(str(x) for x in t))
    for w, g in zip(want, got):
        if w != g:
            return (False, f"结果不匹配（无序比较）：期望行 {w}，实际行 {g}")
    return (True, "")


def check_result(r, c):
    exp = c.get("expected_result")
    if exp is None:
        return (None, "")
    rows = r.get("rows")
    if rows is None:
        return (None, "无结果可对比")

    # ★ 新：结构化比较（dict 且含 rows）—— 行数/列数/无序/精度/NULL 全规则
    if isinstance(exp, dict) and "rows" in exp:
        return _check_result_structured(rows, exp)

    # 旧：数字集合包含（纯数字/字符串/列表格式，向后兼容既有数据集）
    def nums(obj):
        return set(re.findall(r"-?\d+(?:\.\d+)?", str(obj)))

    want = nums(exp)
    got = nums(rows)
    miss = {n for n in want if n not in got}
    return (not miss, f"结果缺数值={miss}")


# ═══════════════════════════════════════════════════════════
# ⑥ 时间口径（用例提供 expected_time 时判定 SQL 是否含区间）
# ═══════════════════════════════════════════════════════════
def _next_day(s):
    try:
        from datetime import date, timedelta
        y, m, d = [int(x) for x in str(s).split("-")[:3]]
        return (date(y, m, d) + timedelta(days=1)).isoformat()
    except Exception:
        return None


def check_time(r, c):
    """
    时间口径判定（兼容系统实际写法）：
      ① 精确：SQL 同时含 start 与 end；end 允许「左闭右开」的次日边界
         （系统 P0 规则用 [start, end+1)，故 2026-09-30 的区间写作 2026-10-01）
      ② 宽松：年份命中 + 存在时间过滤函数（toYear/toStartOf*/INTERVAL/between/比较符）
    """
    if c.get("time_sensitive"):
        return (None, "time_sensitive 跳过")
    exp = c.get("time_range")
    if not exp or not isinstance(exp, dict):
        return (None, "")
    sql = _l(r.get("sql") or "")
    if not sql.strip() or sql.strip().startswith("--"):
        return (None, "无 SQL（拒答/未生成）")

    start, end = exp.get("start"), exp.get("end")
    ok_start = (not start) or (_l(start) in sql)
    nd = _next_day(end) if end else None
    ok_end = (not end) or (_l(end) in sql) or (nd and _l(nd) in sql)
    if ok_start and ok_end:
        return (True, "")

    years = set(re.findall(r"\d{4}", f"{start or ''}{end or ''}"))
    if years and all(y in sql for y in years) and re.search(
            r"toyear|tostartof|tostartofmonth|interval|between|>=|<=", sql):
        return (True, "宽松匹配（函数式时间过滤）")

    miss = ([start] if (start and not ok_start) else []) + ([end] if (end and not ok_end) else [])
    return (False, f"SQL缺时间边界={miss}")


def check_format(r, c):
    """
    输出格式断言：expected_format = {"列名关键字": "正则"}。
    用于「取数正确、但展示格式不对」这类问题（如月份要求 YYYY-MM，系统给了 YYYY-MM-DD）。
    """
    spec = c.get("expected_format")
    if not spec or not isinstance(spec, dict):
        return (None, "")
    cols = list(r.get("columns") or [])
    rows = r.get("rows") or []
    if not cols or not rows:
        return (None, "无结果可比对")
    bad = []
    for col_key, regex in spec.items():
        idx = next((i for i, name in enumerate(cols) if col_key in str(name)), None)
        if idx is None:
            bad.append(f"缺列『{col_key}』（实际列={list(cols)}）")
            continue
        vals = [row[idx] for row in rows if row and len(row) > idx]
        non_match = [v for v in vals if not re.match(regex, str(v))]
        if non_match:
            bad.append(f"『{col_key}』不匹配 {regex}，样例={[str(x) for x in non_match[:2]]}")
    return (not bad, "；".join(bad))


# 维度注册表：名字 → (函数, 是否"越低越好")
DIMENSIONS = {
    "ok":              (check_ok, False),
    "abstain":         (check_abstain, False),
    "tables":          (check_tables, False),
    "metrics":         (check_metrics, False),
    "forbidden":       (check_forbidden_metrics, False),
    "sql_contains":    (check_sql_contains, False),
    "sql_not_contains": (check_sql_not_contains, False),
    "time":            (check_time, False),
    "result":          (check_result, False),
    "format":          (check_format, False),
}
