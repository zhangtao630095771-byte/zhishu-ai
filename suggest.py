# -*- coding: utf-8 -*-
"""
suggest.py —— 根据问题反馈，自动生成知识库优化建议

★ 它补的是哪一环？
  到这里，闭环的前半段已经齐了：
      「知识缺口」机器扫出该补哪里
      「问题反馈」人踩坑时记一笔
  但记录本身不会让系统变好 —— 得有人把它翻译成"该改哪条知识、改成什么样"。
  这一步以前全压在人的脑子里："车拖车被当成公司名了……那我该加条什么规则来着？"

  这个模块就是把这一步自动化：**读反馈 → 理解错在哪 → 给出可直接用的修改内容**。

★ 为什么必须让人确认，不能自动应用？
  ① 模型可能过度修改（把一条小问题上升成一条过宽的规则，反而误伤别的问题）
  ② 建议里常有业务语义判断（"6 到底是已完成还是已收货"），机器无权拍板
  ③ 知识库是要长期维护的资产，改动必须有人负责
  所以这里只【生成建议】，落地由人在页面上确认 / 修改后再应用。

★ 为什么优先建议改 prompt，而不是逐条补字段注释？
  一条 prompt 规则能防住【一整类】问题（"提到中文名时必须转成编码过滤"），
  而补一个字段注释只解决那一个字段。
  反馈反复出现同一类错，就说明缺的是规则，不是数据。
"""
import json
import re

import config
import config_store
import db
import enum_parse
import gaps
import guard
import llm


# 建议能落到哪些地方（和前端展示、应用逻辑一一对应）
KINDS = {
    "prompt":         "提示词（写 SQL 的规则 / 角色）",
    "column_comment": "字段业务注释",
    "metric":         "指标口径",
    "dict":           "数据字典",
}


def _collect_context(fbs):
    """
    收集这些反馈涉及的【知识现状】

    让模型知道"现在是什么"，才谈得上"改成什么"。
    """
    tables, metrics, dicts, columns = set(), set(), set(), set()
    for f in fbs:
        tables.update(f.get("tables") or [])
        metrics.update(f.get("metrics") or [])
        dicts.update(f.get("dicts") or [])
        #   ★ 一定要看 SQL 里【所有】用到的字段，不能只看命中的表/指标 ——
        #     答错的常常是 WHERE 里那个"用错的字段"（如 company_id），
        #     它既不在输出列里、也不在命中信息里，只有解析 SQL 才看得到。
        columns.update(guard.extract_columns(f.get("sql") or ""))

    lines = []

    # ── 提示词全文（模型必须看到现有规则，才知道"别重复"）──
    for key in ("role", "sql_rules"):
        txt = config_store.get_prompt(key)
        if txt:
            lines.append(f"### prompt: {key}\n{txt}")

    # ── 涉及字段的现有注释 ──
    field_lines = []
    for t in sorted(tables):
        try:
            manual = config_store.get_column_comments(t)
            dbc = db.fetch_comments([t]).get("columns", {})
            all_cols = [c for c, _ in db.list_columns(t)]
        except Exception:
            continue
        for c in all_cols:
            key = f"{t}.{c}"
            cmt = manual.get(key) or dbc.get(key) or ""
            #   只挑"相关的"：SQL 里用到的字段 + 带枚举含义的字段
            #   （把 210 个字段全塞进去，光上下文就上万 token，还会稀释重点）
            if c in columns or enum_parse.mentions_values(cmt):
                src = "人工" if manual.get(key) else ("库" if dbc.get(key) else "无")
                field_lines.append(f"{key}（{src}）：{cmt or '（无注释）'}")
    if field_lines:
        lines.append("### 涉及字段的现有注释\n" + "\n".join(field_lines[:80]))

    # ── 涉及指标的现有口径 ──
    all_metrics = {m["name"]: m for m in config_store.get_metrics(enabled_only=False)}
    m_lines = []
    for name in sorted(metrics):
        m = all_metrics.get(name)
        if m:
            m_lines.append(f"{name}：{m['definition']}")
    if m_lines:
        lines.append("### 涉及指标的现有口径\n" + "\n".join(m_lines))

    # ── 涉及字典的现有取值（只给条数，避免上下文过长）──
    d_lines = []
    for d in config_store.get_dict_entries(enabled_only=False):
        if d["name"] in dicts:
            d_lines.append(f"{d['name']}（适用字段 {d['field_hint']}，{len(d['items'])} 条取值）")
    if d_lines:
        lines.append("### 涉及的数据字典\n" + "\n".join(d_lines))

    return "\n\n".join(lines)


def _format_feedbacks(fbs):
    """把反馈整理成给模型看的样子"""
    out = []
    for f in fbs:
        out.append(
            f"### 案例 #{f['id']}\n"
            f"- 用户问题：{f['question']}\n"
            f"- 错误类型：{config_store.FEEDBACK_REASONS.get(f['reason'], f['reason'])}\n"
            f"- 用户说明：{f['detail']}\n"
            f"- 当时生成的 SQL：{f['sql'] or '（无）'}\n"
            f"- 当时命中：表 {f['tables'] or '无'} / 指标 {f['metrics'] or '无'} / 字典 {f['dicts'] or '无'}"
        )
    return "\n\n".join(out)


SYSTEM = """你是「知数AI」的知识库优化顾问。

用户会给你若干条【答错案例】和【当前知识库配置】，你要给出**最小必要**的优化建议。

可以优化的四类东西：
1. prompt          —— 写 SQL 的规则/角色。★ 优先级最高：一条规则能防住一整类问题
2. column_comment  —— 某个字段的业务注释（补枚举含义、单位、口径）
3. metric          —— 指标口径（聚合类问题的公式）
4. dict            —— 数据字典（编码 → 中文含义，适合跨表通用的编码）

必须遵守：
- **优先改 prompt**：如果几条案例错法相同，说明缺的是规则，不是数据
- **不要重复已有的规则**（下面给了现行规则全文，逐字对比）
- proposed 必须是【可直接使用的完整内容】，不能是"建议补充xxx"这种空话
  · kind=prompt 时：给出该 prompt 的【完整新版全文】（不是只给新增的那一句）
  · kind=column_comment 时：给出该字段的完整注释文本
  · kind=metric 时：给出完整的 SQL 口径表达式
- 改动要小、要具体，别顺手重写整段
- 如果某个案例不是知识能解决的（比如底层数据本身缺失），**不要硬给建议**
- 最多给 5 条建议，宁少勿滥

target 的写法：
- kind=prompt         → target 必须是 "role" 或 "sql_rules"
- kind=column_comment → target 写成 "表名.字段名"
- kind=metric         → target 是指标名
- kind=dict           → target 是字典名

输出：严格的 JSON 数组，不要任何解释文字、不要 ``` 包裹：
[
  {
    "kind": "prompt",
    "target": "sql_rules",
    "proposed": "（完整的新版内容）",
    "reason": "（一句话说明：为什么这么改，对应哪几条案例）",
    "feedback_ids": [1, 2]
  }
]
如果没有值得给的建议，输出空数组 []。"""


def _parse_json(raw):
    """从模型输出里抠出 JSON 数组（它经常会顺手包上 ```json）"""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    i, j = s.find("["), s.rfind("]")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    return json.loads(s)          # 解析失败就让调用方去接住（不在这里吞掉）


def generate(feedback_ids=None, max_suggestions=5):
    """
    生成优化建议

    参数：
        feedback_ids = 只针对这几条反馈生成（None = 全部待处理的）

    返回：{"suggestions": [...], "feedback_count": n, "message": "..."}
    """
    all_fb = config_store.get_feedbacks(status="pending")
    if feedback_ids:
        fbs = [f for f in all_fb if f["id"] in set(feedback_ids)]
    else:
        fbs = all_fb

    if not fbs:
        return {"suggestions": [], "feedback_count": 0,
                "message": "没有待处理的反馈。先去「取数」页用一用，答错了点「结果不对」记下来。"}

    context = _collect_context(fbs)
    user = (f"# 待处理的答错案例（共 {len(fbs)} 条）\n\n{_format_feedbacks(fbs)}\n\n"
            f"# 当前知识库配置\n\n{context}")

    print(f"[suggest] 用 {len(fbs)} 条反馈生成建议（上下文 {len(user)} 字符）…")
    raw = llm.chat(SYSTEM, user)

    try:
        items = _parse_json(raw)
    except Exception as e:
        #   ★ 不静默失败：把模型原始输出回传一小段，方便排查
        return {"suggestions": [], "feedback_count": len(fbs),
                "error": f"模型返回的不是合法 JSON：{str(e)[:120]}",
                "raw_preview": (raw or "")[:400]}

    if not isinstance(items, list):
        items = []

    out = []
    for i, it in enumerate(items[:max_suggestions]):
        kind = str(it.get("kind") or "").strip()
        target = str(it.get("target") or "").strip()
        proposed = str(it.get("proposed") or "").strip()
        if kind not in KINDS or not target or not proposed:
            continue                                  # 缺关键字段的建议直接丢掉
        fids = it.get("feedback_ids") or []
        out.append({
            "sid": f"s{i + 1}",
            "kind": kind,
            "kind_label": KINDS[kind],
            "target": target,
            "proposed": proposed,
            "reason": str(it.get("reason") or "").strip(),
            "feedback_ids": [int(x) for x in fids if str(x).isdigit()],
            "current": _current_value(kind, target),   # 现在的值（前端做对比展示）
        })

    return {
        "suggestions": out,
        "feedback_count": len(fbs),
        "message": f"已根据 {len(fbs)} 条反馈生成 {len(out)} 条建议 —— 请逐条确认或修改后再应用",
    }


def _current_value(kind, target):
    """取某个目标的当前值（前端用来做"改前 vs 改后"对比）"""
    try:
        if kind == "prompt":
            return config_store.get_prompt(target) or ""
        if kind == "column_comment":
            table, col = target.split(".", 1)
            manual = config_store.get_column_comments(table)
            dbc = db.fetch_comments([table]).get("columns", {})
            return manual.get(f"{table}.{col}") or dbc.get(f"{table}.{col}") or ""
        if kind == "metric":
            m = next((x for x in config_store.get_metrics(enabled_only=False)
                      if x["name"] == target), None)
            return m["definition"] if m else ""
        if kind == "dict":
            d = next((x for x in config_store.get_dict_entries(enabled_only=False)
                      if x["name"] == target), None)
            if d:
                return "、".join(f"{i['value']}={i['label']}" for i in d["items"][:20])
    except Exception:
        pass
    return ""


def apply_suggestion(kind, target, value, feedback_ids=None, note=""):
    """
    应用一条建议（由人在页面上确认/修改后调用）

    ★ 为什么要把应用和生成分开？
      生成是"机器提议"，应用是"人拍板"。分开之后，
      责任清晰：改错了是人改的，不是机器偷偷改的。
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("内容不能为空")

    if kind == "prompt":
        if target not in ("role", "sql_rules"):
            raise ValueError(f"未知的 prompt 键：{target}")
        config_store.set_prompt(target, value)

    elif kind == "column_comment":
        if "." not in target:
            raise ValueError(f"字段 target 要写成「表名.字段名」，收到：{target}")
        table, col = target.split(".", 1)
        config_store.set_column_comment(table, col, value)

    elif kind == "metric":
        old = next((x for x in config_store.get_metrics(enabled_only=False)
                    if x["name"] == target), None)
        if not old:
            raise ValueError(f"没找到指标：{target}（新指标请去「指标信息」里建）")
        config_store.upsert_metric({
            "name": target,
            "keywords": old["keywords"],
            "definition": value,
            "doc": old["doc"],
            "tables": old["tables"],
        })

    else:
        raise ValueError(f"这类建议暂不支持自动应用（{kind}）—— 请去对应模块手工处理")

    # ── 把来源反馈标记为已处理 ──
    #   ★ 必须留痕：只把状态改掉，过两个月没人记得当时改了什么。
    marked = []
    for fid in (feedback_ids or []):
        n = note or f"由建议应用：改了 {KINDS.get(kind, kind)}「{target}」"
        if config_store.update_feedback(int(fid), status="resolved", note=n):
            marked.append(int(fid))

    return {"ok": True, "kind": kind, "target": target, "marked_feedback": marked}


# ═══════════════════════════════════════════════════════════
# ② 根据字段清单，起草「表业务注释」
# ═══════════════════════════════════════════════════════════
#  ★ 为什么这件事适合交给 AI？
#    表注释决定"模型能不能找对表"，但它得通读几百个字段才写得出来 ——
#    人做这件事性价比极低（一张表 210 个字段，看完小半小时），
#    而 AI 扫一遍字段清单就能抓住"这张表到底存的是什么"。
#
#  ★ 为什么只"起草"、坚持让人改？
#    ① 表注释是给业务同事看的，措辞和口径得有人把关
#    ② AI 只看得到字段清单，看不到这张表在数仓里的上下游定位
#       （同一批字段可能出现在 ODS 明细层和 DWD 汇总层，用途并不一样）
#    所以它给草稿，人给判断 —— 和"优化建议"是同一套原则。
TABLE_DOC_SYSTEM = """你是数据仓库的文档助手。

用户会给你一张表的【表名 + 字段清单（字段名、类型、注释）】。
请你推断这张表存的是什么业务数据，写一句表业务注释。

必须遵守：
- 一句话，25~60 字
- 说清两件事：这是什么数据（业务实体）+ 关键的维度或指标
- **不要罗列字段名**（把字段名堆进去等于没写）
- 不要用「该表包含」「本表用于存储」「这是一张」「这是一份」这类废话开头 ——
  直接说业务实体（如「车辆运输订单全流程明细，记录…」）
- **只依据字段清单，不要编造清单里没有的信息**（比如别猜它属于哪个业务线）
- 如果字段注释里已经写了枚举含义，不要把它们抄进来，那是字段注释该干的事
- ★ 字段清单里可能混着【串味】注释（某个字段的说明其实属于另一张表，
  比如询价表里的 carstate 被写成了订单流转状态）。
  判断依据看【大多数字段】：以字段主体的业务为准，
  个别字段的描述与整体业务明显不搭时，**不要被它带偏**，也别把它写进表注释

直接输出那一句话，不要引号、不要解释、不要 markdown、不要换行。"""


def generate_table_comment(table, max_fields=300):
    """
    根据表的字段清单生成推荐的表业务注释

    返回：{"table": ..., "suggested": "...", "field_count": n}
    """
    cols = db.list_columns(table)
    if not cols:
        raise ValueError(f"表 {table} 没有字段，无法推断用途")

    manual = config_store.get_column_comments(table)
    db_cols = db.fetch_comments([table]).get("columns", {})

    #   字段清单就是全部依据，所以尽量给全（超长时截断，避免上下文过载）
    lines = []
    for c, ty in cols[:max_fields]:
        cmt = manual.get(f"{table}.{c}") or db_cols.get(f"{table}.{c}") or ""
        lines.append(f"- {c} ({ty}) {cmt}".rstrip())

    user = (f"表名：{table}\n字段数：{len(cols)}\n\n字段清单：\n" + "\n".join(lines))
    print(f"[suggest] 为 {table} 起草表注释（{len(lines)} 个字段，{len(user)} 字符）…")
    out = llm.chat(TABLE_DOC_SYSTEM, user)

    #   模型偶尔会顺手加引号、加"推荐注释："前缀或换行解释 —— 统一清理干净
    txt = (out or "").strip()
    txt = txt.split("\n")[0].strip()
    for ch in ('"', "'", "「", "」", "《", "》"):
        txt = txt.strip(ch)
    for prefix in ("推荐注释：", "推荐的表业务注释：", "表注释：", "建议："):
        if txt.startswith(prefix):
            txt = txt[len(prefix):].strip()

    #   ★ 直接写进 table_comments（source='ai'）—— 为什么？
    #     用户要的是"白名单时生成一份【临时生效】的表注释，供 RAG 用，可修改"。
    #     所以它不是"草稿"，而是"生效中的临时注释"：
    #       · 立刻进 RAG（模型马上能用这张表的中文描述）
    #       · 标 source='ai'，页面会提示"AI 生成，可修改"
    #       · 用户一旦改过 → source 变 'manual'，AI 下次就不再覆盖它
    if txt:
        config_store.set_table_comment(table, txt, source="ai")

    return {"table": table, "suggested": txt, "field_count": len(cols)}


# ═══════════════════════════════════════════════════════════
# ②-b 根据表结构，起草「业务实体」登记（语义层 P2）
# ═══════════════════════════════════════════════════════════
#
#  ★ 为什么授权新表时要顺手起草实体？
#    「语义路由」认表靠的是实体注册表（业务俗称 → 权威主表）。
#    新开放一张表却没人登记实体，后果是：
#      模型知道"库里有这张表"，但用户用业务俗称（"代驾单""小板订单"）提问时匹配不上
#      → 表现就是"表明明开放了，却总是找不对表 / 干脆说没有"。
#    而登记实体需要的输入（这表是什么业务、粒度是一行一个什么）恰好就在字段清单里。
#    授权这一步是用户唯一会主动碰"这张表"的时刻，此刻起草最省事 ——
#    和表注释同一套逻辑。
#
#  ★ 它输出的是【临时生效】的草稿，不是"待办"：
#     写进 entities(source='ai') → 立刻进向量索引，立刻能被业务俗称命中；
#     人一旦改过 → source 变 'manual' → AI 永远不再覆盖（护栏在 upsert_entity）。
#    否则人精心写的关键词每次授权都被冲掉，很快就会没人愿意去改。
ENTITY_SYSTEM = """你是数据仓库的「语义层」登记助手。

用户会给你一张表的【表名 + 已有业务注释 + 字段清单】。
请你把它登记成一个【业务实体】，供自然语言取数系统做语义路由。

严格按 JSON 输出，不要 markdown、不要解释：
{
  "name": "业务实体名（中文，4~10 字，是业务对象的名字，不是表名）",
  "grain": "粒度：这个实体的一行代表什么（如「一行一个订单」）",
  "comment": "一句话业务说明，20~50 字",
  "keywords": ["业务俗称1", "业务俗称2", "业务俗称3"]
}

要求：
- name 写【业务对象】而不是表：如「代驾订单」「大板订单」「客户」「司机」，
  ★ 不要带「表」字（"代驾订单表"是表名不是实体名），不要照抄英文字段
- grain 必须能从字段里推断出来（有单号/订单号 → 一行一个订单）；推不出来就留空字符串
- keywords 给 3~6 个：业务同学平时会怎么叫它（含简称、口语、可能的英文），
  ★ 这些词是用来【匹配用户提问】的，所以要写"用户会打的词"，不是"表里的字段名"
- ★ 只依据给的字段清单和注释，**不要编造**你没有看到的信息
  （比如看不出它属于哪个业务线，就不要瞎猜业务线名）
- 拿不准就保守：name 用最直白的业务叫法，keywords 少而准，别凑数"""


def _parse_json_obj(raw):
    """从模型输出里抠出 JSON 对象（它经常会顺手包上 ```json）"""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    return json.loads(s)


def generate_entity(table, max_fields=200):
    """
    根据表结构起草一个「业务实体」登记，并写入（source='ai'）。

    返回：{"table","ok","entity","reason"}
      · ok=False 不代表出错 —— 可能是"已有人工登记的实体，AI 不覆盖"，
        这属于正常保护，前端应照实显示而不是报错。
    """
    cols = db.list_columns(table)
    if not cols:
        raise ValueError(f"表 {table} 没有字段，无法推断业务实体")

    table_comment = config_store.get_table_comments().get(table, "")
    manual = config_store.get_column_comments(table)
    db_cols = db.fetch_comments([table]).get("columns", {})

    lines = []
    for c, ty in cols[:max_fields]:
        cmt = manual.get(f"{table}.{c}") or db_cols.get(f"{table}.{c}") or ""
        lines.append(f"- {c} ({ty}) {cmt}".rstrip())

    user = (f"表名：{table}\n"
            f"已有业务注释：{table_comment or '（无）'}\n"
            f"字段数：{len(cols)}\n\n字段清单：\n" + "\n".join(lines))
    print(f"[suggest] 为 {table} 起草业务实体（{len(lines)} 个字段）…")
    try:
        parsed = _parse_json_obj(llm.chat(ENTITY_SYSTEM, user))
    except Exception as e:
        return {"table": table, "ok": False,
                "reason": f"模型返回的不是合法 JSON：{str(e)[:100]}"}

    name = str(parsed.get("name") or "").strip()
    kws = parsed.get("keywords") or []
    if isinstance(kws, str):
        kws = [kws]
    kws = [str(k).strip() for k in kws if str(k).strip()][:8]
    if not name:
        return {"table": table, "ok": False, "reason": "模型没给出实体名"}

    #   ★ 同一张表已有 AI 版实体时就地更新，沿用【原实体名】：
    #     否则每授权一次就可能换个名字，同一张表堆出好几个实体（库里已经出现过
    #     "代驾订单" 和 "代驾订单表" 都指向 ods_valet_order_all 的情况）。
    existing = config_store.get_entity_by_table(table)
    if existing and existing.get("source") == "manual":
        return {"table": table, "ok": False,
                "reason": f"已有人工登记的实体「{existing['name']}」，AI 不覆盖",
                "entity": existing}
    if existing:
        name = existing["name"]
    if name in kws:                      # 实体名本身也该是召回词
        pass
    else:
        kws.insert(0, name)

    ent = {"name": name, "table_name": table,
           "grain": str(parsed.get("grain") or "").strip(),
           "comment": str(parsed.get("comment") or "").strip(),
           "keywords": kws}
    ok, why = config_store.upsert_entity(ent, source="ai")
    return {"table": table, "ok": ok, "entity": ent, "reason": why}


# ═══════════════════════════════════════════════════════════
# 字段注释：拿着【真实取值】去问模型，而不是让它凭字段名猜
# ═══════════════════════════════════════════════════════════
#
# ★ 为什么这件事比补表注释更值钱？
#   两类幻觉的代价完全不同：
#     找不到表  → 直接答不上来，或者答"没有相关表"  ← 用户立刻知道不对
#     搞错枚举  → SQL 能跑、数字出来了、但是错的    ← 静默算错，最要命
#   本库 98 个枚举字段里有 17 条说不清取值，就是 17 个静默算错的口子。
#
# ★ 为什么这活儿 AI 干得了？（关键在于有 grounding）
#   单看字段名 is_picked，谁也不知道 0 和 1 哪个是"是"。
#   但把【真实取值清单】一起给模型，它做的就不是"猜"，而是"归纳已知事实"：
#     is_picked  取值 [0, 1]       → 是否有司机接单（0=未接单，1=已接单）
#     insure_status 取值 [0,1,2,3] → 保险状态（0=未投保，1=已投保，2=已失效，3=已理赔）
#   同样的 boundaries 也适用于字典型编码字段（101/503 这种）。
#
# ★ 安全底线（比生成能力更重要）：
#   ① 原注释的业务描述必须保留 —— 不能把"是否有司机接单"换成"是否接单标记"
#      （表面没差别，但对已经熟悉这张表的人是一种信息磨损）
#   ② 不得编造取值清单里没有的码值
#   ③ **每个取值都必须在注释里出现**，少一个就判失败
#      理由：只说 1=xx、漏掉 3，模型遇到 3 照样瞎猜 —— 等于没补，还制造了"已补"的错觉
#   ④ 拿不准就返回空，留着让人补 —— 补一条假的比留一条空的有害得多

#   ── 这个阈值是实测踩出来的，不是拍脑袋：
#   取值 2~4 个（0/1 标记、是否类）→ AI 几乎不会错，二值语义本来就没多少可能性
#   取值 5~8 个                     → 可能个别叫法不准，但主体可信，值得自动补
#   取值 15+ 个（实测踩的坑）：
#       cartype_code  16 个值 → AI 编出「0=经济型 1=舒适型 2=豪华型…15=未知」
#       activity_source 25 个值 → AI 编出一整套「8=渠道推广 9=广告投放…」
#       这些翻译看着专业、格式完整、每个值都覆盖 —— 而实际是编的。
#     这类错误最危险：它不像"空注释"那样让人警惕，反而让人以为已经搞定了，
#     模型照着写出 cartype_code=0 以为是查"经济型"，结果全错还不知道错在哪。
#   → 宁可留空让人手动补，也不放一条看起来完美的假知识进去。
MAX_AUTO_VALUES = 8

COL_DOC_SYSTEM = """你是数据仓库的文档助手。

用户给你【一个字段】的信息：**表名、表业务注释**、字段名、类型、现有注释、
**这个字段在真实数据里出现过的全部取值**。
请你写一句字段业务注释，让看注释的人（包括大模型）能正确理解每一个取值的含义。

必须遵守：
1. **必须覆盖取值清单里的【每一个】值**，一个都不能漏。
   漏掉的值等于让模型继续瞎猜 —— 那不如不写。
2. **不得编造清单里没有的取值**（比如清单只有 0/1，就绝不能写 2=xxx）。
3. 如果【现有注释】有内容，**必须保留它的业务描述**，在后面追加取值说明。
   不要重写、不要"优化"措辞 —— 熟悉这张表的人要靠原话认字段。
   允许删掉明显的重复和口语冗余（如「是否有司机接单,有司机接单的订单量，接单订单」
   → 保留「是否有司机接单」，去掉后面堆砌的重复）。
4. 如果【现有注释】为空，你根据字段名推断业务含义；推断不出来就只写取值说明。
5. 取值含义要写得像业务同事说的话，不要写"值为1时表示"这种技术腔。
6. 一句话，15~60 字。格式：业务描述（0=xx，1=xx）或多值情形：业务描述（0=xx，1=xx，2=xx）

★ 7. 【同名 ≠ 同义】这是最容易出错、也最不容易被发现的一条。
   不同表里字段名完全有可能一模一样，含义却完全不同。本库真实案例：
     carstate 在订单/运单表里是「订单流转状态」（2待接单 3已接单…7已取消），
     而在询价表 ods_tu_order_pricing_record_all 里是「车况」（能否启动）。
   曾经有人把订单表那套状态码整套抄到询价表上，注释看着完整、格式漂亮，
   但每一个值的含义都是错的 —— SQL 照它生成，结果全错还不报错。
   所以：
   · 只能依据【本表业务注释 + 本字段真实取值 + 字段名】这三样来写；
   · 严禁套用你在其它表见过的同名/近名字段的枚举；
   · 【表业务注释】是判断本表干什么的第一依据：
     若字段名看起来像"状态"，但本表业务是"询价记录"，那它更可能是车况/配置，而不是订单流转。

★ 8. 【现有注释可能本身就是串味的】如果【现有注释】里的编码含义与本表业务、
   或与真实取值**明显冲突**（典型：注释里出现了本表数据根本不存在的大批编码），
   就判定它的枚举部分不可信：**丢掉枚举，只保留业务描述**，取值说明按真实取值重写。
   若连业务描述都推断不出，直接输出空字符串 —— 宁可空着，也不要贴一条假知识。

直接输出那一句注释，不要引号、不要解释、不要 markdown、不要换行。"""


#   注释里"括号包着的枚举段"，如「车况（1能启动，2不能启动）」里的括号部分。
#   只认括号内出现「数字=」形态的段，避免把「（单位：元）」这类说明误剥掉。
_RE_ENUM_GROUP = re.compile(r"[（(][^（）()]*\d\s*[=:：][^（）()]*[）)]")


def _strip_enum_parts(comment):
    """
    剥掉注释里的枚举段，只留业务描述

    ★ 为什么要"剥"而不是"整条丢弃"？
      串味的注释里，脏的往往只是后面那串编码，前面的业务描述
      （如「车况」）经常是对的。整条丢掉等于把人对的部分也一起扔了；
      剥壳能保住有效信息、只换掉错的那一半。
    """
    out = comment or ""
    prev = None
    while prev != out:                 # 可能有好几个括号段，剥到稳定为止
        prev = out
        out = _RE_ENUM_GROUP.sub("", out)
    return out.strip(" ，,、;；。")


def _is_num(s):
    """能不能当数字看（用于编造取值列表的排序）"""
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _real_values(table, column, max_values=31):
    """
    取字段在真实数据里的全部取值

    ★ 直接复用 gaps.fetch_values —— 和「知识缺口」页展示给用户看的是同一份数据。
      两边口径必须一致，否则会出现"页面上写这个字段有 1/3 两个值，
      AI 却说它只有一个值不用补"的荒唐局面。
    """
    return gaps.fetch_values(table, [column]).get(column, [])


def generate_column_comment(table, column, overwrite=False):
    """
    根据【字段名 + 现有注释 + 真实取值】生成一个字段的业务注释，直接写进
    column_comments(source='ai')。

    参数 overwrite：
        False（默认）→ 遇到 source='manual' 的字段【拒绝并退回】，不动人的东西
        True         → 允许改写人工注释（用户在某一条上明确确认过才传）

    ★ 为什么默认要保护人工版？
      批量补全跑一次可能涉及几十个字段，其中偶尔混着人一条条手写的。
      AI 就算"保留了业务描述"，也免不了改写措辞 —— 而一旦写回，source 就从
      manual 降级成 ai。这种大范围、不易察觉的降级，用户往往事后才发现。
      所以批量默认不碰人的东西；真要动，由人在具体某条上点头（前端会二次确认）。
      和表注释"manual 永不覆盖"是同一套规矩，别搞两套标准。

    返回：{"table":..., "column":..., "suggested":..., "values":[...], "ok":bool, "reason":...}

    ★ ok=False 的几种情况（都是"宁可不补，不要补错"）：
        · 这条是人工注释且没授权覆盖
        · 字段不是枚举（取值太多或只有一个值）
        · 模型返回空 / 输出被判定为废话
        · **生成的注释漏了某个取值、或编造了不存在的取值**（安全底线）
    """
    type_of = dict(db.list_columns(table))
    if column not in type_of:
        raise ValueError(f"表 {table} 没有字段 {column}")

    manual = config_store.get_column_comments(table)
    db_col_c = db.fetch_comments([table]).get("columns", {})
    cur_comment = manual.get(f"{table}.{column}") or db_col_c.get(f"{table}.{column}") or ""

    #   ★ 人工版保护：批量跑的时候遇到就得收手
    if manual.get(f"{table}.{column}") and not overwrite:
        return {"table": table, "column": column, "suggested": "", "values": [],
                "ok": False, "reason": "这条注释是你手写的，批量补全不自动改",
                "advice": "manual"}

    values = _real_values(table, column)
    #   ≤1 个值 → 常量字段，写不写条件都一样，补了没意义
    #   >30 → 它是 ID/名称不是枚举（gaps 里同一个判断口径）
    if len(values) <= 1:
        return {"table": table, "column": column, "suggested": "", "values": values,
                "ok": False, "reason": "只有一个取值，不是枚举"}
    if len(values) > MAX_AUTO_VALUES:
        #   ★ 不是"AI 补不出来"，而是"AI 补出来的不可信"：
        #     16 个值的 cartype_code 它能全套编出来，而且编得像真的。
        #     取值多的字段本来就该用数据字典管 —— 字典是从库里的字典表导入的真实数据，
        #     不是推断出来的。这里明确指路，别让人误以为这条已经没办法了。
        return {"table": table, "column": column, "suggested": "", "values": values,
                "ok": False, "reason": f"取值 {len(values)} 个（超过 {MAX_AUTO_VALUES}），"
                                       f"自动补全容易编错，建议配数据字典",
                "advice": "dict"}

    # ── ★ 串味预检（确定性，不靠模型自觉）──
    #   判据：现有注释里"声称存在"的数字编码，有多少在本表真实数据里根本不存在。
    #   注释说要 1~7 七个状态码、而本表实测只有 0 和 1 —— 那不是"注释写错了一个值"，
    #   那是一整套别的表的编码被搬过来了（carstate 踩过的就是这个）。
    #   ★ 为什么这类判断必须写在代码里？
    #     光在 Prompt 里叮嘱"别串味"，模型看到一段完整的枚举说明，
    #     天然倾向"照着它补全"——它不知道这是抄来的。
    #     把"本表根本没有这些码"这个事实摆到它面前，才拦得住。
    real_codes = set()                        # 本表真实存在的原子编码
    for v in values:
        real_codes.update(enum_parse.components(str(v)))
    claimed = enum_parse.mentioned_codes(cur_comment)   # 现有注释声称存在的编码
    ghost = sorted((claimed - real_codes) - {""},
                   key=lambda x: (0, float(x)) if _is_num(x) else (1, x))
    contaminated = bool(ghost)
    if contaminated:
        kept = _strip_enum_parts(cur_comment)           # 只留业务描述，枚举整段剥掉
        print(f"⚠️ [{table}.{column}] 现有注释声称的编码 {ghost} 在本表数据里不存在，"
              f"判定为跨表串味：丢弃其枚举段，仅保留业务描述「{kept or '(空)'}」")

    #   取值清单里可能有【空字符串】（如 ex_mch_channel 有 '' 和 'alipay'）。
    #   两个坑必须避开：
    #     ① 直接把 '' 塞进清单，模型看不出那是一项，会以为只有 alipay 一个值
    #     ② 待会儿校验"每个值是否出现在注释里"时，'' in 任意文本 恒为 True，
    #        空值会被误判成已经覆盖 —— 这道防线就形同虚设
    #   所以这里显示成「（空）」让模型看得见，校验时按 seen values 的区别处理。
    shown = ["（空）" if v == "" else v for v in values]
    #   ★ 表业务注释是"本表干什么"的第一依据：判断某个字段名在本表
    #     到底是订单状态还是车况，全靠它。以前没传，等于让模型只看字段名猜。
    table_doc = config_store.get_table_comments().get(table, "")
    user = (
        f"表名：{table}\n"
        f"表业务注释：{table_doc or '（无）'}\n"
        f"字段名：{column}\n"
        f"类型：{type_of[column]}\n"
        f"现有注释：{cur_comment or '（无）'}\n"
        f"真实取值（共 {len(values)} 个）：{', '.join(shown)}"
    )
    if contaminated:
        #   把"为什么不可信"讲清楚，而不是只说一句"别信"——
        #   模型需要知道冲突点在哪，才不会一边被告知不可信、一边又把它抄回去。
        user += (
            f"\n★ 预检结论：现有注释里的编码 {', '.join(ghost)} 在本表真实数据里不存在，"
            f"说明这段枚举是从别的表串过来的，**不可采信**。"
            f"只保留其中的业务描述「{_strip_enum_parts(cur_comment) or '（无）'}」，"
            f"取值说明必须完全按上面的真实取值重写。"
        )
    print(f"[suggest] 补全 {table}.{column}（{len(values)} 个取值）…")
    out = llm.chat(COL_DOC_SYSTEM, user)

    txt = (out or "").strip()
    txt = txt.split("\n")[0].strip()
    for ch in ('"', "'", "「", "」", "《", "》"):
        txt = txt.strip(ch)
    for prefix in ("字段注释：", "注释：", "建议：", "推荐注释："):
        if txt.startswith(prefix):
            txt = txt[len(prefix):].strip()

    if not txt:
        return {"table": table, "column": column, "suggested": "", "values": values,
                "ok": False, "reason": "模型没返回内容"}

    # ── ★ 安全底线 ③：每个取值都必须在注释里出现 ──
    #   这是补救措施里最关键的一道。只说了 1=xx 却漏了 3，
    #   模型遇到 3 还是瞎猜 —— 补了跟没补一样，还会让人误以为已经覆盖了。
    #   ★ 反过来还要查【编造】：注释里出现了取值清单里没有的码值。
    #     漏取值只是"没补上"，编造更危险 —— 模型会照着写一个 type=2 的条件，
    #     查出一堆空结果还以为是数据没有，错了都不知道自己错了。
    parsed = enum_parse.parse_values(txt)
    real_set = set(values)
    invented = sorted(set(parsed) - real_set - {""}, key=lambda x: (0, float(x))
                      if _is_num(x) else (1, x))
    if invented:
        print(f"⚠️ [{table}.{column}] 生成结果编造了不存在的取值 {invented}，判失败不写入")
        return {"table": table, "column": column, "suggested": "", "values": values,
                "ok": False, "reason": f"编造了不存在的取值 {', '.join(invented)}"}

    #   ★ 复用 enum_parse.covers_values —— 它和「知识缺口扫描」用的是同一份判据。
    #     分成两处写会自相矛盾：AI 生成时判及格、落库后缺口页又说这是条缺口
    #     （真实发生过：字符串枚举 ex_mch_channel 的「空/alipay」），
    #     用户会以为系统坏了。
    #     空字符串在里面单独处理：'' in 任意文本恒为 True，必须要求注释出现「空」字。
    if not enum_parse.covers_values(txt, values):
        lack = [v for v in values if v != "" and v not in txt]
        if lack:
            reason = f"漏了取值 {', '.join(lack)}"
        else:
            reason = "未说明空取值的含义"
        print(f"⚠️ [{table}.{column}] 生成结果没覆盖全部取值（{reason}），判失败不写入")
        return {"table": table, "column": column, "suggested": "", "values": values,
                "ok": False, "reason": reason}

    #   ★ 和表注释同一套语义：AI 生成即进 column_comments(source='ai')，
    #     立刻参与 RAG；用户改过 → 保存时 source 变 manual，永不被覆盖。
    config_store.set_column_comment(table, column, txt, source="ai")
    return {"table": table, "column": column, "suggested": txt,
            "values": values, "ok": True, "reason": ""}
