# -*- coding: utf-8 -*-
"""
query_understanding.py —— 查询理解（Query Understanding）

★ 职责：在检索之前，先做一次「轻量结构化理解」：
    ① 意图识别（summary / trend / compare / detail / deny ...）
    ② 指标识别：从【权威指标清单】里挑出用户问的指标（直接锁定 P1 口径）
    ③ 实体识别：从【业务实体清单】里挑出涉及的业务对象（代驾订单 / 大板订单 ...）
    ④ 维度 / 时间范围 / 过滤条件 / 歧义标记

★ 为什么单独做一层？
    原架构是「问题 → 直接 RAG 检索 → 生成」。
    但"代驾订单的完单率"里，「完单率」是权威指标、「代驾订单」是实体，
    如果先识别出来，就能【直接锁定 P1 定义 + P2 关联】，不依赖向量分数门槛，
    从根本上避免"检索不到 / 召错表"。这正是 Semantic Layer 路由的前提。
"""
import json
import re
import config
import config_store
import knowledge
import llm
import calendar_util


def _parse_json(raw):
    """容错解析：抽取最外层 {...}，失败返回 {}"""
    if not raw:
        return {}
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            return json.loads(m.group(0))
    except Exception:
        pass
    return {}


def build_understanding_prompt(question, metric_names, entity_names):
    """构造「查询理解」的 prompt（把权威清单列给模型，让它从里面选）"""
    # ★ 日历/节假日感知：把「今天几号」与节日公历日期告知模型，
    #   让它能把「中秋节前7天 / 25号前7天」理解成具体公历区间，而不是原样照抄。
    from datetime import date as _date
    _t = _date.today()
    _wd = "一二三四五六日"[_t.weekday()]
    today_line = f"【今天的日期】{_t.strftime('%Y-%m-%d')}（星期{_wd}）。涉及时间的表达请结合它理解。\n"
    try:
        fest_ref = "【近年关键节日公历日期（供换算，勿臆造）】\n" + calendar_util.festival_reference(_t) + "\n"
    except Exception:
        fest_ref = ""
    return f"""你是一名数据分析语义理解专家。请把用户的自然语言问题解析为结构化意图。

{today_line}{fest_ref}
可用的【权威指标】（只能从下列名称中选，没命中就留空）：
{chr(10).join('  - ' + n for n in metric_names) if metric_names else '  （暂无）'}

可用的【业务实体】（只能从下列名称中选，没命中就留空）：
{chr(10).join('  - ' + n for n in entity_names) if entity_names else '  （暂无）'}

用户问题：
{question}

请只输出如下 JSON（不要 ``` 包裹、不要多余文字）：
{{
  "intent": "summary|trend|compare|detail|deny",
  "metrics": ["命中的指标名（来自上方权威指标清单）"],
  "entities": ["命中的实体名（来自上方业务实体清单）"],
  "dimensions": ["分组维度，如 月份/城市/渠道"],
  "time_range": "时间范围，尽量写成具体公历区间（如 2026-09-18~2026-09-24）；涉及节日请查阅上方节日表换算，没有则空字符串",
  "filters": ["过滤条件，如 状态=已完成"],
  "ambiguity": "存在的歧义或需要假设的说明，没有则空字符串"
}}"""


_UNDERSTAND_TIMEOUT = 8   # 秒：查询理解超时阈值（超时走降级，不拖累主流程）


def understand(question, index=None):
    """
    执行查询理解，返回结构化 dict。
    index 可选：传了就用里面的 metrics/entities 名称，并复用其缓存；否则从 knowledge 读。
    """
    if index:
        metric_names = [m["name"] for m in (index.get("metrics") or [])]
        entity_names = [e["name"] for e in (index.get("entities") or [])]
        cache = index.setdefault("_understanding_cache", {})
        # ★ 缓存：同一问题直接复用（避免重复 LLM 调用，降低延迟）
        if question in cache:
            return cache[question]
    else:
        metric_names = [m["name"] for m in knowledge.get_metrics(enabled_only=False)]
        entity_names = [e["name"] for e in knowledge.get_entities()]
        cache = None

    data = {}
    degraded = False
    prompt_text = build_understanding_prompt(question, metric_names, entity_names)
    for attempt in (1, 2):          # 最多尝试 2 次
        try:
            raw = llm.chat("", prompt_text, timeout=_UNDERSTAND_TIMEOUT)
            data = _parse_json(raw)
            if data:
                break
        except Exception as e:
            print(f"⚠️ 查询理解失败（第 {attempt} 次）：{e}")
            if attempt >= 2:
                degraded = True
            data = {}

    result = {
        "intent": data.get("intent", "summary"),
        "metrics": [m for m in (data.get("metrics") or []) if m in set(metric_names)],
        "entities": [e for e in (data.get("entities") or []) if e in set(entity_names)],
        "dimensions": data.get("dimensions") or [],
        "time_range": data.get("time_range") or "",
        "filters": data.get("filters") or [],
        "ambiguity": data.get("ambiguity") or "",
        "degraded": degraded,       # ★ 是否降级（LLM 失败/超时），供上层观测
    }

    # 只缓存成功结果；降级结果不缓存（下次可重试）
    if cache is not None and not degraded:
        cache[question] = result
    return result
