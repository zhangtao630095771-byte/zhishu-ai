# -*- coding: utf-8 -*-
"""
prompt.py —— Prompt 组装
★ 职责：把"表结构 + 指标口径 + 规则"拼成发给大模型的 System Prompt
★ ★★ 重要变化：规则【不再写在本文件里】，而是从 config_store 读数据库 ★★★
    以前：改规则要改这个文件 → 改代码 → 重启 → 重新部署
    现在：改规则用 admin.py 写数据库 → 下次调用立即生效
"""
import config          # 取 SQL 方言等配置
import config_store    # ★ 配置存储：规则从这里读


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


def _build_dict_block(dicts, dict_names, question=""):
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
    """
    if not dict_names:
        return ""
    from rag import MAX_DICT_ITEMS_IN_PROMPT, _lcs_len      # 避免顶层循环依赖

    def _render(d, shown):
        field = d.get("field_hint") or ""
        head = f"- {d['name']}" + (f"（用于字段 {field}）" if field else "")
        pairs = " | ".join(f"{i['value']}={i['label']}" for i in shown)
        total = len(d.get("items") or [])
        if total > len(shown):
            pairs += f" | …（共 {total} 项，此处省略 {total - len(shown)} 项）"
        lines = [head, f"  {pairs}"]
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
# ★ 组装完整的 System Prompt
# ═══════════════════════════════════════════════════════════
def build_system_prompt(tables, metrics, table_names, metric_names,
                        dicts=None, dict_names=None, question=""):
    """
    把四样东西拼在一起：
        ① 身份（Role）        ← 从配置库读
        ② 上下文（表结构 + 指标口径 + 字段取值字典）← 运行时检索得到
        ③ 规则（Constraints） ← 从配置库读
    ★ 这里的角色和规则都来自数据库，改完立即生效

    ★ dicts / dict_names 是可选参数（默认 None）：
      这样老的调用方（比如评测脚本）一行都不用改，仍然能正常工作。
    """
    # ── 从配置库读角色和规则 ──
    role = config_store.get_prompt("role")                  # 取角色描述
    role = role.format(dialect=config.SQL_DIALECT)          # 替换 {dialect} 占位符
    #       str.format(变量=值) = 把字符串里的 {变量} 替换成值
    rules = config_store.get_prompt("sql_rules")            # 取规则文本

    # ── 拼上下文 ──
    schema_text = _build_schema_block(tables, table_names)
    metric_text = _build_metric_block(metrics, metric_names)
    dict_text = _build_dict_block(dicts or [], dict_names or [], question)

    # 字典块只在命中时才出现（没命中就整段不出现，不留空标题）
    dict_section = f"\n# {dict_text}\n" if dict_text else ""

    # ── 组装 ──
    return f"""{role}

# 可用的表结构（只提供与本次问题相关的表）
{schema_text}
{dict_section}
# {metric_text}

{rules}
"""


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
def build_review_prompt(question, sql, definitions=""):
    """
    让模型对 SELECT 输出列做【字段级逻辑诊断】：口径 / 关联聚合 / 时间窗口 /
    类型除零 / 别名语义是否合理。只给结论，不改 SQL、不重跑。

    ★ 为什么单独一层、且只诊断不改 SQL？
      生成 SQL 的模型容易"能跑但口径错"（静默算错最危险）。再多一次
      独立审视，相当于"复核"，专门抓这类逻辑问题。
      但让第二次模型直接改 SQL 有把对的改错的风险（二次幻觉），
      所以本层只产出诊断报告，由人或上层决定是否采纳。
    """
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

请严格只输出如下 JSON（不要有 ``` 包裹、不要有多余文字）：
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
