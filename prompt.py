# -*- coding: utf-8 -*-
"""
prompt.py —— Prompt 组装
★ 职责：把"表结构 + 指标口径 + 规则"拼成发给大模型的 System Prompt
★ ★★ 业务规则【不写在本文件里】，而是从 config_store 读数据库 ★★★
    以前：改规则要改这个文件 → 改代码 → 重启 → 重新部署
    现在：改规则用 admin.py / 管理页面写数据库 → 下次调用立即生效

★ 但有一类"硬约束"【刻意】写在代码里（HARD_CONSTRAINTS，见下方注释说明）：
  它们防的是"SQL 能跑、数字全错、还不报错"的静默错误，
  必须保证改配置也删不掉 —— 可编辑的安全护栏等于没有护栏。
"""
import re               # 解析注入文本里的字段行（字段名 + 类型 + 注释）
import json             # 需求审核 prompt 里要把契约的 list 渲染成 JSON（避免 str(list) 的 Python 引号）

import config          # 取 SQL 方言等配置
import config_store    # ★ 配置存储：规则从这里读


# ═══════════════════════════════════════════════════════════
# ★ 系统级硬约束（代码内置，配置库里删不掉）
# ═══════════════════════════════════════════════════════════
# 【为什么不放进 config_store 的 sql_rules？】
#   规则 1~19 存在配置库里，管理页面能改能删 —— 这是要的（业务方得能调）。
#   但"同名字段跨表串味"这类错误有个致命特征：**静默算错**。
#   SQL 语法完全正确、能跑出数、不报任何错，只是数字是错的。
#   用户看到一张正常的表，根本不会想到去怀疑字段含义。
#   护栏放在可编辑的地方，等于留了一个"谁都能拆掉安全气囊"的开关。
#   所以这一节写在代码里：改配置抹不掉，升级也不会丢。
#
# 【为什么护栏必须是"按表"而不是"按字段名"？】
#   我们真实踩过：询价表 ods_tu_order_pricing_record_all 的 carstate 本该是
#   「车况」（0=未知，1=能启动，2=不能启动），却被写成了订单流转的编码
#   （1=待派单，2=已派单，3=司机已接单…）—— 典型的跨表枚举搬运。
#   最阴的地方是：两条注释的前半句都写着「车辆状况」，前缀一字不差、
#   看着完全正常，只有把取值逐条对照本表真实数据才能发现。
#   模型（和人）看到"字段名一样 / 前缀一样"就默认"含义一样"，
#   于是拿订单状态的码去过滤车况，结果要么恒为空、要么恒为某个异常值，
#   而 SQL 本身挑不出任何毛病。
HARD_CONSTRAINTS = """# ★ 系统级硬约束（代码内置，优先于以上全部规则；冲突时以本节为准）

H1. 字段的枚举含义【只对同一张表的这一个字段】成立，禁止跨表套用。
    · 不同表里字段名完全相同，业务含义可能完全不同。本库真实案例：
      carstate 在 dwd_tu_order_all 里是「车况」（0=未知，1=能启动，2=不能启动…）；
      询价表 ods_tu_order_pricing_record_all 也用了同一个字段名，
      却被写成「0=未知，1=待派单，2=已派单，3=司机已接单…」——
      那是【订单流转】的编码，被搬到了不该出现的表上。
      ★ 注意这个坑的形态：两条注释的前半句都是「车辆状况」，
        只看前缀根本看不出问题，只有把取值逐条对照【本表真实数据】才能发现。
    · 遇到同名字段，必须分别按【该字段所在那张表】的注释 / 字典取值处理，
      严禁把 A 表的编码含义复制到 B 表。
    · 若某字段在它所在的那张表里没有任何取值说明，就不许猜枚举：
      宁可把它当普通维度原样输出或分组，也不要写一个可能错的 =N 过滤条件。
    · 自查动作：写下 `字段 = N` 之前，先回答"N 是从【这个字段在这张表】
      的注释里看到的吗？" 答不上来就不要写这个条件。

H2. 字段注释里的编码表（如 0=xx，1=xx）默认只覆盖它所属的那一个字段。
    不得因为"另一张表也有个同名/近名字段"就把这套编码搬过去；
    也不得把两个不同表的同名字段合并成同一套取值来解释。

H3. 表名/表注释是本表语义的第一依据。若本表注释说明的业务
    （如"询价记录"）与某个字段注释里的枚举含义明显矛盾
    （如把车况写成订单流转），以【本表业务】为准，该字段注释不可采信。"""


# 这些字段名在每张表里含义都相同，不携带"本表特有枚举"，
# 列进跨表提示纯属噪声（每次查询都会命中，反而把真问题埋掉）。
_TECH_COLUMNS = {
    "id", "create_time", "update_time", "ctime", "mtime", "utime",
    "created_at", "updated_at", "is_deleted", "is_del", "deleted",
    "remark", "version", "sort", "sort_no",
}

#   解析注入文本里的一行字段定义：`- carstate (Nullable(String))   车况（0=能启动…）`
#   类型可能有嵌套括号（Nullable(String)），所以只允许一层嵌套，
#   再把剩下的一律当注释 —— 注释里出现英文括号也不会被截断。
_RE_COL_LINE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(([^()]*(?:\([^()]*\))?[^()]*)\)\s*(.*)$")


def _parse_table_columns(t):
    """
    从 RAG 表对象里取出 {字段名: 字段注释}

    入参 t 形如 rag._build_tables 产出的：
        {"name": 表名, "columns": "- 字段 (类型)  注释\\n...", "column_list": [...]}

    ★ 解析失败不抛异常、只返回空字典 —— 这段是"锦上添花的护栏"，
      不该因为它把主流程（生成 SQL）搞挂。宁可少一条提示，不能少一条 SQL。
    """
    out = {}
    for raw in (t.get("columns") or "").splitlines():   # 一行一个字段
        line = raw.strip()                              # 去掉缩进
        if not line.startswith("-"):                    # 只认字段行
            continue
        body = line[1:].strip()                         # 去掉开头的 "-"
        m = _RE_COL_LINE.match(body)                    # 拆成 名 / 类型 / 注释
        if not m:
            continue
        out[m.group(1)] = (m.group(3) or "").strip()    # 注释可能为空
    return out


def _detect_same_name_columns(tables, table_names, limit=12):
    """
    找出【本次真正注入的那些表】里、同名但分属不同表的字段

    返回 [(字段名, [(表名, 注释), ...]), ...]，按"出现表数"倒序。

    ★ 为什么限定在本次注入的表？
      空话没有约束力。"注意不要跨表串味"模型看过就忘；
      "carstate 在 A 表是订单状态、在 B 表是车况"是可直接执行的证据。
      而且只看本次同时在用的表，噪声最小 —— 不会每次查询都糊上来一堆
      全库同名字段（id / create_time 这类到处都是）。
    ★ 为什么要排除 _TECH_COLUMNS？
      它们是纯技术字段，全库同义。提示它们等于稀释注意力，
      真问题反而被埋在十几条无关字段下面。
    """
    want = set(table_names or [])
    seen = {}                     # 字段名 -> [(表名, 注释), ...]
    for t in tables or []:
        name = t.get("name") or ""
        if not name:
            continue
        if want and name not in want:      # 只统计本次真正注入的表
            continue
        for col, cmt in _parse_table_columns(t).items():
            seen.setdefault(col, []).append((name, cmt))

    same = [(c, v) for c, v in seen.items()
            if len(v) > 1 and c.lower() not in _TECH_COLUMNS]
    same.sort(key=lambda x: (-len(x[1]), x[0]))   # 出现表数多的排前面
    return same[:limit]


def _build_cross_table_block(tables=None, table_names=None):
    """
    把"同名跨表字段"渲染成一段可执行的提示

    没有任何同名跨表字段时返回空串 —— 不留空标题（和字典块同一原则：
    没命中就一个字都不出现，否则模型会去找不存在的东西）。
    """
    same = _detect_same_name_columns(tables or [], table_names or [])
    if not same:
        return ""

    lines = ["# ⚠️ 同名跨表字段（本次注入的表里重复出现，必须按表区分）",
             "下面这些字段名同时出现在多张表中。【同名不等于同义】。"]
    for col, occ in same:
        lines.append(f"- {col}：")
        for tn, cmt in occ:
            c = cmt or "（本表无注释）"
            if len(c) > 70:                 # 注释过长会挤掉更有用的上下文
                c = c[:70] + "…"
            lines.append(f"    · {tn}：{c}")
        #   ★ 各表注释雷同，正是"整表复制串味"的指纹（carstate 就是这么错的）。
        #     单独点出来，比笼统说一句"注意区分"有用得多。
        vals = {c for _, c in occ if c}
        if len(occ) > 1 and len(vals) == 1:
            lines.append("    ★ 上面各表注释完全相同 —— 高度疑似复制串味，"
                         "不得直接沿用，须按各表业务先核对再使用")
    lines.append("使用规则：只按【该字段所在那张表】的注释理解取值；"
                 "不得把一张表的编码含义套到另一张表；本表无注释时不要猜枚举。")
    return "\n".join(lines)


def build_hard_constraints_block(tables=None, table_names=None):
    """
    组装"硬约束"整段：系统级铁律 + 本次的同名跨表字段清单

    ★ 两条流水线都调这一个函数：
      legacy 走 build_system_prompt 内部拼接，v2（agent.py）单独调它补上。
      分成两处各写一段，迟早会出现"只有一条路受约束"的情况 —— 本项目的
      字典注入就吃过这个亏（v2 检索了字典却没拼进 prompt），教训记在这里。
    """
    cross = _build_cross_table_block(tables, table_names)
    if cross:
        return HARD_CONSTRAINTS + "\n\n" + cross
    return HARD_CONSTRAINTS


# ═══════════════════════════════════════════════════════════
# 拼"表结构"部分
# ═══════════════════════════════════════════════════════════
def _build_schema_block(tables, table_names):
    """只把命中的表拼进去，没命中的表一个字都不出现"""
    parts = []                                  # 装每段表结构的空列表
    for t in tables:                            # 遍历所有表
        if t["name"] in table_names:            # 只看命中的
            parts.append(f"表 {t['name']}:\n{t['columns']}")
            #             拼成 "表 xxx:\n  - 字段 (类型)"
    return "\n\n".join(parts)                   # 多张表之间空一行
    #       "\n\n".join(列表) ≈ 用两个换行当分隔符，把多行拼成一个字符串


# ═══════════════════════════════════════════════════════════
# 拼"指标口径"部分
# ═══════════════════════════════════════════════════════════
def _build_metric_block(metrics, metric_names):
    """只把命中的指标口径拼进去"""
    if not metric_names:                        # 没命中任何指标
        return "（本次未识别到特定指标，按字段直接统计即可）"

    lines = []                                  # 装口径说明的空列表
    for m in metrics:                           # 遍历指标库
        if m["name"] in metric_names:           # 只看命中的
            lines.append(f"- {m['name']}：{m['definition']}")
    return "本次涉及的指标定义（业务口径，禁止修改）：\n" + "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 拼"数据字典"部分
# ═══════════════════════════════════════════════════════════
# ★ 输出列转换片段的分支数上限（2026-10-26 新需求：枚举字段输出直接转中文）。
#   大字典（如 210 条的资金收支流水类型）不能整段生成 CASE WHEN，否则 prompt 爆炸；
#   只取 shown（已按问题相关性重排）的前 N 个分支，其余取值留在取值列表里。
MAX_CASE_WHEN_BRANCHES = 20


def _case_when_snippet(field, items, dialect=None):
    """
    生成「输出列枚举转中文」的 CASE WHEN 片段。

    ★ 为什么给现成片段、而不是只在规则里说一句？
      让模型自己拼 CASE WHEN 时，编码↔中文容易抄错、漏 ELSE、或把 label 拼错；
      给可抄写的片段（与"给字典防编编码"同一思路），确定性高得多。

    ★ 使用边界（必须写进指令）：
      - 只用于 SELECT 输出列 —— WHERE 过滤、GROUP BY 分组仍用编码；
      - 片段里的字段名是 field_hint（纯字段名，如 type），模型要替换成实际表别名字段（如 d.type）；
      - ELSE 取原值（不是写死'其他'），避免未登记取值丢失信息：
          ClickHouse → toString(field)；SQLite → CAST(field AS TEXT)。
    """
    if not field or not items:
        return ""
    if dialect is None:
        dialect = config.SQL_DIALECT
    branches = []
    for it in items[:MAX_CASE_WHEN_BRANCHES]:
        v = str(it.get("value", "")).strip()
        label = str(it.get("label", "")).strip().replace("'", "’")   # label 带单引号会破坏 SQL
        if not v or "'" in v:                                        # 非法编码值直接跳过
            continue
        branches.append(f"WHEN {field} = {v} THEN '{label}'")
    if not branches:
        return ""
    if str(dialect).lower() == "clickhouse":
        else_expr = f"toString({field})"
    else:
        else_expr = f"CAST({field} AS TEXT)"
    snippet = "CASE " + " ".join(branches) + f" ELSE {else_expr} END"
    if len(items) > len(branches):
        snippet += f"  /* 其余 {len(items) - len(branches)} 个取值按上方列表处理 */"
    return snippet


def _build_dict_block(dicts, dict_names, question="", tables=None, table_names=None):
    """
    只把命中的字典拼进去（编码 → 中文含义）

    ★ 为什么必须写明"禁止自行猜测编码"？
      模型看到 app_entity_source 是数字字段，最自然的反应是写 =1 或 =2。
      但真实的渠道编码是 101 / 201 / 702 这种。不给取值，它会生成一条
      "语法正确、结果全错、还不报错"的 SQL —— 这正是最危险的失败模式。
      用户只会看到数字不对，却不知道错在哪。

    ★ 为什么需要 question（大字典优先展示相关取值）？
      字典注入 prompt 时会截断到 MAX_DICT_ITEMS_IN_PROMPT 条。像"资金收支流水类型"
      这类 200+ 条的字典，用户要的取值（如「钻石会员」= 161~166）可能排在 100 名开外
      → 被截掉 → 模型看不到 → 只能拒答或瞎猜。传入 question 后，把与问题有 ≥4 字
      连续重合的取值排到最前，保证相关条目一定进 prompt。question 缺省时行为不变。

    ★ 为什么还要传 tables / table_names？（2026-10-27 新增）
      dict_entries 表里只存了 field_hint（字段名），**没有表作用域**。
      同一个字段名出现在两张表里（如 carstate）时，字典会被无条件注入，
      模型自然就把它套到两张表上 —— 这是"跨表串味"在【查询侧】的入口。
      有了本次注入的表清单，就能确定性地判断"这个字段名本次落在几张表上"，
      从而给出点名警告，而不是笼统叮嘱一句"注意别用错"。
      两个参数缺省为 None → 保持老行为，老调用方一行都不用改。
    """
    if not dict_names:
        return ""
    from rag import MAX_DICT_ITEMS_IN_PROMPT, _lcs_len      # 避免顶层循环依赖

    # ── 字段名 → 本次注入的表里，哪些表真的有这个字段 ──
    hint_tables = {}                       # {"carstate": ["表A", "表B"], ...}
    if tables:
        want = set(table_names or [])
        for t in tables:
            tname = t.get("name") or ""
            if not tname or (want and tname not in want):
                continue
            for col in _parse_table_columns(t):
                hint_tables.setdefault(col, []).append(tname)

    def _render(d, shown):
        field = d.get("field_hint") or ""
        head = f"- {d['name']}" + (f"（用于字段 {field}）" if field else "")
        pairs = " | ".join(f"{i['value']}={i['label']}" for i in shown)
        total = len(d.get("items") or [])
        if total > len(shown):
            pairs += f" | …（共 {total} 项，此处省略 {total - len(shown)} 项）"
        lines = [head, f"  {pairs}"]
        #   ★ 唯一性校验：本字典只对【一张表】成立时才能无条件使用。
        #     落表多张时必须点名是哪几表 —— 逼模型去和表注释对照，
        #     而不是默认"字段名一样就能用"。这条比 H1 的通用说辞更硬：
        #     它给的是本次查询的具体事实。
        owners = (hint_tables.get(field) or []) if field else []
        if len(owners) > 1:
            lines.append(
                f"  ★ 注意：字段 {field} 在本次查询的多张表里都出现（"
                + "、".join(owners)
                + "）。这套编码【只在语义匹配的那张表】上成立，"
                  "禁止套用到其它表的同名/近名字段；"
                  "无法确定是哪张表时，不要用这套编码做过滤。")
        # ★ 输出列转换片段（2026-10-26 需求）：命中 field_hint 的字典附上可抄写的
        #   CASE WHEN，让枚举字段在查询结果/导出里直接是中文，而不是裸编码。
        if field and shown:
            snippet = _case_when_snippet(field, shown)
            if snippet:
                lines.append(
                    "  ★ 输出列必须转中文：SELECT 里该字段作为输出列时，用下面的片段"
                    "（把字段名替换成实际的表别名字段，如 d.type）；"
                    "WHERE 过滤与 GROUP BY 分组仍用编码：\n"
                    f"    {snippet}")
        return "\n".join(lines)

    lines = []
    for d in dicts:
        if d["name"] not in dict_names:
            continue
        items = d.get("items") or []
        if not items:                                        # 老结构兜底：直接用预算好的 text
            if d.get("text"):
                lines.append(d["text"])
            continue
        shown = items[:MAX_DICT_ITEMS_IN_PROMPT]
        if len(items) > MAX_DICT_ITEMS_IN_PROMPT and question:
            rel, seen = [], set()
            for it in items:
                lab = str(it.get("label") or "")
                if lab and it.get("value") not in seen and \
                        (lab in question or _lcs_len(lab, question)[0] >= 4):
                    rel.append(it)
                    seen.add(it.get("value"))
            if rel:                                          # 命中相关取值 → 提前
                rest = [it for it in items if it.get("value") not in seen]
                shown = (rel + rest)[:MAX_DICT_ITEMS_IN_PROMPT]
        lines.append(_render(d, shown))
    if not lines:
        return ""
    return ("本次涉及的【字段取值字典】（编码 → 中文含义）。\n"
            "  当用户用中文名提到某个取值时（例如「车拖车」「安卓」），\n"
            "  必须转成「适用字段 = 对应编码」的过滤条件（如 app_entity_source = 101），\n"
            "  ★ 禁止用其它字段代替（例如把中文名当成公司名去过滤 company_id）\n"
            "  ★ 禁止自行猜测编码，也禁止套用其它字典的编码\n"
            # ★ 输出列转换指令不写死在这里：无 field_hint 片段时不得出现 CASE WHEN 字样
            #   （否则会误导模型去找不存在的片段）——指令由 _render 的片段行自带。
            + "\n".join(lines))


# ═══════════════════════════════════════════════════════════
# 拼"修复 SQL"的提示词（执行失败时用）
# ═══════════════════════════════════════════════════════════
def build_repair_prompt(question, bad_sql, error):
    """
    把报错信息回喂给模型，让它自己修
    ★ 关键：修复时必须用【同一个 system_prompt】，
      否则模型看不到表结构和规则，会在修 A 错误时引入 B 错误。
    """
    return f"""下面这条 SQL 在数据库中执行失败了，请修正它。

用户的问题：{question}

执行失败的 SQL：
{bad_sql}

数据库返回的错误信息：
{error}

请针对错误信息进行修正，只输出修正后的 SQL，不要任何解释，不要用 ``` 包裹。"""


# ═══════════════════════════════════════════════════════════
# 拼"SQL 逻辑自审"的提示词（生成 SQL 后、返回前，额外诊断一次）
# ═══════════════════════════════════════════════════════════
def build_review_prompt(question, sql, definitions="", deep=True):
    """
    让模型对 SELECT 输出列做【字段级逻辑诊断】：口径 / 关联聚合 / 时间窗口 /
    类型除零 / 别名语义是否合理。只给结论，不改 SQL、不重跑。

    ★ deep=True（HIGH 风险）：逐字段诊断，输出完整 fields JSON。
    ★ deep=False（MEDIUM 风险）：只给整体结论 + 风险点，输出更短、更快。

    ★ 为什么单独一层、且只诊断不改 SQL？
      生成 SQL 的模型容易"能跑但口径错"（静默算错最危险）。再多一次
      独立审视，相当于"复核"，专门抓这类逻辑问题。
      但让第二次模型直接改 SQL 有把对的改错的风险（二次幻觉），
      所以本层只产出诊断报告，由人或上层决定是否采纳。
    """
    light_tail = f"""请严格只输出如下 JSON（不要有 ``` 包裹、不要有多余文字）：
{{
  "overall": "一句话整体结论（逻辑是否合理 / 有哪些主要问题）",
  "needs_fix": false,
  "risks": ["值得关注的风险点1", "风险点2"]
}}
其中 needs_fix 为布尔（true 表示有明显逻辑问题建议修改）；不需要逐字段列出 fields。"""
    deep_tail = f"""请严格只输出如下 JSON（不要有 ``` 包裹、不要有多余文字）：
{{
  "overall": "一句话整体结论（逻辑是否合理 / 有哪些主要问题）",
  "needs_fix": false,
  "fields": [
    {{"field": "字段别名或表达式", "logic_ok": true, "comment": "该字段逻辑判断与理由"}},
    {{"field": "另一字段", "logic_ok": false, "comment": "哪里不对、应该怎么改"}}
  ],
  "risks": ["值得关注的风险点1", "风险点2"]
}}
其中 needs_fix 为布尔（true 表示有明显逻辑问题建议修改）；fields 里的 logic_ok 为布尔（true 合理 / false 有问题 / 无法判断可省略该键）。"""

    if not deep:
        return f"""你是一名资深数据分析师与 SQL 审查专家。下面是一条已由系统生成并（尝试）执行的 SQL，请对其【整体业务逻辑】做快速诊断，只关注最可能出错的地方。

用户原始问题：
{question}

最终 SQL：
{sql}

审查要求：
1. 只做诊断，不要修改 SQL、不要重新输出 SQL。
2. 重点判断：计算口径是否符合问题直觉、是否存在明细×明细笛卡尔放大、时间窗口与除零是否有明显错误。
3. 已知字段语义提示（避免误报）：has_1d/has_3d/ordered_after 是 0/1 标记（LEFT JOIN 未匹配为 NULL、SUM(NULL)=0 是对的）；按 customer 预聚合后 1:1 JOIN 是刻意优化。

本次命中指标的业务口径（权威定义，仅供核对，不要修改）：
{definitions if definitions else "（本次无特定指标口径）"}

{light_tail}"""

    return f"""你是一名资深数据分析师与 SQL 审查专家。下面是一条已由系统生成并（尝试）执行的 SQL，请对其【输出字段的业务逻辑】做逐字段审查。

用户原始问题：
{question}

最终 SQL：
{sql}

审查要求：
1. 只做诊断，不要修改 SQL、不要重新输出 SQL、不要解释你的审查方法。
2. 对 SELECT 输出列中的【每一个字段】逐一判断逻辑是否合理，重点检查：
   - 业务口径：计算口径是否符合用户问题的直觉（例如"发单率"应是"发单人数/注册人数"，而不是"订单数/注册人数"）；
   - 关联与聚合：是否存在"明细 × 明细"的笛卡尔放大、是否应为先 1:1 关联再聚合；
   - 时间窗口：时间差单位（天/小时）与边界是否正确、是否用注册日对齐导致结果全 0；
   - 类型与除零：Int32 时间戳是否 toDateTime、除法是否 *1.0 出小数、分母是否可能为空（COALESCE / nullIf）；
   - 别名语义：中文别名是否准确对应表达式含义。
3. 特别区分"语法能跑但口径错"（最危险）与"语法/性能问题"。
4. 已知字段语义提示（避免误报）：
   - 形如 has_1d / has_3d / ordered_after 的字段是「0/1 标记」（由 max(条件) 得到），
     LEFT JOIN 未匹配到时该列为 NULL，SUM(NULL) 按 0 计是正确的，不要报"分子被低估"。
   - new_users 按客户主键 id 已去重，COUNT(*) 即注册人数，不要报"未去重导致虚高"。
   - 按 customer 预聚合后再 1:1 LEFT JOIN（防笛卡尔放大）是刻意优化，不是性能问题。

本次命中指标的业务口径（权威定义，仅供你核对字段逻辑是否合理，不要修改它）：
{definitions if definitions else "（本次无特定指标口径）"}

{deep_tail}"""


# ═══════════════════════════════════════════════════════════
# 拼"需求一致性修复"的提示词（SQL 能跑但没回答对问题时用）
# ═══════════════════════════════════════════════════════════
def build_requirement_repair_prompt(question, bad_sql, findings,
                                    base_question="", base_sql=""):
    """
    需求一致性审核未通过 → 让模型【自己】改，而不是由系统改写 SQL。

    ★ 与 build_repair_prompt 的区别（两者不能混用）：
        build_repair_prompt  = "SQL 执行报错" → 修语法/字段名；
        本函数               = "SQL 能跑但没答对" → 修语义（丢了指标/维度/过滤，或替换写成追加）。

    ★ 为什么把三条硬约束写死在提示里？
      实测中模型最典型的失败不是"不改"，而是"改一处丢三处"：
      让它补回完成率，它把月份分组删了；让它改过滤值，它把原过滤用 OR 追加。
      把"不许删"写成前置约束，比事后一条条纠更省一次往返。
    """
    # ── 逐条列出问题：规则名 + 问题描述 + 修正建议 ──
    lines = []
    for f in findings or []:
        msg = f.get("message") or ""
        sug = f.get("suggestion") or ""
        lines.append(f"- {msg}" + (f"\n  修正方向：{sug}" if sug else ""))
    problems = "\n".join(lines) if lines else "- （未提供具体问题）"

    # ── 增量修改时补上上一轮上下文：否则模型不知道"要保留的原文"长什么样 ──
    base_part = ""
    if base_sql or base_question:
        base_part = (
            "\n上一轮用户的原始问题："
            f"{base_question or '（无）'}\n"
            f"上一轮已确认可用的 SQL：\n{base_sql or '（无）'}\n")

    return f"""你之前生成的 SQL 可以执行，但【没有满足用户的本次需求】。请修正它。

用户本次的要求：{question}
{base_part}
你上一版的 SQL：
{bad_sql}

审核发现的问题：
{problems}

修正时必须遵守（按优先级）：
1) 只针对上面列出的问题做最小改动，不要重写整条查询，不要改动与本问题无关的部分；
2) 用户【没有明确要求删除/替换】的指标、分组维度、过滤条件、时间范围必须原样保留；
3) 用户说"换成 / 只看某个值"时，是【替换】原来的取值，不是用 OR 追加；
4) 去掉某个分组维度时，该字段上的等值过滤条件要保留（除非用户明确要求连过滤一起去掉）；
5) 不要为了绕过问题而删条件、改表或加 LIMIT。

只输出修正后的完整 SQL，不要解释，不要用 ``` 包裹。"""


# ═══════════════════════════════════════════════════════════
# 拼"需求一致性审核"的提示词（语义层：判断 SQL 是否回答了"这次问的问题"）
# ═══════════════════════════════════════════════════════════
def build_requirement_review_prompt(question, sql, contract=None,
                                    base_question="", base_sql="",
                                    experience_digest="", validator_issues=None):
    """
    让模型做【需求一致性裁决】：这条 SQL 是否真的回答了用户这次的要求。

    ★ 为什么要模型来判，而不是继续加规则？
      确定性规则能抓住"值没换""维度没了"这类可枚举的错误；
      但"用户说去掉代驾，模型改成了大板但保留了代驾的状态条件"这种，
      只能靠理解语义。规则穷举不完，模型可以。
    ★ 为什么只让它裁决、不让它输出 SQL？
      一旦允许输出 SQL，就会变成"第二个人写 SQL"，两个模型互相覆盖，
      出问题无从归因。修正永远走"模型自主重新生成"那条既有路径。
    ★ 为什么要喂历史经验？
      审核和生成看到同一批证据，判断才一致；否则会出现"生成时参考了黄金 SQL，
      审核时却说写法不对"的自相矛盾。
    """
    c = contract or {}

    # ── 契约段：把系统已经确定的期望写清楚，避免模型凭感觉判 ──
    def _j(v):
        return json.dumps(v, ensure_ascii=False)

    contract_lines = []
    if c.get("metrics"):
        contract_lines.append(f"- 必须出现在输出里的指标：{_j(c['metrics'])}")
    if c.get("dimensions"):
        contract_lines.append(f"- 必须按其分组的维度：{_j(c['dimensions'])}")
    if (c.get("must_keep") or {}).get("filters"):
        contract_lines.append(
            f"- 必须保留的过滤条件（用户没让删）：{_j((c['must_keep'] or {})['filters'])}")
    if (c.get("must_remove") or {}).get("dimensions"):
        contract_lines.append(
            f"- 用户要求去掉的维度：{_j((c['must_remove'] or {})['dimensions'])}")
    if c.get("replace_pairs"):
        pairs = [f"{p.get('field')} 从 {p.get('old')} 换成 {p.get('new')}"
                 for p in c["replace_pairs"]]
        contract_lines.append(f"- 本轮是【替换】不是追加：{_j(pairs)}")
    if c.get("time_range"):
        contract_lines.append(f"- 时间范围：{c['time_range']}")
    if c.get("unknown"):
        contract_lines.append(f"- 系统自己也没把握的地方：{_j(c['unknown'])}")
    if c.get("source") != "modify":
        contract_lines.append("- 这是【首轮查询】，没有上一轮需要继承的条件")
    contract_block = "\n".join(contract_lines) or "（本次没有可结构化的约束）"

    # ── 上一轮上下文：没有就不提，避免模型对着"（无）"发散 ──
    base_block = ""
    if base_sql or base_question:
        base_block = f"""
上一轮用户问题：{base_question or "（无）"}
上一轮已确认可用的 SQL：
{base_sql or "（无）"}
"""

    # ── 确定性校验已发现的问题：让模型别重复报，也别放过 ──
    issues_block = ""
    if validator_issues:
        lines = [f"- [{i.get('rule')}] {i.get('message')}" for i in validator_issues]
        issues_block = "\n规则校验已发现（供你参考，不要重复罗列）：\n" + "\n".join(lines) + "\n"

    exp_block = ""
    if experience_digest:
        exp_block = f"""
{experience_digest}
"""

    return f"""你是一名资深数据分析师，负责【需求一致性审核】。
判断下面这条 SQL 是否真正回答了用户本次的要求。你只做裁决，不修改 SQL。

用户本次的要求：{question}
{base_block}
待审核的 SQL：
{sql}

系统已确定的约束（作为裁决依据）：
{contract_block}
{issues_block}{exp_block}
请逐项判断，重点看这些【规则抓不到】的问题：
1. 用户本次的意图是否被正确理解（有没有答非所问、答成了另一个指标/另一个口径）；
2. 是否正确继承了上一轮的条件（本轮没提的指标/维度/过滤，是否被意外丢弃）；
3. 用户本次的修改是否真正落实（改指标、改时间、改过滤、改分组）；
4. 该【替换】的条件是否被写成了【追加】（新旧取值同时存在 = 放大范围）；
5. 是否误删了原有指标、维度或过滤条件；
6. JOIN 关系、关联基数与数据粒度是否合理（有没有明细×明细放大、粒度错配）；
7. 是否命中"已知错误模式"（若上方给了失败经验，逐条对照）。

判定标准（严格但不要臆测）：
- fail：存在会让用户拿到**错误答案**的具体问题，且你能指出在 SQL 的哪里；
- unsure：信息不足以判断（例如用户表述本身有歧义），需要用户补充确认；
- pass：未发现上述问题。

★ 只输出如下 JSON（不要 ``` 包裹、不要输出 SQL、不要多余文字）：
{{
  "verdict": "pass|fail|unsure",
  "findings": [
    {{"code": "intent_misread|context_lost|change_not_applied|replace_as_append|dropped_metric|dropped_filter|grain_wrong|join_cardinality|metric_caliber|known_failure",
      "severity": "error|warning",
      "what": "一句话说清问题（给用户/模型看，不要写技术黑话）",
      "evidence": "SQL 中对应的片段或位置",
      "fix": "应该怎么改（用文字描述，不要写 SQL）"}}
  ],
  "assumptions": ["为了通过审核，你假设了什么（如果有）"]
}}
其中 severity=error 表示会导致答案错误；warning 表示可疑但不必然错。没有问题时 findings 为空数组。"""
