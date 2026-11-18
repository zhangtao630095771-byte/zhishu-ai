# -*- coding: utf-8 -*-
"""
evaluation/result_compare.py —— 业务结果对比引擎（P0-1-C / P0-2-L3）

★ 职责：把「AI 实际查询结果」和「标准结果 expected_result」做逐格对比，
  产出【六类判定 + 差异定位】。比 check_result 的「通过/不通过」更细，
  用于回答「到底差在哪、差几个格、是行数问题还是字段问题」。

★ 为什么不能只靠字符串相等？
  - 数仓结果里 "10" 与 "10.0"、None 与 ""（CK 空串 vs SQLite NULL）本质同值；
  - GROUP BY 输出顺序不稳定，逐行对齐会误报；
  - 「结构对但数值差」和「字段完全对不上」是两种病，修复方向不同。

★ 六类判定（对齐任务书 §3.2-C）：
    exact                完全一致
    within_tolerance     数值容差内一致（precision 归一后一致）
    structure_only       结果结构一致但数值不一致（行列对齐、逐格数值不同）
    column_mismatch      字段不一致（列数/列名不同）
    row_count_mismatch   行数不一致
    incomparable         无法比较（缺结果 / 缺期望 / 类型无法解析）

★ 纯函数：不连库、不调模型、不碰 IO，可直接单测。
"""
from __future__ import annotations

import re


# ═══════════════════════════════════════════════════════════
# 单元格规范化（与 checks._norm_cell 口径一致）
# ═══════════════════════════════════════════════════════════
def norm_cell(v, precision=4):
    """
    ① NULL 归一：None / "" / "None" / "null" 一律视为 NULL
    ② 数值归一：int/float/数字字符串统一 round(float(v), precision)
    ③ 布尔保持原样
    ④ 其余按字符串 strip（日期等按字面文本比较，避免时区歧义）
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


def _cells_equal(a, b):
    """两格是否相等（已是 norm_cell 之后的 Python 值）"""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if (isinstance(a, bool) and isinstance(b, bool)) else False
    if isinstance(a, float) or isinstance(b, float):
        # 归一化后都是 float，直接数值比（容差由 precision 已吸收）
        try:
            return float(a) == float(b)
        except Exception:
            return a == b
    return a == b


def _classify_cell_kind(a, b):
    """返回差异类型，用于定位报告"""
    if a is None or b is None:
        return "null"
    if isinstance(a, float) or isinstance(b, float):
        return "numeric"
    return "string"


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════
def compare(actual_rows, expected, precision=None):
    """
    对比实际结果与标准结果。

    参数：
        actual_rows = AI 查询结果（list of list/tuple），可能为 None
        expected    = expected_result：
                        {"columns": [...], "rows": [...], "precision": N}
                      或旧格式（数字/字符串/列表，向后兼容 → 降级为数值包含比较）
    返回：dict（见模块注释的 verdict / matched / details）
    """
    # ── 无法比较：缺任一输入 ──
    if actual_rows is None:
        return _incomparable("实际结果为空（未执行成功）")
    if expected is None:
        return _incomparable("未提供标准结果 expected_result")

    # ── 旧格式（非 dict 或非结构化）：降级为数值包含 ──
    if not (isinstance(expected, dict) and "rows" in expected):
        return _compare_legacy(actual_rows, expected)

    exp_rows = expected.get("rows") or []
    exp_cols = expected.get("columns") or []
    p = precision if precision is not None else expected.get("precision", 4)

    n_exp, n_act = len(exp_rows), len(actual_rows)

    # ── 行数不一致 ──
    if n_exp != n_act:
        return {
            "verdict": "row_count_mismatch",
            "matched": False,
            "details": {
                "row_count_expected": n_exp,
                "row_count_actual": n_act,
                "col_count_expected": len(exp_cols),
                "col_count_actual": len(actual_rows[0]) if actual_rows else 0,
                "cell_diffs": [],
                "extra_rows": [],
                "missing_rows": [],
            },
        }

    # ── 列数不一致 ──
    c_exp = len(exp_rows[0]) if exp_rows else len(exp_cols)
    c_act = len(actual_rows[0]) if actual_rows else 0
    if exp_rows and c_exp != c_act:
        return {
            "verdict": "column_mismatch",
            "matched": False,
            "details": {
                "row_count_expected": n_exp,
                "row_count_actual": n_act,
                "col_count_expected": c_exp,
                "col_count_actual": c_act,
                "expected_columns": list(exp_cols),
                "cell_diffs": [],
                "extra_rows": [],
                "missing_rows": [],
            },
        }

    # ── 无序逐行对比（允许 GROUP BY 输出顺序不同）──
    #   排序键用【规范化后的元组】，保证顺序稳定；但保留原始行用于 exact 判定。
    def _sortkey(row):
        return tuple(str(norm_cell(v, p)) for v in row)

    raw_want = sorted(exp_rows, key=_sortkey)
    raw_got = sorted(actual_rows, key=_sortkey)
    want = [tuple(norm_cell(v, p) for v in row) for row in raw_want]
    got = [tuple(norm_cell(v, p) for v in row) for row in raw_got]

    # 排序后完全一致 → exact（原始字面完全一致）或 within_tolerance（靠归一才对齐）
    if want == got:
        verdict = "exact" if _no_numeric_norm(raw_want, raw_got) else "within_tolerance"
        return {"verdict": verdict, "matched": True,
                "details": {"row_count_expected": n_exp, "row_count_actual": n_act,
                            "col_count_expected": c_exp, "col_count_actual": c_act,
                            "cell_diffs": [], "extra_rows": [], "missing_rows": []}}

    # ── 结构一致（行列对齐）但数值不一致 → structure_only ──
    cell_diffs = []
    # 找出第一个差异（用有序对齐后的第一个不一致位置，便于定位）
    for i, (w, g) in enumerate(zip(want, got)):
        if w == g:
            continue
        for j in range(len(w)):
            if not _cells_equal(w[j], g[j]):
                cell_diffs.append({
                    "row": i,
                    "col": j,
                    "col_name": exp_cols[j] if j < len(exp_cols) else str(j),
                    "expected": w[j],
                    "actual": g[j],
                    "kind": _classify_cell_kind(w[j], g[j]),
                })
    # 行数相等、列数相等 → 结构一致，差异只在数值
    return {
        "verdict": "structure_only",
        "matched": False,
        "details": {
            "row_count_expected": n_exp,
            "row_count_actual": n_act,
            "col_count_expected": c_exp,
            "col_count_actual": c_act,
            "cell_diffs": cell_diffs,
            "extra_rows": [],
            "missing_rows": [],
        },
    }


def _no_numeric_norm(raw_want, raw_got):
    """raw_want/raw_got 是已按同一 key 排序的【原始】行。
    逐格比较原始字面（str），完全一致 → 未发生归一 → exact；
    任一格原始字面不同（但规范化后相同）→ within_tolerance。"""
    for w, g in zip(raw_want, raw_got):
        if len(w) != len(g):
            return False
        for a, b in zip(w, g):
            if str(a) != str(b):
                return False
    return True


def _compare_legacy(actual_rows, expected):
    """旧格式（纯数字/字符串/列表）：数字集合包含，向后兼容"""
    def nums(obj):
        return set(re.findall(r"-?\d+(?:\.\d+)?", str(obj)))
    want, got = nums(expected), nums(actual_rows)
    miss = {n for n in want if n not in got}
    if not miss:
        return {"verdict": "exact", "matched": True,
                "details": {"row_count_expected": None, "row_count_actual": len(actual_rows),
                            "col_count_expected": None, "col_count_actual": None,
                            "cell_diffs": [], "extra_rows": [], "missing_rows": []}}
    return {"verdict": "structure_only", "matched": False,
            "details": {"missing_numbers": sorted(miss), "cell_diffs": [], "extra_rows": [],
                        "missing_rows": []}}


def _incomparable(reason):
    return {"verdict": "incomparable", "matched": False,
            "details": {"reason": reason, "cell_diffs": [], "extra_rows": [],
                        "missing_rows": []}}
