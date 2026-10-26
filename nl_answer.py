# -*- coding: utf-8 -*-
"""
nl_answer.py —— 自然语言回答生成（Natural Language Answer）

★ 职责：把「SQL 结果」转成用户能直接读的结论。
    要点：
    ① 结论先行，给出关键数字；
    ② 说明本次所用的【指标口径】（来自 P1 权威定义），让用户知道数怎么来的；
    ③ 如有歧义/假设（来自 Query Understanding），显式声明，避免"看似精确实则猜的"。
"""
import llm


def _table_snapshot(columns, rows, max_rows=8):
    """把结果压成一段可读的 Markdown 表格（截断）"""
    head = "| " + " | ".join(str(c) for c in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for r in rows[:max_rows]:
        body.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    more = f"\n（共 {len(rows)} 行，仅显示前 {max_rows} 行）" if len(rows) > max_rows else ""
    return "\n".join([head, sep] + body) + more


def build_answer_prompt(question, columns, rows, metric_defs="", ambiguity=""):
    snap = _table_snapshot(columns, rows)
    assumptions = (f"\n本次存在的歧义/假设：{ambiguity}\n" if ambiguity else "")
    defs = (f"\n本次采用的指标口径（权威定义）：\n{metric_defs}\n" if metric_defs else "")
    return f"""你是一名数据分析师。请根据查询结果，用中文给用户一段【自然语言结论】。

用户问题：{question}
{assumptions}{defs}
查询结果：
{snap}

要求：
1. 结论先行，直接给出最关键的数字与趋势；
2. 用用户能看懂的话，不要堆砌 SQL；
3. 若结果中有比率，说明其含义；
4. 如有上述歧义/假设，必须在结尾用一句话说明"注：本次按……口径/假设计算"；
5. 不要编造结果里没有的数字。只输出结论文本，不要 ``` 包裹。"""


def answer(question, columns, rows, metric_defs="", ambiguity=""):
    """
    生成自然语言回答。失败则返回简单表格摘要，绝不抛错拖垮主流程。
    """
    if not columns or not rows:
        return "（无结果数据，无法生成结论）"
    try:
        return llm.chat("", build_answer_prompt(question, columns, rows, metric_defs, ambiguity)).strip()
    except Exception as e:
        print(f"⚠️ 自然语言回答生成失败：{e}")
        # 降级：直接返回表格快照
        return _table_snapshot(columns, rows)
