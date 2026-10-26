# -*- coding: utf-8 -*-
"""
join_suggest.py —— 「关联 / Join 规则」的关联字段推荐（含 AI 推断）

★ 解决的问题：
  在「知识管理 → 语义注册表 → 关联/Join 规则」里手写 join_sql 既慢又容易错
  （例如把两张表的主键 id 当关联键 → t.id = r.id 这类错误）。
  这里给一个"选表 → 推荐关联字段 → 确认后自动拼 join_sql"的能力。

★ 推荐来源（按可信度排序）：
  1. existing  已有关系规则里用过的关联键（最可信）
  2. same_name 两表【同名字段】（排除 id/status 等通用噪声列）
  3. fk_guess  外键推断（左 xxx_id → 右 id，或反之）—— 低分，仅作候选，需人工确认
  4. ai        LLM 结合表注释 + 字段名推断（无确定性候选时尤其有用）
"""
import re

import db
import knowledge
import config_store
import llm


# 同名字段里【不该当关联键】的通用噪声列
# ★ 特别是 id：两张表都有 id 是各自的【主键】，用它做关联是最经典的错误
#   （就是之前 t.id = r.id 那个 bug 的成因），所以必须排除。
_GENERIC_COLS = {
    "id", "status", "state", "type", "ctime", "create_time", "update_time",
    "create_data_time", "enabled", "money", "total", "amount", "total_amount",
    "order_amount", "remark", "note", "is_deleted",
}

# 常见的关联键名字（命中则加分）
_COMMON_KEYS = ["order_no", "customer_id", "driver_id", "order_id", "car_id",
                "user_id", "company_id", "customer", "driver", "phone"]


def resolve_table(text):
    """把用户输入解析成真实表名。

    依次尝试：① 表名精确 → ② 实体名/别名 → ③ 表名/注释模糊包含。
    返回 (表名 or None, 说明, 候选列表)
    """
    s = (text or "").strip()
    if not s:
        return None, "输入为空", []
    tables = knowledge.business_tables()
    if s in tables:
        return s, "表名精确命中", [s]
    low = s.lower()
    for t in tables:
        if t.lower() == low:
            return t, "表名精确命中", [t]

    # 实体名 / 别名（如「代驾订单」→ ods_valet_order_all）
    #   ★ 命中多个实体时要提示歧义：像「资金流水」这种词可能同时是多个实体的别名，
    #     闷头取第一个会让人以为选对了表。
    ents = [e for e in config_store.get_entities()
            if e.get("table_name") and
            (s == e.get("name") or s in list(e.get("keywords") or []))]
    if len(ents) == 1:
        return ents[0]["table_name"], f"实体「{ents[0]['name']}」", [ents[0]["table_name"]]
    if len(ents) > 1:
        tabs = []
        for e in ents:
            if e["table_name"] not in tabs:
                tabs.append(e["table_name"])
        who = "、".join(e["name"] for e in ents[:4])
        return tabs[0], f"实体别名命中多个（{who}…），暂取「{ents[0]['name']}」", tabs

    # 模糊：表名或表注释包含关键词（与 /api/tables?search= 同一套口径）
    comments = config_store.get_table_comments()
    hits = [t for t in tables
            if low in t.lower() or low in (comments.get(t, "") or "").lower()]
    if len(hits) == 1:
        return hits[0], "模糊匹配（唯一）", hits
    if hits:
        # 多个候选：优先最短（通常最贴近），并把候选回传让前端提示
        hits_sorted = sorted(hits, key=len)
        return hits_sorted[0], f"模糊匹配（{len(hits)} 个候选，取最贴近的一个）", hits_sorted[:8]
    return None, "未匹配到表（请检查表名，或先用关键词搜索）", []


def _cols(table):
    """取表字段名列表；连不上库时返回 None（不抛，保证接口可用）"""
    try:
        return [c for c, _t in db.list_columns(table)]
    except Exception:
        return None


def _name_tokens(table):
    """把表名拆成有意义的词（用于判断"字段名里的业务词是否指向这张表"）"""
    base = (table or "").lower()
    for p in ("ods_", "dwd_", "dim_", "dws_", "all", "local", "_"):
        base = base.replace(p, " ")
    return {t for t in base.split() if len(t) >= 3}


def _deterministic_candidates(left_table, right_table, lcols, rcols):
    """确定性候选：同名字段 + 外键推断

    ★ 外键推断为什么这么保守？
      "左表 customer_id → 右表 id" 这种猜测【通常都是错的】（customer_id 指向客户维表，
      不是右表主键）。所以只在【字段里的业务词正好是对方表名的一部分】时才推荐，
      例如右表 ..._fee_detail_all 与左表字段 fee_id。宁可少推荐，也不要瞎推荐。
    """
    lset, rset = set(lcols), set(rcols)
    out = []

    # ① 同名字段（排除 id/status 等通用噪声列）
    for c in sorted(lset & rset):
        if c.lower() in _GENERIC_COLS:
            continue
        score = 95 if c.lower() in _COMMON_KEYS else 85
        out.append({"left_col": c, "right_col": c, "source": "same_name", "score": score,
                    "reason": f"两表同名字段 {c}"})

    # ② 外键推断（保守）：字段前缀的业务词要能在对方表名里找到
    ltok, rtok = _name_tokens(left_table), _name_tokens(right_table)
    for c in sorted(lset):
        cl = c.lower()
        if cl.endswith("_id") and "id" in rset and cl[:-3] in rtok:
            out.append({"left_col": c, "right_col": "id", "source": "fk_guess", "score": 60,
                        "reason": f"左表 {c} 的业务词与右表名相符 → 指向右表主键 id（需确认）"})
    for c in sorted(rset):
        cl = c.lower()
        if cl.endswith("_id") and "id" in lset and cl[:-3] in ltok:
            out.append({"left_col": "id", "right_col": c, "source": "fk_guess", "score": 60,
                        "reason": f"右表 {c} 的业务词与左表名相符 → 指向左表主键 id（需确认）"})

    out.sort(key=lambda x: -x["score"])
    return out


def _ai_candidate(left_table, right_table, lcols, rcols, hint=""):
    """LLM 推断关联字段；失败/解析不出返回 None（绝不拖垮接口）"""
    try:
        comments = config_store.get_table_comments()
        prompt = f"""你在帮数据分析师配置两张表的 JOIN 关联规则。请判断这两张表最可能用哪两个字段关联。

表A：{left_table}
  业务注释：{comments.get(left_table, "（无）")}
  字段：{", ".join(lcols[:80])}

表B：{right_table}
  业务注释：{comments.get(right_table, "（无）")}
  字段：{", ".join(rcols[:80])}

补充说明：{hint or "（无）"}

只输出如下 JSON（不要 ``` 包裹、不要多余文字）：
{{"left_col": "表A的关联字段名", "right_col": "表B的关联字段名", "reason": "为什么这么关联（一句话）", "cardinality": "1:1 或 1:N 或 N:1"}}
若确实判断不出关联字段，输出 {{}}。
★ 不要因为两张表都有 id 就用 id 关联（那是各自的主键）。"""
        raw = llm.chat("", prompt, timeout=60)
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            return None
        import json
        d = json.loads(m.group(0))
        lc, rc = (d.get("left_col") or "").strip(), (d.get("right_col") or "").strip()
        if not lc or not rc:
            return None
        # ★ 幻觉护栏：AI 给的字段必须【真的存在于对应表】里，否则丢弃。
        #   模型很爱编一个"听起来合理"的字段名（如 pay_log_id = code），放进候选会误导人。
        ll = {c.lower() for c in lcols}
        rr = {c.lower() for c in rcols}
        if lc.lower() not in ll or rc.lower() not in rr:
            return None
        return {"left_col": lc, "right_col": rc, "source": "ai", "score": 75,
                "reason": (d.get("reason") or "AI 推断").strip(),
                "cardinality": (d.get("cardinality") or "").strip()}
    except Exception:
        return None


def suggest_join(left, right, hint=""):
    """主入口：给左右两张表/实体，推荐关联字段。

    返回：
      {
        "ok": bool, "message": str,
        "left":  {"input","table","note","ambiguous":[...]},
        "right": {...},
        "candidates": [{"left_col","right_col","reason","source","score",...}],
        "columns_available": bool,     # 是否取到了字段（连不上库时为 False）
      }
    """
    lt, lnote, lamb = resolve_table(left)
    rt, rnote, ramb = resolve_table(right)
    out = {
        "ok": False, "message": "",
        "left": {"input": left, "table": lt, "note": lnote, "ambiguous": lamb},
        "right": {"input": right, "table": rt, "note": rnote, "ambiguous": ramb},
        "candidates": [], "columns_available": False,
    }
    if not lt or not rt:
        out["message"] = f"表未识别：左={lnote}；右={rnote}"
        return out
    if lt == rt:
        out["message"] = "左右是同一张表，无需关联"
        return out

    cands = []

    # ① 复用已有关系规则里出现过的关联键
    for rel in config_store.get_relationships(enabled_only=False):
        js = (rel.get("join_sql") or "")
        if lt in js and rt in js and "ON" in js.upper():
            on = js.split("ON", 1)[-1].strip()
            cands.append({"left_col": "", "right_col": "", "source": "existing", "score": 99,
                          "reason": f"已有关系规则正在用：{on}", "on_clause": on})

    # ② / ③ 确定性候选（需要字段）
    lcols = _cols(lt)
    rcols = _cols(rt)
    if lcols and rcols:
        out["columns_available"] = True
        cands += _deterministic_candidates(lt, rt, lcols, rcols)

    # ④ AI 推断（作为补充候选）
    if lcols and rcols:
        ai = _ai_candidate(lt, rt, lcols, rcols, hint)
        if ai:
            cands.append(ai)

    # 去重（按 左右字段 组合）
    seen, uniq = set(), []
    for c in cands:
        k = (c.get("left_col"), c.get("right_col"), c.get("on_clause", ""))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    uniq.sort(key=lambda x: -x.get("score", 0))
    out["candidates"] = uniq
    out["ok"] = bool(uniq)
    if not uniq:
        out["message"] = ("未推荐出关联字段：可能是左右表没有同名键，"
                          "或当前连不上数据库读不到字段。请手工填写 join_sql。")
    return out
