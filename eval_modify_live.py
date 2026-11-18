# -*- coding: utf-8 -*-
"""
eval_modify_live.py —— 用【真实自定义模型（DeepSeek）】回归验证"当前窗口增量修改"能力

★ 为什么单独做这个脚本（而不是塞进单测）
  单测里用的是"受控模型替身"，只能验证【接线 + 安全闸门重跑】；
  但"模型到底会不会保留未改的指标/时间/过滤、会不会正确去掉分组却留住等值过滤"，
  必须用真实模型才能验证 —— 这正是本次专项修复最不确定的部分。

★ 这个脚本同时是【需求一致性审核】的评测基座（design: docs/requirement_consistency_2026-10-30.md §5.3）
  每跑完一个场景，除了人工断言，还会把生成的 SQL 喂给 `requirement_review.review(...)`（只跑确定性层），
  于是同一批数据能同时算出两个上线决策指标：
    · req_pass_rate    —— 审核器判 pass 的比例（基线）
    · false_block_rate  —— 人工断言全过、审核器却判 fail 的比例（必须 < 5%，否则开 BLOCK=1 会大面积误拒）
  ★ 为什么审核器也要一起评？因为"审核器对不对"和"模型对不对"是两个问题。
    只看模型通过率，会把审核器的误拦藏在"场景未通过"里看不出来。

★ 测什么（15 个场景）
  ①~⑩ 任务点名的 10 类增量修改（删维度/增指标/改指标/改时间/改过滤/删过滤/改口径/报错纠错/追加/改粒度）
  E1~E5 需求一致性专杀场景（极简改过滤 / 删分组留过滤 / 增指标 / 口径切换不留残留 / 指代消解）
  ★ E1/E3 的问句与 ⑤/② 表述相近，是**故意的对照组**：
    同一条 SQL 换个说法就该得到同一个结论；若 ⑤ 过而 E1 被审核器拦，说明审核器对同义表述不稳定。

★ 三类断言（报告里分组展示，避免混在一起看不出重点）
  · must_keep   —— 人工写的"必须仍在 SQL 里"的片段（少一个就是漏改/误删）
  · must_remove —— "必须已经消失"的片段（如替换后的旧值、改后的旧上界）
  · checks      —— 结构化判定（分组键里有没有某字段、是否出现 DISTINCT 等），函数返回 bool

★ 判定器统一"先剥注释"（修 G8）
  老版本用 `"app_entily_source" not in sql` 判"字段名已修正"，
  模型在注释里写一行 `-- app_entily_source → app_entity_source`、正文全对，却被判失败。
  现在所有断言只跑在 `judge_sql()` 的结果上：剥注释 + 压空白 + 等号两侧归一 + 大小写无关。

★ 怎么跑
  · 真实模型（默认，会花 token，请在折扣时段跑）：  .venv/bin/python eval_modify_live.py
  · 空跑自检（不调模型，验证报告格式与断言逻辑）： .venv/bin/python eval_modify_live.py --dry-run

★ 输出
  同目录 eval_modify_live_report.md（含每场景的模型 SQL + 逐项判定 + 审核器裁决 + 结论）
"""
import os
import re
import sys
import datetime

import config          # 配置（含 DEEPSEEK_API_KEY，经 .env 自动加载）
import config_store    # 角色 Prompt（真实口径）
import agent           # _modify_prompt_block：本次修复注入的"增量修改约束段"
import llm             # 真实模型调用
import permission      # _parse_sql：验证生成的 SQL 能通过字段级权限解析（本次解析修复）
import requirement_review  # ★ 需求一致性审核（第六维）：只用于评测，不参与生成


# ═══════════════════════════════════════════════════════════
# 基准上下文：任务点名的经典场景「注册客户数，按月份和应用主体分组」
#   user_type=1（客户）、app_entity_source=0（应用主体）、时间 2025-10-01 ~ 2026-11-01 左闭右开
# ═══════════════════════════════════════════════════════════
BASE_QUESTION = "注册客户数，按月份和应用主体分组"
BASE_SQL = (
    "SELECT r.app_entity_source AS \"应用主体\", "
    "toStartOfMonth(r.create_time) AS \"月份\", "
    "count(*) AS \"注册客户数\" "
    "FROM ctc_data_warehouse.register r "
    "WHERE r.user_type = 1 AND r.app_entity_source = 0 "
    "AND r.create_time >= '2025-10-01' AND r.create_time < '2026-11-01' "
    "GROUP BY r.app_entity_source, toStartOfMonth(r.create_time) "
    "ORDER BY toStartOfMonth(r.create_time) "
    "FORMAT TabSeparatedWithNames"
)
BASE_UNDERSTANDING = {
    "intent": "summary",
    "metrics": ["注册客户数"],
    "entities": ["register"],
    "dimensions": ["月份", "应用主体"],
    "time_range": "2025-10-01 ~ 2026-11-01（左闭右开）",
    "filters": ["user_type = 1", "app_entity_source = 0"],
}

# E4「口径切换」用一份不同的基准：上一轮看的是【代驾】(102)，本轮要求换到【大板】(201)。
#   ★ 为什么要单独一份基准？"不要看代驾了"这类否定式切换，只有在"上一轮确实是代驾"时才成立。
#     沿用 =0 的基准去问"不要看代驾了"，模型面对的是一个本就不含代驾的查询，测不出"残留过滤"。
BASE_SQL_DAIJIA = BASE_SQL.replace("app_entity_source = 0", "app_entity_source = 102")
BASE_U_DAIJIA = dict(
    BASE_UNDERSTANDING,
    filters=["user_type = 1", "app_entity_source = 102"],
)

# ⑧「报错纠错」用一份**上一轮真的把字段名写错了**的基准。
#   ★ 为什么必须换基准？（第二次踩）
#     报错纠错场景的前提是"上轮 SQL 有错"。若沿用正确的 BASE_SQL，
#     模型会（非常正确地）指出"报错里的 app_entily_source 与上一轮 SQL 的
#     app_entity_source 对不上，请确认报错来源"然后拒答 —— 这是夹具不自洽，不是模型错。
BASE_SQL_TYPO = BASE_SQL.replace("app_entity_source", "app_entily_source")

# 角色 + 表/指标知识（真实角色 Prompt 来自 config_store；表结构/指标口径为任务场景的忠实复现）
SCHEMA_BLOCK = """# 数据表与指标口径（权威知识）

## 表：ctc_data_warehouse.register（别名 r）—— 用户注册明细
- user_id：用户唯一标识（UInt64），去重统计用此字段
- create_time：注册时间（DateTime），按此字段做时间过滤与月份/周分组
- user_type：用户类型（1=客户，2=司机，6=其他）
- app_entity_source：应用主体来源（0=默认主体，101=车拖车，102=代驾，201=大板）
- is_complete：是否完成注册（0/1）

## 权威指标
- 注册客户数：指定时间段内新注册的客户数。口径：count(*) FROM register WHERE user_type = 1
- 完成率：完成注册的客户占比。口径：sumIf(is_complete=1)/count(*)
- 注册司机数：指定时间段内新注册的司机数。口径：count(*) FROM register WHERE user_type = 2
"""


def build_modify_context(base_sql=BASE_SQL, base_u=BASE_UNDERSTANDING):
    """构造一份"上一轮查询上下文"，供增量修改锚定（与 server._modify_query 组装的字段一致）。"""
    return {
        "base_question": BASE_QUESTION,
        "base_understanding": base_u,
        "base_sql": base_sql,
        "base_tables": ["ctc_data_warehouse.register"],
        "base_metrics": base_u["metrics"],
    }


def build_system_prompt(question, modify_context):
    """拼出与真实流水线同构的 system prompt：角色 + 知识 + 本次修复的增量修改约束段。"""
    role = config_store.get_prompt("role").format(dialect="ClickHouse")  # 真实角色口令
    modify_block = agent._modify_prompt_block(question, modify_context)  # ★ 本次修复新增的约束段
    return "\n\n".join([role, SCHEMA_BLOCK, modify_block])


def extract_sql(text):
    """从模型输出里抠出 SQL（去掉 ``` 代码围栏与首尾空白，兼容多行）。"""
    t = (text or "").strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    return t


def parseable(sql):
    """验证生成的 SQL 能通过字段级权限解析（复现原 'Invalid expression' 已修复）。"""
    if not sql:
        return False
    try:
        return permission._parse_sql(sql) is not None
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
# 判定器：所有断言都跑在归一化后的正文上（修 G8）
# ═══════════════════════════════════════════════════════════
def _norm(s):
    """压空白 + 等号两侧归一 + 去首尾空格（不做大小写变换，大小写由 _has 处理）。

    ★ 为什么等号两侧要归一？
      模型写 `r.app_entity_source=0` 和 `r.app_entity_source = 0` 是同一件事，
      断言片段只写一种形态就会漏判 —— 这属于"评测器自己不可靠"，比模型错更危险。
    ★ 为什么用负向后行断言 `(?<![<>!=])`？
      ClickHouse 里 `>=` `<=` `!=` `<>` 都含 `=`。若不排除，`create_time >= '2025-10-01'`
      会被改写成 `create_time > = '2025-10-01'`，后续所有时间断言全部失效。
    """
    s = re.sub(r"\s+", " ", str(s or ""))
    s = re.sub(r"(?<![<>!=])\s*=\s*(?!=)", " = ", s)
    return s.strip()


def judge_sql(sql):
    """判定用正文：剥掉 `--` / `/* */` 注释，再归一化。

    ★ 这是 G8 的修法：注释里出现危险词（如错误字段名）属于"解释"，不是"事实"。
    """
    return _norm(requirement_review.strip_comments(sql))


def _has(body, frag):
    """正文里是否出现该片段（大小写无关，两侧都归一化）。"""
    return _norm(frag).lower() in body.lower()


def group_by_part(body):
    """取 GROUP BY 到 ORDER BY / HAVING / LIMIT / FORMAT 之间的片段；无 GROUP BY 返回 ""。

    ★ 为什么要专门抽这一段？"不再按应用主体分组"不能用 `"app_entity_source" not in sql` 判 ——
      该字段在 WHERE 里必须留着（`= 0` 等值过滤），只能看分组键里还有没有它。
    """
    m = re.search(
        r"\bGROUP\s+BY\b(.*?)(?:\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|\bFORMAT\b|\bSETTINGS\b|$)",
        body, re.S | re.I)
    return m.group(1) if m else ""


def has_closed_range(body):
    """是否同时存在 `create_time >= …` 与 `create_time < …`（左闭右开区间）。

    ★ 为什么不断言"边界是日期字面量"？（真实踩到过）
      模型对"时间改到上个月"给出的是 `toStartOfMonth(now() - INTERVAL 1 MONTH)` /
      `< toStartOfMonth(now())` —— 这比写死日期更好（查询不会随日期失效），
      但老断言要求 `'\\d{4}-\\d{2}-\\d{2}'`，于是把更优解判成未通过。
    ★ 为什么也不断言具体日期？
      "上个月"是哪天取决于跑评测的当天，写死会让结果随日期漂移（评测必须可重复）。
      所以这里只守"形态"：两界都还在、且不是 `<=`；旧边界是否真的消失由 must_remove 保证。
    """
    lb = re.search(r"create_time\s*>=\s*\S", body, re.I)
    ub = re.search(r"create_time\s*<(?!\s*=)\s*\S", body, re.I)
    return bool(lb and ub)


# ═══════════════════════════════════════════════════════════
# 场景表：每个场景 = 名称 + 问句 + must_keep + must_remove + checks(+ 可选基准覆盖)
#   must_keep / must_remove 是字符串片段；checks 是 (说明, 函数) 且函数入参是【判定正文】
# ═══════════════════════════════════════════════════════════
SCENARIOS = [
    {
        "name": "① 删分组维度（去掉应用主体分组，只按月份）",
        "question": "去掉应用主体分组，只按月份统计",
        "must_keep": ["app_entity_source = 0", "toStartOfMonth", "注册客户数",
                      "user_type = 1", "2025-10-01", "2026-11-01"],
        "must_remove": [],
        "checks": [
            ("不再按应用主体分组（GROUP BY 键里没有该字段）",
             lambda s: "app_entity_source" not in group_by_part(s)),
            ("仍有 GROUP BY", lambda s: bool(group_by_part(s).strip())),
        ],
    },
    {
        "name": "② 增指标（加上完成率）",
        "question": "再加上完成率",
        "must_keep": ["注册客户数", "完成率", "user_type = 1",
                      "app_entity_source = 0", "toStartOfMonth", "2025-10-01"],
        "must_remove": [],
        "checks": [],
    },
    {
        "name": "③ 改指标（换成注册司机数）",
        "question": "把注册客户数改成注册司机数",
        "must_keep": ["注册司机数", "user_type = 2", "2025-10-01", "app_entity_source = 0"],
        "must_remove": ["user_type = 1"],   # ★ 真替换：客户口径必须消失，不能两个口径并存
        "checks": [],
    },
    {
        "name": "④ 改时间范围（结束时间到 2026-12-01）",
        "question": "时间范围改成到 2026-12-01",
        "must_keep": ["2026-12-01", "2025-10-01", "注册客户数",
                      "app_entity_source = 0", "user_type = 1"],
        "must_remove": ["2026-11-01"],      # ★ 旧上界必须消失（只加不删 = 区间被悄悄放大）
        "checks": [],
    },
    {
        "name": "⑤ 改过滤（app_entity_source 0 → 101）",
        "question": "应用主体只看 101，其它不变",
        "must_keep": ["app_entity_source = 101", "注册客户数", "toStartOfMonth",
                      "2025-10-01", "user_type = 1"],
        "must_remove": ["app_entity_source = 0"],   # ★ 替换不是 OR 追加
        "checks": [],
    },
    {
        "name": "⑥ 删过滤（去掉 app_entity_source 过滤）",
        "question": "去掉应用主体等于 0 这个过滤条件",
        "must_keep": ["2025-10-01", "user_type = 1", "注册客户数", "toStartOfMonth"],
        "must_remove": ["app_entity_source = 0"],
        "checks": [],
    },
    {
        "name": "⑦ 改统计口径（count → count distinct id）",
        "question": "注册客户数要按去重客户 id 统计",
        "must_keep": ["注册客户数", "app_entity_source = 0", "2025-10-01", "user_type = 1"],
        "must_remove": [],
        "checks": [
            ("含 DISTINCT / uniqExact 等去重口径",
             lambda s: "DISTINCT" in s.upper() or "uniqExact" in s),
        ],
    },
    {
        "name": "⑧ 报错纠错（基于错误信息修正）",
        "question": "SQL 报错：DB::Exception: Unknown identifier r.app_entily_source。帮我改对字段名",
        "base_sql": BASE_SQL_TYPO,       # 上一轮真的拼错了，报错才成立（见 BASE_SQL_TYPO 注释）
        "must_keep": ["app_entity_source", "注册客户数", "2025-10-01"],
        "must_remove": ["app_entily_source"],   # ★ 剥注释后才能安全断言（原 G8 假阴性）
        "checks": [],
    },
    {
        "name": "⑨ 追加问句（再加一个指标，需保留原指标）",
        "question": "这个查询不错，再帮我加一个完成率",
        "must_keep": ["注册客户数", "完成率", "app_entity_source = 0"],
        "must_remove": [],
        "checks": [],
    },
    {
        "name": "⑩ 改分组粒度（按月份 → 按周）",
        "question": "按周统计，不要按月份了",
        "must_keep": ["toStartOfWeek", "注册客户数", "app_entity_source = 0", "2025-10-01"],
        "must_remove": ["toStartOfMonth"],
        "checks": [],
    },

    # ── E1~E5：需求一致性专杀（design §5.3）──────────────────
    {
        "name": "E1 极简改过滤（只说「只看 101」）",
        "question": "只看 101",
        "must_keep": ["app_entity_source = 101", "注册客户数", "toStartOfMonth", "user_type = 1"],
        "must_remove": ["app_entity_source = 0"],
        "checks": [
            ("仍按应用主体分组（用户没要求删分组）",
             lambda s: "app_entity_source" in group_by_part(s)),
        ],
    },
    {
        "name": "E2 删分组维度但必须留住等值过滤",
        "question": "去掉应用主体分组",
        "must_keep": ["app_entity_source = 0", "toStartOfMonth", "注册客户数", "2025-10-01"],
        "must_remove": [],
        "checks": [
            ("GROUP BY 键里没有 app_entity_source",
             lambda s: "app_entity_source" not in group_by_part(s)),
            ("仍有 GROUP BY（不能退化成一行汇总）",
             lambda s: bool(group_by_part(s).strip())),
        ],
    },
    {
        "name": "E3 增指标（保留原指标）",
        "question": "再加上完成率",
        "must_keep": ["注册客户数", "完成率", "toStartOfMonth",
                      "user_type = 1", "app_entity_source = 0"],
        "must_remove": [],
        "checks": [],
    },
    {
        "name": "E4 口径切换（代驾 → 大板，不留残留过滤）",
        "question": "不要看代驾了，只看大板",
        "base_sql": BASE_SQL_DAIJIA,
        "base_understanding": BASE_U_DAIJIA,
        "must_keep": ["app_entity_source = 201", "注册客户数", "toStartOfMonth", "user_type = 1"],
        "must_remove": ["app_entity_source = 102"],   # ★ 旧口径不能以 OR 形式残留
        "checks": [],
    },
    {
        "name": "E5 指代消解（「上面那个查询，时间改到上个月」）",
        "question": "上面那个查询，时间改到上个月",
        "must_keep": ["注册客户数", "toStartOfMonth", "app_entity_source = 0", "user_type = 1"],
        "must_remove": ["2025-10-01", "2026-11-01"],  # ★ 两界都要变，不能只改一头
        "checks": [
            ("仍是左闭右开封闭区间（>= 与 < 都在）", has_closed_range),
        ],
    },
]


# ═══════════════════════════════════════════════════════════
# 每场景的「本轮查询理解」夹具（审核器探针的输入）
#   ★ 为什么手写、而不是现场调 query_understanding？
#     1) 每场景多调一次模型 = 成本翻倍、结果不可复现，评测结论就无法归因到"审核器"；
#     2) 真实流水线里 understanding 就是模型对"本轮用户到底要什么"的正式解析结果，
#        手写一份忠实夹具，等于把"解析正确时审核器的表现"单独隔离出来量。
#   ★ 只写本轮【明确提出】的字段；没写的由 build_contract 自动从上一轮继承。
#     这也是防误拦的关键：继承逻辑一旦写错，正确 SQL 会被判成"丢了原条件"。
#   ★ key 用场景名首段（"①" / "E1"），避免把 15 个长中文名抄两遍抄错。
# ═══════════════════════════════════════════════════════════
UNDERSTANDING = {
    # ① 只按月份 → 维度只剩月份；过滤一条都不许丢（这是本场景的考点）
    "①": {"metrics": ["注册客户数"], "dimensions": ["月份"],
          "filters": ["user_type = 1", "app_entity_source = 0"]},
    # ② 加指标 → 新旧两个指标都要在
    "②": {"metrics": ["注册客户数", "完成率"], "dimensions": ["月份", "应用主体"]},
    # ③ 换指标 → 指标变司机数，user_type 随之从 1 变 2（不写这条会被 R3 正确判"丢了 user_type=1"）
    "③": {"metrics": ["注册司机数"], "dimensions": ["月份", "应用主体"],
          "filters": ["user_type = 2", "app_entity_source = 0"]},
    # ④ 只改时间上界；下界不动
    "④": {"time_range": "2025-10-01 ~ 2026-12-01（左闭右开）"},
    # ⑤ 换过滤值
    "⑤": {"filters": ["user_type = 1", "app_entity_source = 101"]},
    # ⑥ 删掉一条过滤（只剩 user_type）
    "⑥": {"filters": ["user_type = 1"]},
    # ⑦ 只改统计口径，条件全不动 → 给了等于没给，正好验证"继承不误伤"
    "⑦": {"metrics": ["注册客户数"], "dimensions": ["月份", "应用主体"],
          "filters": ["user_type = 1", "app_entity_source = 0"]},
    # ⑧ 报错纠错：意图与上一轮完全一致（不写 = 全继承）
    "⑧": None,
    # ⑨ 追加指标
    "⑨": {"metrics": ["注册客户数", "完成率"]},
    # ⑩ 换粒度：周替代月
    "⑩": {"dimensions": ["周", "应用主体"]},
    # E1 极简（问句里没有字段名，只有 101）→ 靠 filters 夹具补全语义
    "E1": {"filters": ["user_type = 1", "app_entity_source = 101"]},
    # E2 去掉应用主体分组 → 维度只剩月份
    "E2": {"metrics": ["注册客户数"], "dimensions": ["月份"],
           "filters": ["user_type = 1", "app_entity_source = 0"]},
    # E3 同 ②（对照组：同一件事不同说法，审核结论必须一致）
    "E3": {"metrics": ["注册客户数", "完成率"], "dimensions": ["月份", "应用主体"]},
    # E4 口径切换：黑话"大板"被解析成 201
    "E4": {"filters": ["user_type = 1", "app_entity_source = 201"]},
    # E5 时间改到"上个月"→ 具体日期取决于跑评测的当天，离线无法写死。
    #    这里显式给一句无日期的表述，让 R6（时间窗）**主动跳过**而不是拿上一轮的
    #    旧日期去要求新 SQL —— 否则"正确改了时间"反而会被判"时间窗未落实"，信号是反的。
    "E5": {"time_range": "上个月（相对时间，评测不写死具体日期）"},
}


def _uid(sc):
    """取场景编号（"① 删分组…" → "①"），用于查 UNDERSTANDING 夹具。"""
    return sc["name"].split()[0]


# 空跑自检名单：这些场景的基准 SQL **明显没有执行用户要求的修改**
# （没加完成率、没换指标、没改过滤值、没换口径），审核器必须判 fail。
# ★ 为什么要守这一条？审核器最容易的"腐化"方式不是报错，而是悄悄变宽松 ——
#   改一条规则的 severity、少写一个 continue，就会变成"永远 pass"，而真实评测里
#   它只会表现为"这次模型表现不错"。空跑是唯一零成本的守门人。
SELFTEST_MUST_FAIL = {"②", "③", "⑤", "⑨", "E1", "E3", "E4"}


# ═══════════════════════════════════════════════════════════
# 审核器探针：把生成的 SQL 喂给确定性层，量 req_pass_rate / false_block_rate
# ═══════════════════════════════════════════════════════════
def audit_sql(sc, ctx, body):
    """对本场景的 SQL 跑需求一致性审核（只跑确定性层，use_llm=False）。

    返回 dict：verdict / codes / degraded / error
    ★ 为什么只跑确定性层？语义层要额外一次模型调用，评测成本翻倍；
      而 false_block_rate 主要由确定性规则造成（规则写太严才会误拦），量它就够。
    """
    try:
        contract = requirement_review.build_contract(
            sc["question"], UNDERSTANDING.get(_uid(sc)), ctx)
        rev = requirement_review.review(body, contract, use_llm=False)
        codes = [i.get("rule", "?") for i in (rev.get("issues") or [])]
        return {"verdict": rev.get("verdict"), "codes": codes,
                "degraded": bool(rev.get("degraded")), "error": ""}
    except Exception as e:                      # 审核器自己崩了必须如实记录，不能当成通过
        return {"verdict": "error", "codes": [], "degraded": True,
                "error": f"{type(e).__name__}: {str(e)[:160]}"}



# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
def run(dry_run):
    rows = []  # 每场景的结果记录
    for sc in SCENARIOS:
        # 场景可覆盖基准（E4 需要"上一轮是代驾"的上下文）
        ctx = build_modify_context(sc.get("base_sql", BASE_SQL),
                                   sc.get("base_understanding", BASE_UNDERSTANDING))
        prompt = build_system_prompt(sc["question"], ctx)

        if dry_run:
            # 空跑：不调模型，用该场景的基准 SQL 当"模型输出"，仅验证报告管线与断言逻辑。
            #   ★ 注意：空跑下绝大多数修改场景本就应该"未通过"（基准 SQL 没做任何修改），
            #     这不是缺陷，而是证明断言真的在起作用（否则断言形同虚设）。
            sql = sc.get("base_sql", BASE_SQL)
            note = "（--dry-run 空跑：未调用真实模型，用基准 SQL 占位；修改类场景预期不通过）"
            err = ""
        else:
            try:
                raw = llm.chat(prompt, sc["question"])
                sql = extract_sql(raw)
                note = ""
                err = ""
            except Exception as e:
                sql = ""
                note = f"调用失败：{type(e).__name__}: {str(e)[:200]}"
                err = str(e)

        body = judge_sql(sql) if sql else ""     # ★ 所有断言只看这一份正文

        # 三类断言分开收集，报告里分组展示（混在一起会看不出是"漏改"还是"多改"）
        keeps = [(f"必须保留 `{f}`", _has(body, f)) for f in sc.get("must_keep") or []]
        drops = [(f"必须消失 `{f}`", not _has(body, f)) for f in sc.get("must_remove") or []]
        checks = [(label, bool(fn(body))) for label, fn in sc.get("checks") or []]
        if not sql:                              # 没拿到 SQL 时，所有断言一律算不通过
            keeps = [(l, False) for l, _ in keeps]
            drops = [(l, False) for l, _ in drops]
            checks = [(l, False) for l, _ in checks]

        ok_all = bool(sql) and not err and all(
            o for _, o in (keeps + drops + checks))
        audit = audit_sql(sc, ctx, body) if body else {
            "verdict": "skipped", "codes": [], "degraded": True, "error": ""}

        rows.append({
            "name": sc["name"], "question": sc["question"], "uid": _uid(sc),
            "sql": sql, "body": body,
            "keeps": keeps, "drops": drops, "checks": checks,
            "ok": ok_all, "parseable": parseable(sql),
            "audit": audit, "note": note, "err": err,
            "is_e": sc["name"].startswith("E"),
        })

    _write_report(rows, dry_run)
    return rows


def _metrics(rows):
    """算两个上线决策指标。

    ★ req_pass_rate 的分母是"真的拿到 SQL 的场景"，不是全部场景 ——
      模型调用失败（网络/超时）不属于审核器的表现，混进去会污染基线。
    ★ false_block 只统计"人工断言全过"的场景，这才是"合法 SQL 被拦"的真实口径。
    """
    with_sql = [r for r in rows if r["sql"] and r["audit"]["verdict"] != "skipped"]
    passed = [r for r in with_sql if r["audit"]["verdict"] == "pass"]
    human_ok = [r for r in with_sql if r["ok"]]
    false_block = [r for r in human_ok if r["audit"]["verdict"] == "fail"]
    return {
        "n": len(rows),
        "human_ok": sum(1 for r in rows if r["ok"]),
        "audited": len(with_sql),
        "req_pass": len(passed),
        "req_pass_rate": (len(passed) / len(with_sql) * 100) if with_sql else 0.0,
        "false_block": len(false_block),
        "false_block_rate": (len(false_block) / len(human_ok) * 100) if human_ok else 0.0,
        "false_block_names": [r["name"] for r in false_block],
    }


def _write_report(rows, dry_run):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    m = _metrics(rows)
    lines = []
    lines.append("# 增量修改 · 真实模型回归报告")
    lines.append("")
    lines.append(f"- 生成时间：{ts}")
    lines.append(f"- 模型：{config.LLM_MODEL}（{config.LLM_BASE_URL}）")
    lines.append(f"- 模式：{'--dry-run 空跑（未花 token）' if dry_run else '真实模型调用'}")
    lines.append(f"- 场景数：{m['n']}（①~⑩ 增量修改 10 个 + E1~E5 需求一致性 5 个）")
    lines.append(f"- 人工断言：{m['human_ok']}/{m['n']} 场景整体通过")
    lines.append(f"- 审核器 `req_pass_rate`：{m['req_pass']}/{m['audited']}"
                 f"（{m['req_pass_rate']:.1f}%）")
    lines.append(f"- 审核器 `false_block_rate`：{m['false_block']}/{m['human_ok']}"
                 f"（{m['false_block_rate']:.1f}%，上线门槛 < 5%）")
    if m["false_block_names"]:
        lines.append(f"  - 误拦候选：{'；'.join(m['false_block_names'])}")
    lines.append("")
    if dry_run:
        # 空跑的核心价值：验证"审核器不是摆设"（见 SELFTEST_MUST_FAIL 注释）
        wrong = [r["name"] for r in rows
                 if r["uid"] in SELFTEST_MUST_FAIL and r["audit"]["verdict"] != "fail"]
        lines.append(f"- 审核器自检：{len(SELFTEST_MUST_FAIL) - len(wrong)}/{len(SELFTEST_MUST_FAIL)}"
                     f" —— 基准 SQL 明显答非所问时，审核器必须判 fail")
        if wrong:
            lines.append(f"  - ❌ 这些场景本该 fail 却判通过（规则被改松了）：{'；'.join(wrong)}")
        lines.append("")
    lines.append("## 说明")
    lines.append("本报告只验证【真实模型在增量修改约束下的 SQL 生成】是否符合预期；")
    lines.append("SQL 未对真实库执行（执行前仍会走完整安全闸门，已由 test_modify_query.py 覆盖）。")
    lines.append("")
    lines.append("判定口径：所有断言都跑在【剥注释 + 压空白 + 等号归一】后的正文上"
                 "（修 G8 —— 老版本会把注释里的错误字段名当成「还没改」）。")
    lines.append("审核器口径：只跑 `requirement_review` 确定性层（`use_llm=False`），"
                 "语义层需额外一次模型调用，不纳入本报告成本。")
    lines.append("")
    for r in rows:
        lines.append(f"## {r['name']}")
        lines.append(f"**修改问句**：{r['question']}")
        lines.append("")
        if r["err"]:
            lines.append(f"❌ {r['note']}")
            lines.append("")
            continue
        lines.append("**模型生成的 SQL**：")
        lines.append("```sql")
        lines.append(r["sql"] or "（无）")
        lines.append("```")
        if r["note"]:
            lines.append(f"*{r['note']}*")
        lines.append("")

        for title, items in (("契约 · 必须保留", r["keeps"]),
                             ("契约 · 必须消失", r["drops"]),
                             ("结构判定", r["checks"])):
            if not items:
                continue
            lines.append(f"**{title}**：")
            for label, ok in items:
                lines.append(f"- {'✅' if ok else '❌'} {label}")
            lines.append("")

        a = r["audit"]
        codes = f"（{'、'.join(a['codes'])}）" if a["codes"] else ""
        tail = "，degraded（判定不可信）" if a["degraded"] else ""
        lines.append(f"**审核器裁决**：`{a['verdict']}`{codes}{tail}")
        if a["error"]:
            lines.append(f"- ❌ 审核器异常：{a['error']}")
        lines.append(f"- {'✅' if r['parseable'] else '❌'} 能通过字段级权限解析（方言感知，原 bug 已修复）")
        lines.append("")
        lines.append(f"**结论**：{'✅ 通过' if r['ok'] else '⚠️ 未通过（见上方 ❌ 项）'}")
        lines.append("")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_modify_live_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已写入：{out}")
    print(f"人工断言：{m['human_ok']}/{m['n']} 场景通过")
    print(f"审核器：pass {m['req_pass']}/{m['audited']}"
          f"（{m['req_pass_rate']:.1f}%），误拦 {m['false_block']}"
          f"（{m['false_block_rate']:.1f}%）")
    if dry_run:
        wrong = [r["name"] for r in rows
                 if r["uid"] in SELFTEST_MUST_FAIL and r["audit"]["verdict"] != "fail"]
        print(f"审核器自检：{len(SELFTEST_MUST_FAIL) - len(wrong)}/{len(SELFTEST_MUST_FAIL)}"
              f" 通过" + (f"；未通过：{wrong}" if wrong else ""))
    for r in rows:
        print(f"  {'✅' if r['ok'] else '❌'} {r['name']}"
              f"  [审核 {r['audit']['verdict']}]")


if __name__ == "__main__":
    _dry = "--dry-run" in sys.argv
    run(dry_run=_dry)
