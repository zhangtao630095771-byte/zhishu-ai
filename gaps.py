# -*- coding: utf-8 -*-
"""
gaps.py —— 知识缺口扫描（把"发现幻觉"从被动变主动）

★ 为什么要主动扫缺口？
  "减少幻觉"的关键是补字段注释，但没人知道该补哪些 ——
  真实数仓一张表 210 个字段，逐个看一遍不现实。
  于是只能【踩到坑才补】：用户问错一个数 → 才发现 status 没说明枚举 → 补上。
  这种被动方式有两个问题：
    ① 坑已经踩了（用户已经拿到错数字，信任已经受损）
    ② 靠运气（没问到的地方一直是隐患）
  所以要把"发现缺口"变成主动的：自动扫出【最可能让模型算错】的字段，
  按优先级排好，人只需要从上往下补。

★ 什么字段最危险？
  【枚举型字段 + 注释没写取值含义】。
  模型看到 `status Int8` 注释只写"订单状态"，它不知道 6 是完成还是取消，
  就会按常识猜 —— 而业务枚举经常反直觉
  （本库 status=6 是【已完成】，7 是【已取消】；猜错的代价是数字全错还不报错）。

★ 为什么判断"是不是枚举"必须查真实数据，不能靠字段名/类型？
  实测：`cartype` 名字看着像枚举，实际有 65021 个不同值（它是车型 ID）；
  `is_invoice` 只有 1 个值（常量字段）。
  靠名字猜会扫出一堆假枚举，靠类型猜又会漏掉 String 枚举。
  【只有 distinct 值个数是可信的】—— 值少（≤ 30）才是枚举。

★ 已经配了数据字典的字段算【已覆盖】
  字典就是把"编码 → 含义"直接摆给模型看，和字段注释里的枚举说明是同一效果。
  重复报出来只会制造噪音，让人不想看这份清单。
"""
import config
import config_store
import db
import enum_parse          # ★ 共享的"从注释解析枚举含义"（结果表展示也用同一套）
import knowledge


# 名字像枚举的关键词（用来缩小扫描范围 —— 全表逐字段查 distinct 太重）
ENUM_NAME_HINTS = (
    "status", "state", "type", "flag", "source", "channel",
    "level", "grade", "kind", "category", "nature", "mode",
)

# 明显不可能当枚举的类型（金额、时间、大文本）——直接在候选阶段排掉，省一次查询
SKIP_TYPE_HINTS = ("Decimal", "Float", "DateTime", "Date", "UUID", "Array", "Map")

MAX_DISTINCT = 30           # distinct ≤ 30 才当枚举（值太多说明是 ID/名称，不是枚举）
BATCH = 20                  # 一次查询最多拼多少个 uniqExact（实测 12 个约 0.6s）


def _is_candidate(col, dtype):
    """这个字段值不值得去查 distinct（先粗筛，避免对 210 个字段全查）"""
    if any(s in dtype for s in SKIP_TYPE_HINTS):
        return False
    c = col.lower()
    return c.startswith("is_") or any(h in c for h in ENUM_NAME_HINTS)


def _mentions_values(comment):
    """
    注释里是否说明了【取值含义】（如"1内部司机，2普通司机"）

    ★ 实现挪到了共享模块 enum_parse，不再自己写一份正则。
      因为「结果表把编码 1 自动翻成中文」用的是【同一套解析规则】——
      两处各写一份，迟早会各说各话：缺口扫描说"这条注释写清楚了"，
      结果表却翻不出来，用户会觉得系统自相矛盾。
    """
    return enum_parse.mentions_values(comment)


def _dict_fields():
    """
    已经配了字典的字段 → {"表.字段": 字典名}（只算【已开放】的字典）

    ★ 为什么要看 enabled？
      停用的字典不进 RAG，等于没配。把它算成"已覆盖"会让缺口被漏报。
    """
    out = {}
    for d in config_store.get_dict_entries(enabled_only=False):
        f = (d.get("field_hint") or "").strip()
        if f and d.get("enabled") and d.get("items"):
            out[f] = d["name"]
    return out


def _query_distinct(table, cols):
    """一次查多个字段的 distinct 值个数 → {字段: 个数}"""
    if not cols:
        return {}
    exprs = ", ".join(f"uniqExact(`{c}`) AS c{i}" for i, c in enumerate(cols))
    sql = f"SELECT {exprs} FROM {config.CK_DATABASE}.{table}"
    _, rows = db.execute_sql(sql)
    return {c: int(v) for c, v in zip(cols, rows[0])}


def fetch_values(table, cols):
    """
    取若干字段的实际取值 → {字段: [值, ...]}（值少才调用，开销可控）

    ★ 为什么是全表 groupUniqArray 而不是 LIMIT 采样？
      曾经为了"怕慢"加过 LIMIT 前 2 万行，结果 ods_tu_customer_all.type
      采到 ['1'] 而全表其实是 ['1','3'] —— 字段被误判成"非枚举"，该补的注释没补。
      实测 469 万行的表全表 groupUniqArray 只要 0.24s（列式存储的优势），
      根本没有优化必要，加了采样反而制造错误。

    ★ 这个函数是【公开】的：suggest 生成字段注释时也调它，
      而不是自己再写一遍 SQL ——
      两处口径不一致的话，会出现"缺口页说这字段有 2 个值、AI 偏说只有 1 个"，
      用户会以为系统坏了。
    """
    if not cols:
        return {}
    exprs = ", ".join(f"groupUniqArray(`{c}`) AS c{i}" for i, c in enumerate(cols))
    sql = f"SELECT {exprs} FROM {config.CK_DATABASE}.{table}"
    _, rows = db.execute_sql(sql)
    out = {}
    for c, vals in zip(cols, rows[0]):
        try:
            vs = [str(v) for v in vals]
        except TypeError:
            vs = [str(vals)]
        # 数字型枚举按【数值】排序：纯字典序会把 10 排在 2 前面，看着别扭
        try:
            vs.sort(key=lambda x: (0, float(x)))
        except (ValueError, TypeError):
            vs.sort()
        out[c] = vs
    return out


def _visible_tables(tables=None):
    """本次要扫的表（默认：全部会进 RAG 的表）"""
    if tables is not None:
        return list(tables)
    business = knowledge.business_tables()
    return sorted(config_store.filter_visible_tables(
        set(business), config.ACL_DEFAULT_VISIBLE))


def scan_table_gaps(tables=None):
    """
    表级知识缺口 —— 表业务注释 / 业务实体（语义层 P2 登记）

    ★ 为什么要单扫这一层？它和字段级缺口管的不是一回事：
        字段级缺口（枚举没写取值）决定「这一列怎么算对」；
        表级缺口决定「这张表能不能被找对」——
          · 没有表注释  → 向量检索只能拿英文表名去匹配，业务俗称完全对不上
          · 没有业务实体→ 语义路由认不出"用户说的那个业务对象"是哪张主表
                         （表现就是"表明明开放了，却总是找不对表"）
      新授权一张表时这两样通常都没有，而它们恰恰是"这张表能不能用"的前提。

    ★ 返回的是"一行一个缺口"，不是"一行一张表"：
      同一张表可能既缺表注释又缺实体，拆开才能各自一键补、各自消失，
      合在一起会出现"补了注释这行还在"的困惑。
    """
    table_c = config_store.get_table_comments()
    table_src = config_store.get_table_comment_sources()   # {表: manual|ai}
    ent_by_table = {}
    for e in config_store.get_entities():
        ent_by_table.setdefault(e.get("table_name") or "", []).append(e)

    items = []
    for t in _visible_tables(tables):
        # ── ① 表业务注释 ──
        cmt = (table_c.get(t) or "").strip()
        if not cmt:
            items.append({
                "table": t, "kind": "table_comment", "severity": "danger",
                "title": "缺业务注释",
                "detail": "检索只能拿英文表名去匹配，业务俗称对不上 → 模型容易找错表或说没有",
                "current": "",
            })

        # ── ② 业务实体登记（P2）──
        ents = ent_by_table.get(t) or []
        if not ents:
            items.append({
                "table": t, "kind": "entity", "severity": "danger",
                "title": "缺业务实体",
                "detail": "语义路由认不出业务对象 → 用户用俗称提问时匹配不上这张主表",
                "current": "",
            })
        else:
            #   实体有了，但关键词为空 = 等于没登记（路由就是靠关键词/向量匹配的）
            kw = []
            for e in ents:
                kw += [str(k) for k in (e.get("keywords") or []) if str(k).strip()]
            if not kw:
                items.append({
                    "table": t, "kind": "entity", "severity": "incomplete",
                    "title": "实体缺业务俗称",
                    "detail": "实体没有关键词，路由只能靠实体名硬匹配，口语化提问会漏",
                    "current": "、".join(e["name"] for e in ents),
                })

    _rank = {"danger": 0, "incomplete": 1}
    items.sort(key=lambda x: (_rank.get(x["severity"], 3), x["table"], x["kind"]))
    return {
        "summary": {
            "tables": len(_visible_tables(tables)),
            "gap_tables": len({i["table"] for i in items}),
            "missing_comment": sum(1 for i in items if i["kind"] == "table_comment"),
            "missing_entity": sum(1 for i in items if i["kind"] == "entity"),
            "table_gaps": len(items),
        },
        "items": items,
    }


def scan(tables=None, with_values=True):
    """
    扫描知识缺口

    参数：
        tables      = 要扫的表（默认：全部会进 RAG 的表）
        with_values = 是否把枚举的实际取值也取回来（方便直接照着补注释）

    返回：{"summary": {...}, "items": [...]}
        items 已按【危险程度】排序，同一个级别里「值少的排前面」（补起来快）
    """
    if tables is None:
        business = knowledge.business_tables()
        tables = sorted(config_store.filter_visible_tables(
            set(business), config.ACL_DEFAULT_VISIBLE))

    table_c = config_store.get_table_comments()
    col_c = config_store.get_column_comments()
    col_s = config_store.get_column_comment_sources()   # {表.字段: manual|ai}
    db_c = db.fetch_comments(sorted(tables))
    db_table_c = db_c.get("tables", {})
    db_col_c = db_c.get("columns", {})
    dict_map = _dict_fields()

    items = []
    total_fields = enum_fields = 0

    for t in tables:
        all_cols = db.list_columns(t)
        total_fields += len(all_cols)

        # ── ① 粗筛：名字像枚举的字段 ──
        cands = [c for c, ty in all_cols if _is_candidate(c, ty)]

        # ── ② 查真实 distinct，判断哪些是真的枚举 ──
        distinct = {}
        for i in range(0, len(cands), BATCH):
            try:
                distinct.update(_query_distinct(t, cands[i:i + BATCH]))
            except Exception as e:
                print(f"⚠️ [{t}] 批量查 distinct 失败：{str(e)[:70]}")

        #   distinct ∈ (1, 30] 才算枚举：
        #   = 1 说明是常量字段（写不写条件都一样，没有歧义）
        #   > 30 说明是 ID/名称（如 cartype 65021 个值），不是枚举
        enums = [c for c in cands if 1 < distinct.get(c, 0) <= MAX_DISTINCT]
        enum_fields += len(enums)

        # ── ③ 取实际取值（只对真枚举取，值少、开销可控）──
        values = fetch_values(t, enums) if (with_values and enums) else {}

        # ── ④ 逐个判级 ──
        type_of = dict(all_cols)
        for c in enums:
            key = f"{t}.{c}"
            manual = col_c.get(key, "")
            from_db = db_col_c.get(key, "")
            comment = manual or from_db
            #   ★ 三态要分清楚：manual（人写的）/ ai（AI 补的）/ db（库自带的）。
            #     column_comments 里现在可能装着 AI 生成的结果，
            #     一律当 manual 会让用户分不清哪些经过人核实。
            source = (col_s.get(key) or "") if manual else ("db" if from_db else "")

            #   字典已经解决了"编码 → 含义"，和字段注释里的枚举说明是同一效果，
            #   重复报出来只是噪音，会让人不想看这份清单
            if c in dict_map:
                continue
            #   ★ 判定"说清楚了没"用真实取值逐字比对，而不是"注释长得像不像枚举说明"：
            #     后者会漏判字符串枚举（见 enum_parse.covers_values 里的例子）。
            #     values 拿不到时（with_values=False）才退回"至少两对数字编码"的老规则。
            vals = values.get(c, [])
            explained = (enum_parse.covers_values(comment, vals) if vals
                         else _mentions_values(comment))

            #   ★ 幽灵值（过期注释）：注释声称有、真实数据里【从来没出现过】的编码。
            #     和"漏了取值"是镜像的两面 ——
            #       漏了取值  → 模型只能猜，可能写出错的过滤条件
            #       幽灵值    → 模型照着写条件，查出【空结果】还以为"确实没有这种数据"
            #     后者更隐蔽：查询不报错、结果只是 0 行，用户毫无防备。
            #     这类只有【没人复查过的注释】才会出现 —— AI 生成时已被「不得编造」规则拦掉，
            #     所以这里扫出来的基本是库自带 / 人工写老的注释，正好是最该复查的。
            ghost = []
            if comment and vals:
                real_codes = set()
                for v in vals:
                    real_codes.update(enum_parse.components(str(v)))
                claimed = enum_parse.mentioned_codes(comment) - real_codes - {""}
                ghost = sorted(claimed, key=lambda x: (0, float(x)) if x.isdigit() else (1, x))

            #   完全说清楚、且没有幽灵值 → 过关，不报
            if explained and not ghost:
                continue

            #   ★ 级别优先级（谁更危险谁排前面）：
            #     danger  = 完全没注释 → 模型只知道字段名，全靠猜
            #     stale   = 注释给的是错的（声称有不存在的值）→ 模型会被直接带偏
            #     incomplete = 有注释但漏了值 → 知道字段是什么，部分值含义未知
            #     注：一条字段可能既漏值又含幽灵值，统一归 incomplete（漏值更基础），
            #        但 ghost 列表照样带出来，UI 上同时提示。
            if not comment:
                sev = "danger"
            elif explained:          # 取值都覆盖到了，问题只在幽灵值
                sev = "stale"
            else:
                sev = "incomplete"

            items.append({
                "table": t,
                "column": c,
                "type": type_of.get(c, ""),
                "distinct": distinct.get(c, 0),
                "values": values.get(c, []),
                "comment": comment,
                "comment_source": source,
                "ghost": ghost,
                "severity": sev,
            })

    # 排序：危险的在前（danger > stale > incomplete）；同级别里取值少的在前（先易后难）
    _rank = {"danger": 0, "stale": 1, "incomplete": 2}
    items.sort(key=lambda x: (_rank.get(x["severity"], 3), x["distinct"]))

    #   ★ 表级缺口（表注释 / 业务实体）一起返回 ——
    #     它只读配置库、不查 CK，几乎不花时间；而且用户看缺口时是
    #     "从粗到细"看的：先看哪张表根本还没搭好，再看哪一列要怎么补。
    tg = scan_table_gaps(tables)

    return {
        "summary": {
            "tables": len(tables),
            "total_fields": total_fields,
            "enum_fields": enum_fields,
            "danger": sum(1 for i in items if i["severity"] == "danger"),
            "stale": sum(1 for i in items if i["severity"] == "stale"),
            "incomplete": sum(1 for i in items if i["severity"] == "incomplete"),
            "gaps": len(items),
            #   表级缺口的计数（前端两个区块各显示各的）
            "table_gaps": tg["summary"]["table_gaps"],
            "missing_comment": tg["summary"]["missing_comment"],
            "missing_entity": tg["summary"]["missing_entity"],
        },
        "items": items,
        "table_items": tg["items"],
    }
