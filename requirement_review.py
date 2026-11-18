# -*- coding: utf-8 -*-
"""
requirement_review.py —— 需求一致性审核（第六维审核）

★ 它解决什么问题？
    现有审核层（sql_validator 五维）只判断「这条 SQL 合不合法」：
    表/字段存不存在、口径对不对、JOIN 会不会放大、只读不安全。
    但它从来不判断「这条 SQL 回答的是不是用户这次问的那个问题」。
    于是三种高频错误可以静默通过：
      ① 用户说"只看 101"，模型写成 `app_entity_source = 0 OR = 101`（替换变追加）；
      ② 用户说"去掉应用主体分组"，模型顺手把 `app_entity_source = 0` 过滤也删了；
      ③ 用户要"按月份"，模型吐了一行汇总（漏了 GROUP BY）。
    本模块就是把这类「合题意」判断补上。

★ 它的边界（非常重要，三句话）：
    1. 只判定，不改写。绝不拼接/改写 SQL —— 修正永远由模型重新生成（保留模型自主性）。
    2. 只报问题，不执行。本模块不 import db、不碰任何执行入口。
    3. 排在安全闸门【之后】。需求类问题绝不能成为放行越权 SQL 的理由。

★ 两层结构：
    确定性层（本文件 P0 已实现）：需求契约 + 8 条 AST/正则规则，零 LLM 成本、可单测；
    语义层（P2 实现）：把契约 + SQL + 经验摘要交给模型判"意图是否读对"，输出
                      verdict=pass/fail/unsure。LLM 超时必须标 skipped，不得当 pass。

★ 返回结构与 sql_validator 保持同构：issues = [{severity, tier, rule, message, suggestion}]
    —— 这样 agent 现有的"回灌修复"逻辑可以直接复用，不需要新写一套。
"""
import hashlib
import json
import re

import config
import guard

# 契约结构版本：以后加字段时，落库的历史记录能靠它判断"当时是怎么算的"
CONTRACT_VERSION = 1

# ── 子句边界词 ──
#   ★ 为什么要 `format` / `settings`？ClickHouse 的 SQL 末尾常带
#     `FORMAT TabSeparatedWithNames`，它不是查询体的一部分；若不当作边界，
#     ORDER BY / GROUP BY 的截取会把这个尾巴一并吞进来，干扰后续判断。
_CLAUSE_KEYWORDS = ("where", "group by", "order by", "having", "limit",
                    "union", "format", "settings")

# ── 日期类维度的同义词 ──
#   ★ 用户说"按月"，SQL 里写的是 toStartOfMonth(create_time)、别名可能是"统计日期"。
#     纯字符串匹配会误报"维度缺失"，所以只要分组表达式是日期函数，就认为日期类维度已落实。
_DATE_DIM_WORDS = ("月", "日", "天", "周", "年", "季度", "date", "day", "week",
                   "month", "year", "hour")

# ── 修改动词表（判定 operation，不调模型）──
#   ★ 顺序有意义：先判"替换"，再判"删除"，最后判"追加"。
#     因为"不要按月份了，按周统计"同时含"不要"和"按周"，语义是【删除月份维度】；
#     而"把注册客户数改成注册司机数"含"改成"，语义是【替换指标】。
_RE_PLACE_VERBS = re.compile(r"(换成|换为|改为|改成|调整为|变成|只看|只保留|只统计|只要)")
_RE_REMOVE_VERBS = re.compile(r"(去掉|去除|不要|删掉|删除|取消|移除|不再|不看|拿掉|不顾|只按|仅按|只以|仅以)")
_RE_ADD_VERBS = re.compile(r"(加上|再加|增加|新增|添加|补充|还要|也要|追加|同时看)")

# ── "只按 X" 这类收敛表达 ──
#   ★ 语义陷阱：用户说"只按月份统计"，被删掉的是【应用主体】——它根本没出现在问句里。
#     如果只按"问句里点名的维度"来判 must_remove，就会漏判，
#     进而把"只按月份"误报成"应用主体维度丢失"（把正确的 SQL 拦下来）。
#     所以这类表达要反向处理：问句里【没点名】的维度，才是要删的。
_RE_ONLY_DIM = re.compile(r"(只按|仅按|只以|仅以|只保留|只统计|仅统计)")

# ── 删除动词（指向"它后面那个对象"）──
#   ★ 为什么必须与 _RE_ONLY_DIM 分开？
#     "去掉应用主体" = 删应用主体（动词指向后面）；
#     "只按月份"     = 删"月份之外"的维度（词义相反，月份要留）。
#     如果混在一张表里，会出现"用户说只按月份，系统却把月份当成要删的"这种反向误判。
_RE_DROP_NEAR = re.compile(r"(去掉|去除|不要|删掉|删除|取消|移除|拿掉|不看|不再|别按|别用)")


# ═══════════════════════════════════════════════════════════
# ① 基础文本工具（全部纯函数，不依赖数据库）
# ═══════════════════════════════════════════════════════════
def _squash(x):
    """压平文本：去掉所有空白并转小写。

    ★ 为什么不用简单的 `in`？
      `status = 6` 与 `status=6` 是同一件事，模型两种写法都常见。
      统一压平后再比较，能避免"因为多了个空格就判不通过"。
    """
    return re.sub(r"\s+", "", str(x or "")).lower()


def strip_comments(sql):
    """剥掉 SQL 里的 `--` 行注释与 `/* */` 块注释，字符串字面量内的不动。

    ★ 为什么这件事必须统一做？（真实踩过的坑）
      `eval_modify_live.py` 判"字段名是否改对"时用的是 `"错误字段" not in sql`，
      模型在注释里写了一行 `-- 把 app_entily_source 改成 app_entity_source`，
      正文其实完全正确，却被判成失败。同一坑在 agent._strip_leading_comments 也踩过一次。
      本模块所有判断一律基于"剥注释后的正文"，从源头堵住这类假阳性/假阴性。
    """
    s = str(sql or "")
    out = []                     # 逐字符收集结果
    i, n = 0, len(s)             # 游标与总长
    quote = ""                   # 当前所处的引号（' " `），空串表示不在引号内
    while i < n:
        ch = s[i]
        if quote:
            out.append(ch)
            if ch == quote:
                # 连续两个引号是转义（'' 或 ""），要整体跳过，不能当成结束
                if i + 1 < n and s[i + 1] == quote:
                    out.append(s[i + 1])
                    i += 2
                    continue
                quote = ""       # 引号闭合
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch           # 进入引号，后续内容原样保留
            out.append(ch)
            i += 1
            continue
        if ch == "-" and i + 1 < n and s[i + 1] == "-":
            j = s.find("\n", i)  # 行注释到行尾
            if j == -1:
                break
            i = j
            continue
        if ch == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            if j == -1:
                break
            i = j + 2
            out.append(" ")      # 用空格替代，避免把前后两个词粘成一个
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _word_at(low, pos, word):
    """判断 low[pos:] 是否以 word 开头，且前后都是词边界。

    ★ 为什么要边界判断？
      找 `where` 子句时，如果没边界，`somewhere` / `order_status` 里的片段也会命中，
      导致"在字段名里找到了关键字"，后续截取全错位。
    """
    if not low.startswith(word, pos):
        return False
    before = low[pos - 1] if pos > 0 else " "
    after = low[pos + len(word)] if pos + len(word) < len(low) else " "
    return not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_")


def _find_clause(body, keyword):
    """取出 depth=0 处某个子句的内容（到下一个子句关键字为止）。

    ★ 为什么必须做括号深度扫描？
      子查询里也有 WHERE / GROUP BY，直接字符串查找会抓到内层那段，
      于是"外层没过滤"会被误判成"有过滤"。只在深度 0 处认关键字才靠谱。
    """
    low = body.lower()
    n = len(low)
    depth = 0
    i = 0
    while i < n:
        ch = low[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and _word_at(low, i, keyword):
            start = i + len(keyword)
            j = start
            d2 = 0                       # 子句内部自己的括号深度
            while j < n:
                c = low[j]
                if c == "(":
                    d2 += 1
                elif c == ")":
                    d2 -= 1
                elif d2 == 0:
                    for kw in _CLAUSE_KEYWORDS:
                        if kw != keyword and _word_at(low, j, kw):
                            return body[start:j]
                j += 1
            return body[start:]
        i += 1
    return ""


def _split_top(seg, sep=","):
    """按顶层分隔符切分（忽略括号内与引号内的分隔符）。

    ★ 典型场景：`GROUP BY a, toStartOfMonth(t), if(x=1,'a','b')`
      简单 split(",") 会把 if() 里的逗号也切开，导致分组键数量判断错。
    """
    out = []
    depth = 0
    quote = ""
    buf = []
    for ch in str(seg or ""):
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [x for x in out if x]


def _bare_field(name):
    """把 `r.app_entity_source` / ``app_entity_source`` 归一成裸字段名（小写）。"""
    return str(name or "").split(".")[-1].strip('`"').strip().lower()


def _drop_before(question, item):
    """判断"删除动词"是否紧挨在 item 之前（如「去掉应用主体分组」里的 应用主体）。

    ★ 只往前看 6 个字符：中文表达里"去掉/不要/删掉"与对象几乎总是相邻；
      窗口太大反而会把"不要按月份，改成按周"里的"按"也算进来，造成误判。
    """
    q = str(question or "")
    pos = q.find(item)
    if pos < 0:
        return False
    return bool(_RE_DROP_NEAR.search(q[max(0, pos - 6):pos]))


# ═══════════════════════════════════════════════════════════
# ② SQL 事实提取（一次解析，多处复用）
# ═══════════════════════════════════════════════════════════
def analyze(sql):
    """把一条 SQL 拆成判定需要的几组事实。

    返回 dict：
        body          剥注释后的正文
        aliases       SELECT 输出列名（含中文别名）——用于判断"指标/维度还在不在"
        group_keys    GROUP BY 的各个键（原文片段）
        has_group_by  是否存在 GROUP BY
        preds         行过滤层的 (字段, 值) 列表 ——用于判断"过滤还在不在/替换是否变追加"
        is_select     是否是 SELECT 查询（不是 → degraded）
        source        用了哪种解析方式（ast / regex），仅用于排障
        degraded      事实是否不完整（此时不报 error，交给既有闸门兜底）

    ★ 为什么不用 sqlglot 一把梭？
      真实 ClickHouse SQL 末尾常带 FORMAT 子句、函数大小写混杂，
      sqlglot 在不同方言下时对时错。所以：别名优先用 sqlglot（guard.output_aliases），
      其余用"剥注释 + 括号深度扫描"的正则，两条腿走路，任一条可用即可。
    """
    body = strip_comments(sql)
    facts = {
        "body": body,
        "aliases": [],
        "group_keys": [],
        "has_group_by": False,
        "preds": [],
        "is_select": False,
        "source": "regex",
        "degraded": False,
    }
    # 是不是一条像样的 SELECT？不像就直接标降级，别硬判（判错比不判更糟）
    low = body.lower()
    if not low.strip():
        facts["degraded"] = True
        return facts
    facts["is_select"] = bool(re.search(r"\bselect\b", low) and re.search(r"\bfrom\b", low))
    # ★ 不是 SELECT 查询（拒答文本 / 非 SQL 内容）→ 直接标降级，交给既有闸门处理。
    #   在这里硬判"缺指标/缺过滤"只会制造噪音，而且这类输入本来就不该由本模块定夺。
    if not facts["is_select"]:
        facts["degraded"] = True
        return facts

    # ── 别名：优先 sqlglot（能吃透引号、中文、函数别名）──
    try:
        aliases = [a for a in (guard.output_aliases(sql) or []) if a]
        if aliases:
            facts["aliases"] = aliases
            facts["source"] = "ast"
    except Exception:
        aliases = []        # 解析器异常不能影响审核（下面有正则兜底）

    # ── 别名兜底：只扫 SELECT ... FROM 之间，避免把 CAST(x AS DateTime) 也当别名 ──
    if not facts["aliases"]:
        head = body
        m = re.search(r"\bselect\b(.*?)\bfrom\b", body, re.I | re.S)
        if m:
            head = m.group(1)
        for mm in re.finditer(r"\bas\s+(\"[^\"]*\"|`[^`]*`|[^\s,()]+)", head, re.I):
            raw = mm.group(1).strip()
            if raw[:1] in ("'", '"', "`"):
                raw = raw[1:-1] if len(raw) > 1 else ""
            if raw:
                facts["aliases"].append(raw)

    # ── GROUP BY：先拿原文，再按顶层逗号切 ──
    gseg = _find_clause(body, "group by")
    if gseg.strip():
        facts["has_group_by"] = True
        facts["group_keys"] = _split_top(gseg)

    # ── 行过滤谓词：只看外层 WHERE ──
    facts["preds"] = _where_predicates(body)
    return facts


def _where_predicates(body):
    """提取外层 WHERE 里的 (字段, 运算符, 值) 三元组。

    ★ 为什么只取外层？
      子查询里的过滤不改变"这条 SQL 对用户条件是否落实"的判断；
      把内层也抓进来，会让"已经删掉的过滤"因为子查询里恰好有同名字段而误判为"还在"。

    ★ 支持三种写法（覆盖真实模型产出的绝大多数形态）：
        field = value / field = 'value' / field IN (a, b) / field >= '2026-01-01'
    """
    wh = _find_clause(body, "where")
    if not wh.strip():
        return []
    preds = []
    # ① 比较运算：= != <> >= <= > <
    pattern = (r"([A-Za-z_][\w.]*)\s*(=|!=|<>|>=|<=|>|<)\s*"
               r"('([^']*)'|\"([^\"]*)\"|-?\d+(?:\.\d+)?)")
    for m in re.finditer(pattern, wh):
        field = _bare_field(m.group(1))
        op = m.group(2)
        # 值可能来自单引号内容(4)、双引号内容(5) 或裸数字(3)
        val = m.group(4) if m.group(4) is not None else (
            m.group(5) if m.group(5) is not None else m.group(3))
        preds.append({"field": field, "op": op, "value": str(val).strip().lower()})
    # ② IN 列表：一个 IN 展开成多条等值谓词，判定时可统一按 = 处理
    for m in re.finditer(r"([A-Za-z_][\w.]*)\s+in\s*\(([^)]*)\)", wh, re.I):
        field = _bare_field(m.group(1))
        for v in m.group(2).split(","):
            v = v.strip().strip("'\"")
            if v:
                preds.append({"field": field, "op": "in", "value": v.lower()})
    return preds


def _parse_filter(text):
    """把契约里的一条过滤条件（如 `user_type = 1`）解析成 (字段, 值)。

    ★ 实现技巧：复用 _where_predicates —— 给它拼一个假的 `where ` 前缀，
      就不用再写一套正则，两处口径天然一致。
    """
    preds = _where_predicates("where " + str(text or ""))
    if not preds:
        return None, None
    return preds[0]["field"], preds[0]["value"]


def _has_pred(preds, field, value):
    """判断"字段 = 值"是否仍出现在行过滤层（等值与 IN 都算）。"""
    f = _bare_field(field)
    v = str(value).strip().lower()
    for p in preds:
        if p["field"] == f and p["op"] in ("=", "in") and p["value"] == v:
            return True
    return False


def _values_of(preds, field):
    """取出某字段在行过滤层的所有取值（用于判断"替换是否变成追加"）。"""
    f = _bare_field(field)
    return [p["value"] for p in preds if p["field"] == f and p["op"] in ("=", "in")]


# ═══════════════════════════════════════════════════════════
# ③ 需求契约（把 understanding + 上一轮上下文拼成"可判定的期望"）
# ═══════════════════════════════════════════════════════════
def _operation(question):
    """判定本轮修改类型：replace / remove / add / none。

    ★ 这是"启发式"，可能判错 —— 但判错无害：
      它只决定"哪几条规则被启用"，最终是否通过仍由规则与（P2 的）模型审核决定。
      绝不用它去改写 SQL。
    """
    q = str(question or "")
    if _RE_PLACE_VERBS.search(q):
        return "replace"
    if _RE_REMOVE_VERBS.search(q):
        return "remove"
    if _RE_ADD_VERBS.search(q):
        return "add"
    return "none"


def _alias_field_map(base_sql):
    """用上一轮 SQL 建立「中文别名 → 源字段」映射。

    ★ 为什么需要它？
      用户说的是"应用主体"，而契约里存的是 `app_entity_source = 0`。
      上一轮 SQL 里恰好有 `app_entity_source AS "应用主体"`，
      用它做一次翻译，就能把"用户点名的字段"和"契约里的过滤字段"对上。
      比维护一张中文标签表更省、也更准（标签就是模型当时起的）。
    """
    out = {}
    try:
        for alias, src in (guard.output_source_fields(base_sql) or {}).items():
            if alias and src:
                out[_squash(alias)] = _bare_field(src)
    except Exception:
        pass
    return out


def _numbers_in(text):
    """从问句里抽出可能是"过滤新取值"的数字（排除年份、排除长数字）。

    ★ 为什么要排除年份？
      "时间改到 2026-12-01" 里的 2026 不是过滤值。若当成新值，
      会造出一条 `field 0 → 2026` 的错误替换对，进而误报。
    """
    out = []
    for m in re.finditer(r"(?<![\d-])(\d{1,6})(?![\d-])", str(text or "")):
        v = m.group(1)
        if len(v) == 4 and v[:2] in ("19", "20"):   # 形如 1999 / 2026 → 当成年份
            continue
        out.append(v)
    return out


def build_contract(question, understanding=None, modify_context=None):
    """构造「需求契约」：这次查询"必须满足什么"。

    参数：
        question        本轮用户原话
        understanding   本轮查询理解结果（query_understanding.understand）
        modify_context  增量修改带入的上一轮上下文（可为 None → 首轮查询）

    返回 dict，字段含义见下方注释。契约是【期望】，不是事实源：
    它不参与 SQL 生成，只用于事后判定。
    """
    u = understanding or {}
    mc = modify_context or {}
    base_u = mc.get("base_understanding") or {}
    base_sql = mc.get("base_sql") or ""
    is_modify = bool(base_sql or mc.get("base_question"))

    # 本轮若没识别出某字段，就沿用上轮的（与 agent._merge_understanding_for_modify 同向：
    # 只往"安全"方向合并，避免把用户没要求删的条件当成"已经删了"）
    def _pick(key):
        cur = u.get(key)
        if cur not in (None, "", [], {}):
            return cur
        return base_u.get(key) or ([] if key != "time_range" else "")

    contract = {
        "version": CONTRACT_VERSION,
        "question": question or "",
        "source": "modify" if is_modify else "fresh",
        "operation": _operation(question) if is_modify else "none",
        "intent": u.get("intent") or base_u.get("intent") or "",
        "metrics": list(_pick("metrics") or []),
        "dimensions": list(_pick("dimensions") or []),
        "time_range": _pick("time_range") or "",
        "filters": list(_pick("filters") or []),
        "must_keep": {"metrics": [], "dimensions": [], "filters": []},
        "must_remove": {"dimensions": [], "filters": []},
        "replace_pairs": [],
        "unknown": [],
        "empty": False,
    }

    # ── 上一轮的"必须保留项"：上一轮有、本轮没提 → 必须仍在 ──
    if is_modify:
        contract["must_keep"]["metrics"] = list(base_u.get("metrics") or [])
        contract["must_keep"]["dimensions"] = list(base_u.get("dimensions") or [])
        contract["must_keep"]["filters"] = list(base_u.get("filters") or [])
        # ★ 上一轮理解里没写 filters 时，从上一轮 SQL 反推（否则"丢过滤"这条规则形同虚设）
        #   但只在 understanding 完全没给的时候才这样做：权限层会往 SQL 里注入行级过滤，
        #   那些不是用户意图，当成"必须保留"会误拦（模型删掉后权限层仍会重新加上）。
        if not contract["must_keep"]["filters"]:
            for p in _where_predicates(strip_comments(base_sql)):
                if p["op"] == "=":
                    contract["must_keep"]["filters"].append(f"{p['field']} = {p['value']}")

    alias_map = _alias_field_map(base_sql)
    q = question or ""
    op = contract["operation"]

    # ── 本轮"明确要去掉"的项 ──
    if op in ("remove", "replace"):
        # 说"分组/维度/按"→ 删维度；说"过滤/条件"→ 删过滤；都没说 → 两种都试（保守）
        about_dim = bool(re.search(r"(分组|维度|按|group)", q, re.I))
        about_filter = bool(re.search(r"(过滤|条件|筛选|where)", q, re.I))
        named_dims = [d for d in contract["must_keep"]["dimensions"]
                      if _squash(d) and _squash(d) in _squash(q)]
        # ① 删除动词紧跟在维度名之前 → 该维度被删（"去掉应用主体分组"）
        dropped = [d for d in named_dims if _drop_before(q, d)]
        if dropped:
            contract["must_remove"]["dimensions"].extend(dropped)
        # ② 收敛表达（"只按月份" / "只保留月份"）→ 点名的留下，【没点名】的才是要删的；
        #    ★ 判定条件必须包含"至少点名了一个已知维度"，否则"只保留注册客户数"
        #      （那是在改指标）会被误判成删维度。
        elif _RE_ONLY_DIM.search(q) and named_dims:
            for dim in contract["must_keep"]["dimensions"]:
                if dim not in named_dims:
                    contract["must_remove"]["dimensions"].append(dim)
        for f in contract["must_keep"]["filters"]:
            field, _val = _parse_filter(f)
            if not field:
                continue
            named = field in _squash(q) or any(
                a in _squash(q) for a, src in alias_map.items() if src == field)
            if named and (about_filter or not about_dim):
                contract["must_remove"]["filters"].append(f)

    # ── 本轮"替换"：从旧过滤推出 (字段, 旧值, 新值) ──
    if op == "replace":
        nums = _numbers_in(q)
        for f in contract["must_keep"]["filters"]:
            field, old = _parse_filter(f)
            if not field or old is None:
                continue
            # 字段是否被用户点名：优先"中文别名→字段"翻译，其次字段名原文
            named = field in _squash(q) or any(
                a in _squash(q) for a, src in alias_map.items() if src == field)
            # 未点名但只有一个过滤条件、且问句里有唯一的新数值 → 也认为是在改它
            if not named:
                if len(contract["must_keep"]["filters"]) == 1 and len(nums) == 1:
                    named = True
                else:
                    continue
            news = [v for v in nums if v != old]
            if len(news) == 1:
                contract["replace_pairs"].append(
                    {"field": field, "old": old, "new": news[0]})
            elif len(news) > 1:
                # 多个候选值 → 不下结论（宁可交给 P2 的语义审核，也不猜）
                contract["unknown"].append(f"替换 {field} 的新取值不唯一：{news}")

    # ── 本轮"替换"第二条路：用本轮 understanding 反推（比从问句里抠数字可靠）──
    #   ★ 为什么仅靠上面的问句解析不够？（真实误拦案例）
    #     用户说"不要看代驾了，只看大板"，问句里**既没有字段名也没有数字** ——
    #     "大板"是业务黑话，唯一的价值线索是 understanding 已经把它解析成
    #     `app_entity_source = 201`。上面那段判定 named=False 直接 continue，
    #     于是 must_keep.filters 里留着 `app_entity_source = 102`，
    #     一条完全正确的 SQL（只有 =201）会被 R3 判成"丢了原过滤"——误拦。
    #   ★ 为什么可以信任 understanding？
    #     它就是真实流水线里模型对本轮的解析结果，是"本轮用户到底要什么"的正式载体。
    #     这里只做"同一字段、值变了"的机械比对，不解释语义、不改 SQL。
    if is_modify and (u.get("filters") or []):
        cur_pairs = [_parse_filter(f) for f in (u.get("filters") or [])]
        cur_pairs = [(fd, v) for fd, v in cur_pairs if fd]
        for f in list(contract["must_keep"]["filters"]):
            field, old = _parse_filter(f)
            if not field:
                continue
            if any(p["field"] == field for p in contract["replace_pairs"]):
                continue                       # 问句那条路已经判过了，不重复
            news = [v for fd, v in cur_pairs if fd == field and str(v) != str(old)]
            if len(news) == 1:
                contract["replace_pairs"].append(
                    {"field": field, "old": old, "new": news[0]})
            elif len(news) > 1:
                contract["unknown"].append(f"替换 {field} 的新取值不唯一：{news}")

    # ── 本轮被"换掉/去掉"的旧指标、旧维度，必须从 must_keep 里剔除 ──
    #   ★ 这是与过滤 replace_pairs 完全同源的坑（P1 修过一版过滤的，这里补指标/维度）：
    #     用户说"把注册客户数改成注册司机数"，正确的 SQL 里【已经不再有】注册客户数；
    #     若 must_keep.metrics 还留着它，correct SQL 会被 R1 判成"删了原指标"。
    #     审核器自相矛盾 → 模型无论怎么改都通不过 → 比不审核更糟。
    #   ★ 为什么必须要求"本轮 understanding 给了清单"（cur_m/cur_d 非空）？
    #     没给清单时无法区分"用户没提"和"解析失败"，此时保持保守（一律必须保留）。
    #   ★ 为什么还要求旧名字出现在问句里？
    #     防止"不要算未完成的"这类问句把「注册客户数」误当成要删的指标。
    if is_modify and op in ("replace", "remove"):
        cur_m = [m for m in (u.get("metrics") or []) if m]
        cur_d = [d for d in (u.get("dimensions") or []) if d]
        if cur_m:
            for m in list(contract["must_keep"]["metrics"]):
                if m not in cur_m and _squash(m) in _squash(q):
                    contract["must_keep"]["metrics"].remove(m)
        if cur_d:
            for d in list(contract["must_keep"]["dimensions"]):
                if d not in cur_d and _squash(d) in _squash(q):
                    if d not in contract["must_remove"]["dimensions"]:
                        contract["must_remove"]["dimensions"].append(d)

    # ── 本轮"要去掉"的项从"必须保留"里剔除，避免自己跟自己打架 ──
    #   ★ 替换对尤其关键：用户说"只看 101"，正确的 SQL 里【不再有】=0。
    #     如果不把这条从 must_keep 里剔除，"正确替换"反而会被 req_filter_missing 判成
    #     "丢了原过滤" —— 两条规则互相打架，模型无论怎么改都通不过。
    for pair in contract["replace_pairs"]:
        for f in list(contract["must_keep"]["filters"]):
            field, old = _parse_filter(f)
            if field == pair.get("field") and str(old) == str(pair.get("old")):
                contract["must_keep"]["filters"].remove(f)
    for dim in contract["must_remove"]["dimensions"]:
        if dim in contract["must_keep"]["dimensions"]:
            contract["must_keep"]["dimensions"].remove(dim)
    for f in contract["must_remove"]["filters"]:
        if f in contract["must_keep"]["filters"]:
            contract["must_keep"]["filters"].remove(f)

    # ── 指标：本轮 + 上轮必须保留的，合并去重（缺一个都算"删了原指标"）──
    merged = []
    for m in list(contract["metrics"]) + list(contract["must_keep"]["metrics"]):
        if m and m not in merged:
            merged.append(m)
    contract["metrics"] = merged

    # ── 是否"空契约"：没有任何可判定的约束 → 直接通过（首轮兜底，杜绝误拦）──
    has_dim_dates = bool(re.findall(r"\d{4}-\d{2}-\d{2}", contract["time_range"]))
    contract["empty"] = not any([
        contract["metrics"], contract["dimensions"], contract["filters"],
        has_dim_dates, contract["must_keep"]["metrics"],
        contract["must_keep"]["filters"], contract["must_remove"]["dimensions"],
        contract["must_remove"]["filters"], contract["replace_pairs"],
    ])
    return contract


# ═══════════════════════════════════════════════════════════
# ④ 确定性规则（8 条）
# ═══════════════════════════════════════════════════════════
def _issue(severity, rule, message, suggestion):
    """统一构造一条 issue（字段名与 sql_validator 保持一致，便于 agent 复用回灌逻辑）。"""
    return {"severity": severity, "tier": "REQ", "rule": rule,
            "message": message, "suggestion": suggestion}


def _dim_satisfied(dim, facts):
    """判断一个维度是否在 SQL 里落实（别名 / 分组键 / 日期函数 三种口径）。"""
    d = _squash(dim)
    if not d:
        return True
    for a in facts["aliases"]:
        if d in _squash(a):
            return True
    for k in facts["group_keys"]:
        if d in _squash(k):
            return True
    # 日期类维度：写法不同但语义一致的，算落实（避免"按月份"vs toStartOfMonth 误报）
    if any(w in d for w in _DATE_DIM_WORDS):
        for k in facts["group_keys"]:
            if re.search(r"(tostartof|todate|todatetime|date_trunc|toyear|tohour|toquarter)", _squash(k)):
                return True
    return False


def _metric_satisfied(name, facts):
    """判断一个指标是否出现在输出列（别名包含指标名即可，容忍"注册客户数(人)"）。"""
    n = _squash(name)
    if not n:
        return True
    for a in facts["aliases"]:
        if n in _squash(a):
            return True
    # 兜底：正文里出现（例如模型把指标写在注释化别名、或用了同名 CTE 列）
    return n in _squash(facts["body"])


def check_contract(sql, contract):
    """按契约检查 SQL，返回 issues（不改 SQL、不执行、不调模型）。"""
    issues = []
    if not contract or contract.get("empty"):
        return issues                      # 空契约 = 没要求 = 不判（防误拦）

    facts = analyze(sql)
    if facts["degraded"]:
        # ★ 判不了就不判：SQL 若真有问题，会由 guard / sql_validator 的 fail-closed 兜住。
        #   在这里硬报 error 等于"审核器自己崩了导致全量拒答"，比漏判更糟。
        return issues

    # ── R1 指标缺失：本轮要求/上轮保留的指标，输出列里一个都没有 ──
    missing = [m for m in contract.get("metrics") or [] if not _metric_satisfied(m, facts)]
    if missing:
        issues.append(_issue(
            "error", "req_metric_missing",
            f"SQL 输出列里找不到这些指标：{missing}（疑似被删除或改名）",
            "把用户要求的指标全部保留在 SELECT 输出里；本轮只要求新增/替换时，"
            "不得删掉上一轮已有的指标"))

    # ── R2 维度缺失：上轮有、本轮没要求删的维度，SQL 里没有了 ──
    dim_missing = [d for d in (contract.get("must_keep") or {}).get("dimensions") or []
                   if not _dim_satisfied(d, facts)]
    if dim_missing:
        issues.append(_issue(
            "error", "req_dim_missing",
            f"上一轮的这些分组维度在本轮 SQL 里丢失了：{dim_missing}（本轮未要求删除）",
            "把未要求删除的维度补回 GROUP BY 与 SELECT；只按用户明确点名的维度做增删"))

    # ── R3 过滤缺失：上轮有、本轮没要求删的过滤条件，SQL 里没有了 ──
    f_missing = []
    for f in (contract.get("must_keep") or {}).get("filters") or []:
        field, val = _parse_filter(f)
        if field is None:
            continue                      # 解析不出来的条件不判（避免噪音）
        if not _has_pred(facts["preds"], field, val):
            f_missing.append(f)
    if f_missing:
        issues.append(_issue(
            "error", "req_filter_missing",
            f"上一轮的这些过滤条件在本轮 SQL 里丢失了：{f_missing}（本轮未要求去掉）",
            "补回这些过滤条件；去掉某个维度分组 ≠ 去掉该字段上的等值过滤"))

    # ── R4 替换写成追加（最高频真实错误）：旧值与新值同时出现 ──
    for pair in contract.get("replace_pairs") or []:
        field, old, new = pair.get("field"), str(pair.get("old")), str(pair.get("new"))
        vals = _values_of(facts["preds"], field)
        if old in vals and new in vals:
            issues.append(_issue(
                "error", "req_replace_appended",
                f"用户要求把 `{field}` 从 {old} 换成 {new}，但 SQL 里 {old} 和 {new} 同时存在"
                f"（替换被写成了追加，结果范围被放大）",
                f"把 `{field} = {old}` 替换为 `{field} = {new}`，而不是用 OR 追加"))

    # ── R4b 替换未落实：旧值没了、新值也没出现（等价于"把过滤条件删了"）──
    #   ★ 为什么 R3/R4 抓不到这种情况？
    #     R3 只查 must_keep 里的旧过滤（替换后已被剔除）、R4 只查"新旧同时存在"。
    #     模型最省事的错误做法是"把这一条 WHERE 删掉" —— 旧值消失、新值从未出现，
    #     既不算"丢了原过滤"也不算"替换变追加"，于是静默通过。
    #     真实后果：用户说"只看 101"，拿到的却是全主体数据，数字变大但没人发现。
    #   ★ 为什么要用"正文里也没出现过新值"兜底？
    #     有些模型会把条件写进子查询 / HAVING / CTE，_values_of 只看外层 WHERE 会误判。
    #     只要新值在正文任何位置出现（带边界），就认为已落实，宁可漏判不误拦。
    for pair in contract.get("replace_pairs") or []:
        field, old, new = pair.get("field"), str(pair.get("old")), str(pair.get("new"))
        if new in _values_of(facts["preds"], field or ""):
            continue
        if re.search(r"(?<![\w.])%s(?![\w.])" % re.escape(new), facts["body"]):
            continue
        issues.append(_issue(
            "error", "req_replace_not_applied",
            f"用户要求把 `{field}` 从 {old} 换成 {new}，但 SQL 里既没有 {old} 也没有 {new}"
            f"（该过滤条件被整体删掉了，结果范围被放大）",
            f"把 `{field} = {new}` 明确写进 WHERE；不要用「删掉这个条件」来实现替换"))


    # ── R5 该删的维度没删（warning：可能是用户本意"保留分组"） ──
    for dim in (contract.get("must_remove") or {}).get("dimensions") or []:
        if _dim_satisfied(dim, facts):
            issues.append(_issue(
                "warning", "req_dim_not_removed",
                f"用户要求去掉维度「{dim}」，但 SQL 里仍按它分组",
                "确认是否确实需要保留该分组；若用户要求去掉，请从 GROUP BY 与 SELECT 中移除"))

    # ── R5b 该删的过滤没删（warning：分不清"模型没删"还是"权限层重新注入"） ──
    #   ★ 为什么是 warning 而不是 error？
    #     权限层会按行级规则往 SQL 里注入等值过滤（tenant/org 之类的字段）。
    #     如果用户要去掉的恰好就是这类字段，模型删了、权限层又加回来了，
    #     报 error 就等于"因为权限生效而拒绝回答"。这种误拦的代价比漏判高。
    #   ★ 为什么还是要报？
    #     "去掉应用主体等于 0 这个过滤"被无视时，结果集会被悄悄收窄 ——
    #     用户看到的是"少数了几个人"，几乎不可能自己发现。至少要让人看见。
    for f in (contract.get("must_remove") or {}).get("filters") or []:
        field, val = _parse_filter(f)
        if field is None:
            continue
        if _has_pred(facts["preds"], field, val):
            issues.append(_issue(
                "warning", "req_filter_not_removed",
                f"用户要求去掉过滤「{f}」，但 SQL 里它还在（结果可能被意外收窄）",
                "把该过滤条件从 WHERE 里删掉；若该条件由权限层强制注入，可忽略本提示"))

    # ── R6 时间范围未落实（warning：函数/时区写法可能不同形，不硬拦） ──
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", contract.get("time_range") or "")
    if dates:
        body = _squash(facts["body"])
        lost = [d for d in dates if d not in body]
        if lost:
            issues.append(_issue(
                "warning", "req_time_missing",
                f"契约里的时间边界 {lost} 没有出现在 SQL 中（时间范围可能未落实）",
                "按用户要求的时间范围写出上下界（左闭右开），不要用相对时间猜测"))

    # ── R7 有维度但完全没有 GROUP BY（典型的"漏了分组，只吐一行汇总"） ──
    if contract.get("dimensions") and not facts["has_group_by"]:
        issues.append(_issue(
            "error", "req_group_absent",
            f"用户要求按 {contract['dimensions']} 分组，但 SQL 没有 GROUP BY（结果会塌成一行）",
            "补上 GROUP BY 与对应的分组列"))

    # ── R8 SELECT *（warning：无法确认指标是否落实） ──
    if contract.get("metrics") and re.search(r"\bselect\s+\*", facts["body"], re.I):
        issues.append(_issue(
            "warning", "req_select_star",
            "SQL 使用了 SELECT *，无法确认用户要求的指标是否真的被计算",
            "显式写出需要的指标列，并给中文别名"))

    return issues


# ═══════════════════════════════════════════════════════════
# ⑤ 语义层（LLM 裁决）—— 抓规则抓不到的"答非所问"
# ═══════════════════════════════════════════════════════════
#   ★ 进程内缓存：自愈循环里同一条 SQL 可能被判 2~3 次（每次修复后重审），
#     缓存能在"修完的 SQL 没变"时省掉一次模型调用。
#   ★ 只缓存成功解析的结果；超时/解析失败不缓存（下次要能重试）。
_SEM_CACHE = {}
_SEM_CACHE_MAX = 256


def clear_semantic_cache():
    """清空语义层缓存（测试/排障用）。"""
    _SEM_CACHE.clear()


def _cache_key(sql, contract):
    """缓存键：SQL 指纹 + 契约指纹 —— 契约变了，结论就不能复用。"""
    payload = json.dumps({
        "sql": _squash(sql),
        "metrics": contract.get("metrics"),
        "dims": contract.get("dimensions"),
        "keep": contract.get("must_keep"),
        "remove": contract.get("must_remove"),
        "pairs": contract.get("replace_pairs"),
        "time": contract.get("time_range"),
        "op": contract.get("operation"),
        "q": contract.get("question"),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _parse_json_obj(raw):
    """容错解析模型返回的 JSON：剥 ``` 围栏、抽最外层 {...}，失败返回 None。"""
    if not raw:
        return None
    try:
        text = re.sub(r"```(?:json)?", "", str(raw))     # 去掉 markdown 围栏
        m = re.search(r"\{.*\}", text, re.S)             # 抽最外层对象
        if not m:
            return None
        return json.loads(m.group(0))
    except Exception:
        return None


# 语义层允许的问题码（与 prompt.build_requirement_review_prompt 的枚举一致）
_SEM_CODES = {"intent_misread", "context_lost", "change_not_applied",
              "replace_as_append", "dropped_metric", "dropped_filter",
              "grain_wrong", "join_cardinality", "metric_caliber", "known_failure"}


def review_semantic(sql, contract, exp_digest="", validator_issues=None,
                    base_question="", base_sql="", timeout=None):
    """调模型做需求一致性裁决（只裁决，不产出 SQL）。

    返回 dict：verdict(pass/fail/unsure/skipped)、issues、assumptions、degraded、source

    ★ 三种"没结果"必须分清（本项目反复踩的坑就是把它们混成"通过"）：
        source=disabled → 开关关了，本次只跑确定性规则（结论由确定性层给）
        source=error    → 调了但失败（超时/解析不了）→ verdict=skipped, degraded=True
        source=llm      → 真拿到了裁决
    """
    # 延迟导入：避免模块级循环依赖（llm/prompt 都间接依赖 config 体系）
    import llm
    import prompt

    if timeout is None:
        timeout = getattr(config, "REQ_REVIEW_TIMEOUT", 8)

    key = _cache_key(sql, contract)
    if key in _SEM_CACHE:
        hit = dict(_SEM_CACHE[key])
        hit["cached"] = True
        return hit

    try:
        rp = prompt.build_requirement_review_prompt(
            question=contract.get("question") or "",
            sql=sql,
            contract=contract,
            base_question=base_question,
            base_sql=base_sql,
            experience_digest=exp_digest,
            validator_issues=validator_issues)
        raw = llm.chat("", rp, timeout=timeout)
    except Exception as e:
        print(f"⚠️ 需求语义审核调用失败（按 skipped 处理，不阻断）：{e}")
        return {"verdict": "skipped", "issues": [], "assumptions": [],
                "degraded": True, "source": "error", "error": str(e)[:200]}

    data = _parse_json_obj(raw)
    if not isinstance(data, dict):
        print("⚠️ 需求语义审核返回无法解析（按 skipped 处理，不阻断）")
        return {"verdict": "skipped", "issues": [], "assumptions": [],
                "degraded": True, "source": "error", "error": "JSON 解析失败"}

    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in ("pass", "fail", "unsure"):
        verdict = "unsure"          # 说了看不懂的话 → 当"没把握"，绝不当作 pass

    # ── findings 规范化：补默认值、过滤未知码、统一成 issues 结构 ──
    issues = []
    for f in (data.get("findings") or []):
        if not isinstance(f, dict):
            continue
        code = str(f.get("code") or "").strip()
        sev = str(f.get("severity") or "").strip().lower()
        if sev not in ("error", "warning"):
            sev = "error" if verdict == "fail" else "warning"
        what = str(f.get("what") or "").strip()
        if not what:
            continue
        issues.append({
            "severity": sev,
            "tier": "REQ-LLM",
            "rule": code if code in _SEM_CODES else "semantic",
            "message": what,
            "suggestion": str(f.get("fix") or "").strip(),
            "evidence": str(f.get("evidence") or "").strip(),
        })

    # ★ 判了 fail 却一条原因都不给 → 补一条合成 error。
    #   否则下游"回灌修复"拿到空清单，模型不知道该改什么，白烧一轮调用。
    if verdict == "fail" and not any(i["severity"] == "error" for i in issues):
        issues.append({
            "severity": "error", "tier": "REQ-LLM", "rule": "semantic",
            "message": "审核判定未满足本次查询要求，但未给出具体原因",
            "suggestion": "按用户本次的要求逐条核对指标、维度、过滤与时间范围",
            "evidence": "",
        })

    result = {
        "verdict": verdict,
        "issues": issues,
        "assumptions": [str(a) for a in (data.get("assumptions") or []) if str(a).strip()],
        "degraded": False,
        "source": "llm",
    }
    if len(_SEM_CACHE) < _SEM_CACHE_MAX:
        _SEM_CACHE[key] = result
    return result


# ═══════════════════════════════════════════════════════════
# ⑥ 对外入口
# ═══════════════════════════════════════════════════════════
def review(sql, contract, exp_digest="", validator_issues=None, use_llm=None,
           base_question="", base_sql=""):
    """执行需求一致性审核：确定性层 →（无 error 时）语义层。

    返回 dict：
        verdict   "pass" | "fail" | "unsure" | "skipped"
        issues    两层的 issues 合并（tier=REQ 确定性 / REQ-LLM 语义）
        degraded  判定不可信（事实提取不全，或语义层不可用）
        errors / warnings  计数
        semantic  语义层原始返回（排障/前端展示"模型怎么想的"）

    ★ 为什么确定性层已报 error 就不再调模型？
      省一次调用，且模型的话也改变不了结论（error 已是硬证据）。
      审核要有成本意识：能确定性判死的，不花钱再问一遍。
    ★ 为什么 degraded 不直接变成 fail？
      本模块跑不动 ≠ SQL 有问题。安全由既有闸门负责；
      把"审核器异常"当成"审核不通过"，会造成大面积误拒
      （sql_validator 修过的老坑：空 issues 无法区分"没问题"和"崩了"）。
    """
    if not contract or contract.get("empty"):
        return {"verdict": "pass", "issues": [], "degraded": False,
                "errors": 0, "warnings": 0, "semantic": None,
                "contract_version": CONTRACT_VERSION}

    facts = analyze(sql)
    issues = check_contract(sql, contract)
    errors = sum(1 for i in issues if i["severity"] == "error")

    # ── ① 确定性层已判死 → 直接 fail（不调模型） ──
    if errors:
        return {"verdict": "fail", "issues": issues, "degraded": False,
                "errors": errors,
                "warnings": sum(1 for i in issues if i["severity"] == "warning"),
                "semantic": None, "contract_version": CONTRACT_VERSION}

    # ── ② 语义层 ──
    llm_on = getattr(config, "REQ_REVIEW_LLM", True) if use_llm is None else bool(use_llm)
    if not llm_on:
        return {"verdict": "pass", "issues": issues, "degraded": bool(facts["degraded"]),
                "errors": 0,
                "warnings": sum(1 for i in issues if i["severity"] == "warning"),
                "semantic": {"verdict": "skipped", "source": "disabled"},
                "contract_version": CONTRACT_VERSION}

    sem = review_semantic(sql, contract, exp_digest=exp_digest,
                          validator_issues=validator_issues,
                          base_question=base_question, base_sql=base_sql)
    merged = issues + list(sem.get("issues") or [])

    if sem["verdict"] in ("fail", "unsure", "skipped"):
        verdict = sem["verdict"]     # 三种都不是"通过"，各自语义不同
    else:
        verdict = "pass"

    # 语义层不可用（skipped）时，degraded 必须为真 —— 否则调用方会当成"通过了"
    degraded = bool(sem.get("degraded")) or bool(facts["degraded"]) or verdict == "skipped"

    return {
        "verdict": verdict,
        "issues": merged,
        "degraded": degraded,
        "errors": sum(1 for i in merged if i["severity"] == "error"),
        "warnings": sum(1 for i in merged if i["severity"] == "warning"),
        "semantic": sem,
        "contract_version": CONTRACT_VERSION,
    }


def enabled_for(question, modify_context=None):
    """判断本次是否应启用需求审核（成本闸门）。

    ★ 为什么要按场景开关？
      首轮查询每问多调一次模型，就是多 3~6 秒 + 一份成本。
      而首轮的需求偏差主要由 P1 口径校验与经验兜底覆盖；
      真正高风险的是"多轮修改"（改错/漏改/替换变追加）。
      所以默认 modify_only，等 P1 拿到基线后再决定是否放开 always。
    """
    if not getattr(config, "REQ_REVIEW_ENABLED", True):
        return False
    mode = str(getattr(config, "REQ_REVIEW_MODE", "modify_only")).strip().lower()
    if mode == "off":
        return False
    if mode == "always":
        return True
    return bool(modify_context) or _operation(question) != "none"


def summary_line(issues):
    """把 issues 压成一行摘要（用于 info / 日志，不占前端版面）。"""
    if not issues:
        return ""
    errs = [i["rule"] for i in issues if i["severity"] == "error"]
    warns = [i["rule"] for i in issues if i["severity"] == "warning"]
    parts = []
    if errs:
        parts.append("error:" + ",".join(errs))
    if warns:
        parts.append("warning:" + ",".join(warns))
    return "需求审核 " + " | ".join(parts)


# ── 面向用户的"待确认问题"模板 ──
#   ★ 为什么不直接把 issue.message 抛给用户？
#     那是写给开发者/模型看的技术描述（"SQL 里 0 和 101 同时存在"），
#     用户看了不知道该怎么回答。这里翻译成"要用户拍板的选择题"。
#   ★ 也不新建澄清会话：那会引入一套状态机（clarification.py），
#     而这里只需要"说明我卡在哪"，一次性的提示更轻。
_CLARIFY_TEMPLATES = {
    "req_replace_appended": "这次修改是要把原来的取值【替换掉】，还是新旧取值都要统计？",
    "req_metric_missing": "上一轮的指标是否还需要保留？如果只是新增，我会把原指标一起保留。",
    "req_dim_missing": "上一轮的分组维度是否都需要保留？",
    "req_filter_missing": "上一轮的过滤条件是否都需要保留？（只有你明确要求时才会去掉）",
    "req_group_absent": "这次要按哪些维度分组？",
    "req_time_missing": "时间范围按哪个区间统计？",
}


def build_clarification(contract, issues, verdict="fail", assumptions=None):
    """审核多轮修正仍不通过、或审核没把握时，生成"需要用户补充什么"的说明。

    返回 {"reason": 一句话原因, "questions": [最多 3 个待确认问题]}
    —— 对应需求文档"多次修正仍无法确认时，向用户说明歧义或需要补充的信息"。

    ★ verdict=unsure 时，把模型给出的 assumptions 反向变成问句：
      "我按 X 理解了，对吗？" —— 这比让用户凭空重述需求有效得多。
    """
    errs = [i for i in (issues or []) if i.get("severity") == "error"]
    pool = errs or [i for i in (issues or []) if i.get("severity") == "warning"]
    questions = []
    for i in pool:
        q = _CLARIFY_TEMPLATES.get(i.get("rule"))
        if q and q not in questions:
            questions.append(q)
    # 契约里已知的未决项（如"替换的新取值不唯一"）也一并抛给用户
    for u in (contract or {}).get("unknown") or []:
        if u not in questions:
            questions.append(u)
    # ★ 模型的"我假设了…"是歧义的最直接证据（仅 unsure 时使用）
    if verdict == "unsure":
        for a in (assumptions or [])[:2]:
            q = f"我按「{a}」理解了，这样对吗？"
            if q not in questions:
                questions.append(q)
    reason = (pool[0]["message"] if pool
              else "无法确认本次查询是否符合你的要求")
    return {"reason": reason, "questions": questions[:3]}
