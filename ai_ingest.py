# -*- coding: utf-8 -*-
"""
ai_ingest.py —— AI 自动整合录入知识库

★ 职责：用户输入一段业务口径/逻辑描述，AI 解析成结构化知识条目，
   判断每条属于哪类知识（P1 指标 / P2 实体 / P2 关系 / P3 字段注释 / P3 字典 / P4 文档），
   自动落库到对应注册表，并返回录入结果。
"""
import json
import re
import llm
import config_store


def _parse_json(raw):
    """容错解析：抽取最外层 {...}"""
    if not raw:
        return {}
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            return json.loads(m.group(0))
    except Exception:
        pass
    return {}


def build_ingest_prompt(text):
    return f"""你是数据分析知识库的录入助手。用户会给你一段「业务口径/逻辑描述」，请把它解析成结构化知识条目，判断每一条属于哪种类型、该进哪个优先级层级。

知识类型（type）与优先级（priority）：
- metric          (P1) 指标定义：指标名 + 怎么算 + 依赖的表
- entity          (P2) 业务实体：中文业务名 → 真实表名 + 粒度
- relationship    (P2) 表关联 / Join 规则
- column_comment  (P3) 表字段的业务注释（表名 + 字段名 → 注释）
- dict            (P3) 字段的「编码 → 中文含义」取值（字段名 + 取值列表）
- doc             (P4) 普通业务文档 / 说明

只输出如下 JSON（不要 ``` 包裹、不要多余文字）：
{{
  "items": [
    {{"type": "metric", "name": "指标名", "definition": "口径说明", "metric_type": "count|ratio|amount|avg|other", "tables": ["依赖表名"], "keywords": ["检索词"]}},
    {{"type": "entity", "name": "业务实体名", "table_name": "真实表名", "grain": "一行=什么", "comment": "说明", "keywords": ["别名"]}},
    {{"type": "relationship", "from_entity": "左实体/表", "to_entity": "右实体/表", "join_sql": "JOIN 片段", "cardinality": "1:1|1:N", "pre_agg_required": false, "rule_note": "说明"}},
    {{"type": "column_comment", "table": "表名", "column": "字段名", "comment": "业务注释"}},
    {{"type": "dict", "name": "字典名", "field_hint": "字段名", "keywords": ["检索词"], "items": [{{"value": "0", "label": "否"}}, {{"value": "1", "label": "是"}}]}},
    {{"type": "doc", "title": "标题", "content": "正文", "keywords": ["检索词"]}}
  ]
}}

规则：
1. 一条描述可能解析出多条知识（如"保司订单 = need_insurance=1"既是指标口径又是字段字典）。
2. 判断不了的类型就不要输出该条，不要硬编。
3. 指标 definition 必须写清「怎么算」（字段/条件/公式）。
4. 有编码取值（如"0否 1是"）优先录成 dict。
5. 表名用真实表名（如 dwd_tu_order_all）。

用户输入：
{text}"""


def build_sql_ingest_prompt(question, sql, note=""):
    """★ SQL 投喂：从「取数问题 + 标准 SQL」提炼可复用知识"""
    return f"""你是数据分析知识库的录入助手。管理员会给你【一条取数问题】和它对应的【标准 SQL】。
请从这条「问题 → SQL」中提炼出【可复用的数据知识】，判断每条属于哪种类型、进哪个层级——
目标：下次有人问同类问题时，仅凭这些知识就能写出同样正确的 SQL。

知识类型（type）与优先级（priority）：
- metric          (P1) 指标定义：指标名 + 怎么算（字段/条件/公式）+ 依赖的表
- entity          (P2) 业务实体：中文业务名 → 真实表名 + 粒度（一行=什么）
- relationship    (P2) 表关联 / Join 规则（含 join 键）
- column_comment  (P3) 表字段的业务注释（表名 + 字段名 → 注释）
- dict            (P3) 字段的「编码 → 中文含义」取值（字段名 + 取值列表）
- doc             (P4) 业务口径文档：写清「什么问题该查哪张表 / 哪些字段 / 什么过滤条件 / 什么时间字段」，并点明易错点

只输出如下 JSON（不要 ``` 包裹、不要多余文字）：
{{
  "items": [
    {{"type": "metric", "name": "指标名", "definition": "口径说明（含字段与条件）", "metric_type": "count|ratio|amount|avg|other", "tables": ["依赖表名"], "keywords": ["检索词"]}},
    {{"type": "entity", "name": "业务实体名", "table_name": "真实表名", "grain": "一行=什么", "comment": "说明", "keywords": ["别名"]}},
    {{"type": "relationship", "from_entity": "左表/实体", "to_entity": "右表/实体", "join_sql": "JOIN 片段", "cardinality": "1:1|1:N", "pre_agg_required": false, "rule_note": "说明"}},
    {{"type": "column_comment", "table": "表名", "column": "字段名", "comment": "业务注释"}},
    {{"type": "dict", "name": "字典名", "field_hint": "字段名", "keywords": ["检索词"], "items": [{{"value": "0", "label": "否"}}, {{"value": "1", "label": "是"}}]}},
    {{"type": "doc", "title": "标题", "content": "正文", "keywords": ["检索词"]}}
  ]
}}

提炼要求：
1. 表名用【真实表名】，去掉库名前缀（如 ctc_data_warehouse.ods_x → ods_x）。
2. SQL 里用到的编码过滤（如 type = 187），含义能从问题/备注推断的 → 录成 dict 或 column_comment；
   推断不出的只写「该字段用于过滤」的事实，**不要编造中文含义**。
3. 时间字段务必点明类型/单位（例如 ctime 是秒级 Unix 时间戳、create_time 是 DateTime），写进 doc，
   这是最容易出错的地方。
4. 若 SQL 是聚合统计（count/sum/占比），产出一条 metric，definition 写清口径；指标名优先采用问题里的说法。
5. **至少产出一条 doc**：把「这类问题 → 该表 / 该字段 / 该条件 / 该时间口径」的映射写清楚，便于复用。
6. 判断不了的类型不要硬编；宁可少，不要编。

【取数问题】
{question}

【标准 SQL】
{sql}

【补充说明】
{note or "（无）"}"""


def _store_items(items):
    """把 AI 产出的 items 逐条落库，返回 [{type, priority, name, status, message, key, data}]"""
    results = []
    for it in items:
        t = it.get("type")
        try:
            results.append(_apply(t, it))
        except Exception as e:
            results.append({
                "type": t, "priority": _priority_of(t),
                "name": it.get("name") or it.get("field_hint") or it.get("title") or "",
                "status": "error", "message": f"落库失败：{e}",
            })
    return results


def ingest(text):
    """解析并落库，返回 [{type, priority, name, status, message}]"""
    if not (text or "").strip():
        return []
    raw = llm.chat("", build_ingest_prompt(text), timeout=60)
    return _store_items(_parse_json(raw).get("items") or [])


def ingest_sql(question, sql, note=""):
    """★ SQL 投喂：管理员粘贴「取数问题 + 标准 SQL」→ AI 提炼为可复用知识并落库。

    返回与 ingest() 同构的结果列表（含 key/data，供前端编辑/删除回显）。
    """
    if not (question or "").strip() or not (sql or "").strip():
        return []
    raw = llm.chat("", build_sql_ingest_prompt(question, sql, note), timeout=90)
    return _store_items(_parse_json(raw).get("items") or [])


def _priority_of(t):
    return {"metric": "P1", "entity": "P2", "relationship": "P2",
            "column_comment": "P3", "dict": "P3", "doc": "P4"}.get(t, "?")


def _apply(t, it):
    """按类型落库，返回结果（含 key 与 data，供前端编辑/删除回显）"""
    if t == "metric":
        config_store.upsert_metric({
            "name": it["name"], "definition": it.get("definition", ""),
            "metric_type": it.get("metric_type") or "other",
            "tables": it.get("tables") or [],
            "keywords": it.get("keywords") or [],
        })
        return {"type": t, "priority": "P1", "name": it["name"], "status": "ok",
                "message": "已录入指标", "key": it["name"], "data": it}
    if t == "entity":
        # ★ source="ai"：AI 录入的实体【不得覆盖人工登记的实体】
        #   （否则每次投喂/SQL 提炼都可能把人写好的关键词、口径冲掉）
        ok, why = config_store.upsert_entity({
            "name": it["name"], "table_name": it.get("table_name", ""),
            "grain": it.get("grain", ""), "comment": it.get("comment", ""),
            "keywords": it.get("keywords") or [],
        }, source="ai")
        return {"type": t, "priority": "P2", "name": it["name"],
                "status": "ok" if ok else "skipped",
                "message": "已录入实体" if ok else f"跳过：{why}",
                "key": it["name"], "data": it}
    if t == "relationship":
        config_store.upsert_relationship({
            "from_entity": it.get("from_entity", ""), "to_entity": it.get("to_entity", ""),
            "join_sql": it.get("join_sql", ""), "join_type": "LEFT",
            "cardinality": it.get("cardinality", "1:1"),
            "pre_agg_required": bool(it.get("pre_agg_required")),
            "rule_note": it.get("rule_note", ""),
        })
        key = f"{it.get('from_entity', '')}|{it.get('to_entity', '')}"
        return {"type": t, "priority": "P2",
                "name": f"{it.get('from_entity', '')}→{it.get('to_entity', '')}",
                "status": "ok", "message": "已录入关联规则", "key": key, "data": it}
    if t == "column_comment":
        config_store.set_column_comment(it["table"], it["column"],
                                        it.get("comment", ""), source="ai")
        return {"type": t, "priority": "P3", "name": f"{it['table']}.{it['column']}",
                "status": "ok", "message": "已录入字段注释",
                "key": f"{it['table']}.{it['column']}", "data": it}
    if t == "dict":
        name = it.get("name") or f"{it.get('field_hint', '')} 枚举"
        config_store.upsert_dict_entry({
            "name": name, "dict_type": "", "field_hint": it.get("field_hint", ""),
            "keywords": it.get("keywords") or [],
            "items": it.get("items") or [],
        })
        return {"type": t, "priority": "P3", "name": name,
                "status": "ok", "message": f"已录入字典（{len(it.get('items') or [])} 条取值）",
                "key": name, "data": it}
    if t == "doc":
        title = it.get("title", "")
        # ★ 同名文档按标题覆盖：否则每次投喂同一条知识都会新增一篇重复文档
        exist = next((d for d in config_store.get_kb_docs(enabled_only=False)
                      if d["title"] == title), None)
        config_store.upsert_kb_doc({
            "id": exist["id"] if exist else None,
            "title": title, "content": it.get("content", ""),
            "keywords": it.get("keywords") or [], "priority": it.get("priority", 4),
        })
        return {"type": t, "priority": "P4", "name": title,
                "status": "ok", "message": "已录入文档" + ("（覆盖同名）" if exist else ""),
                "key": title, "data": it}
    return {"type": t, "priority": "?", "name": "", "status": "error",
            "message": f"未知类型：{t}"}


def apply_item(t, data):
    """编辑后重新落库（公开包装 _apply）"""
    return _apply(t, data or {})


def delete_item(t, key):
    """按类型 + key 删除一条知识，返回是否成功"""
    if not key:
        return False
    if t == "metric":
        return config_store.delete_metric(key)
    if t == "entity":
        return config_store.delete_entity(key)
    if t == "relationship":
        for r in config_store.get_relationships(enabled_only=False):
            if f"{r['from_entity']}|{r['to_entity']}" == key:
                return config_store.delete_relationship(r["id"])
        return False
    if t == "column_comment":
        if "." in key:
            table, col = key.split(".", 1)
            return config_store.delete_column_comment(table, col)
        return False
    if t == "dict":
        return config_store.delete_dict_entry(key)
    if t == "doc":
        for d in config_store.get_kb_docs(enabled_only=False):
            if d["title"] == key:
                return config_store.delete_kb_doc(d["id"])
        return False
    return False
