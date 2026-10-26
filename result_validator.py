# -*- coding: utf-8 -*-
"""
result_validator.py —— 执行后结果校验（Result Validator）

★ 职责：SQL 跑通后、返回用户前，对结果做一次「合理性体检」：
    ① 比率越界：比率列出现 <0 或 >1 的值（口径大概率错了）
    ② 整列为 NULL：某列全部为空（关联/口径失败的信号）
    ③ 行数异常：期望多行却只有 1 行（可能漏了维度 GROUP BY）
    ④ 量级异常：金额/数量列出现明显不合理的 0 行占比过高

★ 只产出 warning（不阻断，因为 SQL 已成功执行），供前端提示与「知识缺口」沉淀。
"""
import re


def _is_ratio_col(col, ratio_names=None):
    # 优先按「命中指标名」判断（metric_type='ratio' 或名称含比率词），回退列名正则
    if ratio_names:
        for n in ratio_names:
            if n and n in str(col):
                return True
    return bool(re.search(r"[率占比比例]|ratio", str(col), re.I))


def _to_float(v):
    try:
        if v is None:
            return None
        return float(str(v).strip().replace(",", "").replace("%", ""))
    except Exception:
        return None


def validate_result(columns, rows, resolved=None, question="", expected_multi_row=False):
    """
    参数：
        columns   = 结果列名列表
        rows      = 结果行（list of list/tuple）
        resolved  = semantic_layer.resolve() 结果（可选，用于判断比率列）
        question  = 原始问题（可选，用于提示文案）
        expected_multi_row = 问题隐含多行（如带"各月/各城市"维度）
    返回：warnings 列表（元素为字符串）
    """
    warnings = []
    if not columns or not rows:
        return warnings

    n_cols = len(columns)
    n_rows = len(rows)

    # ★ 命中指标里的比率指标名（metric_type='ratio' 或名称含比率词），供比率列判定
    ratio_names = []
    metric_types = (resolved or {}).get("metric_types") or {}
    metric_names = (resolved or {}).get("metrics") or []
    for mn in metric_names:
        if metric_types.get(mn) == "ratio" or re.search(r"[率占比比例]|ratio", mn, re.I):
            ratio_names.append(mn)

    # ① 比率越界
    for ci, col in enumerate(columns):
        if not _is_ratio_col(col, ratio_names):
            continue
        for ri, row in enumerate(rows):
            val = _to_float(row[ci]) if ci < len(row) else None
            if val is None:
                continue
            if val < -0.0001 or val > 1.0001:
                warnings.append(
                    f"⚠️ 比率列「{col}」第 {ri+1} 行值为 {val}，超出 [0,1] 合理区间，"
                    f"可能口径（分子/分母/COALESCE）有误")
                break  # 一列报一次即可

    # ② 整列为 NULL
    for ci, col in enumerate(columns):
        all_null = all((ci >= len(row) or row[ci] is None or str(row[ci]).strip() == "")
                       for row in rows)
        if all_null:
            warnings.append(
                f"⚠️ 列「{col}」全部为 NULL/空，可能该字段未关联上或口径未命中（建议核查 JOIN / 口径）")

    # ③ 行数异常
    if expected_multi_row and n_rows == 1:
        warnings.append(
            f"⚠️ 问题含分组维度（如各月/各城市），但结果只有 1 行，可能漏写了 GROUP BY 维度列")

    # ④ 量级异常：数值列全 0 占比过高（>80%）
    for ci, col in enumerate(columns):
        nums = [_to_float(row[ci]) for row in rows if ci < len(row)]
        nums = [x for x in nums if x is not None]
        if len(nums) >= 3 and all(x == 0 for x in nums):
            warnings.append(
                f"⚠️ 列「{col}」全部为 0，可能过滤条件过严或口径未命中")

    return warnings
