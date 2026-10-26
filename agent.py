# -*- coding: utf-8 -*-
"""
agent.py —— 核心编排：把所有模块串成一条流水线
★ 这是整个项目的"大脑"，但它自己几乎不干活，全靠调用其它模块
★ 它的价值在于【编排】：规定各个模块按什么顺序、什么条件被调用

完整流程：
    ① 检索相关表和指标        → rag
    ② 兜底：没检索到就拒绝     → 不调模型，防止幻觉
    ③ 组装 System Prompt      → prompt
    ④ 调大模型生成 SQL        → llm
    ⑤ 清洗 SQL（去 ``` 和分号）→ guard
    ⑥ 循环：安检 → 执行 → 失败自愈 → 重试
"""
import config      # 配置（重试次数等）
import db          # 数据库（执行 SQL）
import guard       # 护栏（清洗 + 只读校验）
import llm         # 大模型（生成 SQL / 修复 SQL）
import prompt      # Prompt 组装
import rag         # 检索
import json        # ★ 解析自审报告 JSON
import re          # ★ 正则提取自审报告 JSON
import time        # ★ 记录流水线各阶段耗时（trace 可观测性）

# ★ 企业级架构升级新增的语义层模块（Semantic Layer + 多路 RAG + Validator）
import config_store        # 配置库（角色 / 规则 / 实体 / 关系 / 文档）
import semantic_layer      # P0~P4 分层注册表 + 冲突解决 + 多路检索
import query_understanding # 查询理解（意图 + 指标识别）
import sql_validator       # SQL 生成前 5 维校验
import result_validator    # SQL 执行后结果校验
import permission          # ★ 字段级权限引擎（ALLOW/MASK/DENY + AST 拦截 + 结果脱敏）
import nl_answer           # 自然语言回答生成
import calendar_util       # ★ 日历/节假日感知（时间口径解析 + 当前日期）


# ═══════════════════════════════════════════════════════════
# ★ 拒答识别（模型"说自己无法回答"时，必须【不执行】）
#
#   背景（真实事故）：提示词约定拒答要写成 `-- 无法回答：…`（行注释），
#   但模型有时写成 `/* 无法回答：… */`（块注释）。旧代码只判 `sql.startswith("--")`，
#   于是出现"模型已经声明无法回答、系统却照样把 SQL 跑出一个数字"——
#   用户会以为那是答案，比直接报错危险得多。
#
#   改为【结构判定】：剥掉开头注释后，只要不是以 SELECT / WITH 开头，就一律当拒答。
#   不依赖措辞，模型换任何说法都兜得住（fail-closed）。
#   另：开头注释里明确写了"无法回答"等措辞时，即使后面跟了 SQL 也按拒答处理
#   （防"边拒答边给 SQL"这种自相矛盾）。
# ═══════════════════════════════════════════════════════════
_SQL_START_RE = re.compile(r"^(select|with)\b", re.I)
_REFUSAL_WORDS = ("无法回答", "无法确定", "无法计算", "无法识别", "无法提供",
                  "无法统计", "无法汇总", "不能回答",
                  "信息不足", "数据不足", "不足以计算", "缺少必要")


def _strip_leading_comments(sql):
    """剥掉开头的注释（块注释 /* */ 与行注释 --），返回剩余内容"""
    s = sql or ""
    while True:
        s2 = re.sub(r"^\s*/\*.*?\*/", "", s, count=1, flags=re.S)
        if s2 == s:
            s2 = re.sub(r"^\s*(?:--[^\n]*\n?)+", "", s, count=1)
        if s2 == s:
            return s
        s = s2


def _leading_comment_text(sql):
    """取开头注释的文本（不含注释符号）"""
    m = re.match(r"^\s*/\*(.*?)\*/", sql or "", re.S)
    if m:
        return m.group(1).strip()
    m = re.match(r"^\s*((?:--[^\n]*\n?)+)", sql or "")
    if m:
        return "\n".join(re.sub(r"^\s*--\s*", "", x) for x in m.group(1).splitlines()).strip()
    return ""


def _refusal_reason(sql):
    """模型输出若是"拒答"而非可执行 SQL，返回原因文本；否则返回 ''"""
    if not (sql or "").strip():
        return "模型未返回任何内容"
    body = _strip_leading_comments(sql).strip()
    comment = _leading_comment_text(sql)
    if _SQL_START_RE.match(body):
        # 是 SQL。但若开头注释里已明说"无法回答"，仍按拒答处理（防自相矛盾）
        return comment[:300] if any(w in comment for w in _REFUSAL_WORDS) else ""
    return (comment or re.sub(r"\s+", " ", sql).strip())[:300] or "模型无法回答该问题"


# ── 配置分布类问题：不要擅自加时间过滤 ──
#   背景：问「信息费比例有几种搭配」时，模型按“没指明时间→近30天”的默认规则
#   加了 create_data_time 过滤，把“全量配置分布”算成了“近30天新注册司机”，
#   结果从 23 种组合缩成 6 种。配置分布是静态口径，不应默认套时间窗。
_CONFIG_DIST_RE = re.compile(r"(比例|配置|系数)")
_COMBO_RE = re.compile(r"(几种|多少种|搭配|组合|分布)")
_TIME_SIGNAL_RE = re.compile(
    r"(20\d{2}|年|季度|月|日|周|近\s*\d+|最近|本月|上月|今年|去年|今天|昨天|前\s*\d+\s*天)")


def needs_no_time_filter(question):
    """问的是“配置/比例的搭配分布”且没提时间 → 不应自动加时间过滤。"""
    q = question or ""
    return bool(_CONFIG_DIST_RE.search(q) and _COMBO_RE.search(q)
                and not _TIME_SIGNAL_RE.search(q))


class NotExecutable(Exception):
    """★ 执行前最后一道闸门拦下的情况（拒答文本 / 不是查询语句）。

    由调用方转成失败结果，**绝不当成可修复的执行错误去重试**。
    """


def _exec_sql(sql):
    """★★ 唯一执行出口（chokepoint）—— 全流程只有这里可以真正跑 SQL。

    ★ 为什么要"唯一出口"？（这是本项目的真实事故教训）
      模型曾在 SQL 顶部写 `/* 无法回答：现有表结构不足以计算 */`，
      而当时的拒答判定只看"是否以 `--` 开头" —— 判定漏了，SQL 就一路走到执行，
      变成"系统一边说无法回答、一边返回一个数字"（用户会以为那是答案）。
      只在【判定点】做检查，等于把"某处忘了判/判漏了"的风险留给未来。
      所以把**执行本身**收成一个出口：不管上游判定写成什么样，
      只要不是一条真正的查询语句，就绝不可能被执行。

    规则（fail-closed）：`_refusal_reason` 判定为拒答/非查询 → 抛 NotExecutable。

    ★ 维护约定：**agent.py 里不得出现裸的 `db.execute_sql(...)`**（由 test_gate 静态检查守着）。
    """
    reason = _refusal_reason(sql)
    if reason:
        raise NotExecutable(reason)
    return db.execute_sql(sql)


def _finalize_sql(raw_sql):
    """
    模型产出的 SQL 的统一收尾：清洗 → 补库名前缀

    ★ 为什么补前缀放在这里（而不是等执行时再补）？
      这样"权限校验看到的 SQL"、"真正执行的 SQL"、"界面上展示的 SQL"
      是同一个字符串。否则会出现"界面显示带了前缀、实际执行没带"这种不一致，
      排查问题时非常误导人。**校验的和执行的一定得是同一份 SQL。**

    ★ 为什么用后处理补前缀，而不是写进 prompt 让模型自己写？
      ① 模型可能忘、可能写错（写成两层前缀、或只给部分表加）——不可靠
      ② 每问都要为这条规则付 token
      后处理是确定性的：不管模型怎么写，出来的 SQL 一定合规。
      （模型自己写了前缀也不会变成两层 —— qualify_tables 会跳过已有前缀的表）
    """
    sql = guard.clean_sql(raw_sql)                   # ① 清洗：去掉 ``` 和分号
    if config.DB_TYPE == "clickhouse" and config.CK_DATABASE:
        sql = guard.qualify_tables(sql, config.CK_DATABASE)
        #   ② 补库名前缀（只对真实数仓有意义；SQLite 没有"库"这一层）
    return sql


def _table_swap_note(question, sql, sidx, ctx):
    """★ 换表说明：用户点名了某张表（如口语说"资金流水表"），
    但最终 SQL 没用它、改用别的召回表 → 显式解释，
    避免用户看到"召回了却没用"误以为是 bug。

    返回说明字符串；无冲突返回空串。
    """
    try:
        named, named_why = rag.named_tables(question, sidx["tables"])
    except Exception:
        return ""
    if not named:
        return ""
    def _bare(t):
        return t.split(".", 1)[-1]                  # 去掉 ctc_data_warehouse. 前缀
    used = {_bare(t) for t in guard.extract_tables(sql)}
    # ★ 只要用户点名的表里【有一张被实际用了】，就说明用户的选表意图已被满足，
    #   不再对"同义词命中的其它表"（如类型字典表 record_type）提示换表，避免噪声。
    if any(_bare(t) in used for t in named):
        return ""
    # 表名 → 业务自述名（短，便于展示；避免把整条注释塞进提示）
    comment_of = {}
    for t in sidx["tables"]:
        comment_of[t["name"]] = rag.table_self_name(t.get("doc", "")) or t["name"]
    # 先收集"点名了但没用"的表，按"实际改用目标"分组去重（避免同目标重复提示）
    by_target = {}          # frozenset(targets) -> (mentioned_list, tables_list)
    for t in named:
        bt = _bare(t)
        if bt in used:
            continue                                # 点名的表确实被用了 → 不用解释
        if bt not in ctx.p3_tables:
            continue                                # 根本没进候选 → 不算"换表"
        targets = tuple(sorted(u for u in used if u in ctx.p3_tables and u != bt))
        if not targets:
            continue
        key = frozenset(targets)
        g = by_target.setdefault(key, ([], []))
        g[0].append(named_why.get(t, bt))           # 命中的口语词
        g[1].append(bt)                             # 对应表名
    if not by_target:
        return ""
    notes = []
    for targets, (mentions, tables) in by_target.items():
        tgt = "、".join(f"{comment_of.get(u, u)}（{u}）" for u in sorted(targets))
        m_txt = "、".join(f"「{m}」" for m in dict.fromkeys(mentions))
        t_txt = "、".join(tables)
        notes.append(
            f"您提及的{m_txt}相关表（{t_txt}）未参与本次计算，实际数据取自「{tgt}」。"
            f"若确认该指标应落在{m_txt}，请核实对应表是否包含相关字段/取值。"
        )
    return " ".join(notes)


def _result(question, sql="", ok=False, error="", columns=None, rows=None,
            tables=None, metrics=None, dicts=None, info="", attempts=0,
            understanding=None, pipeline=None):
    """
    统一的结果格式（这样调用方不用猜返回了什么字段）
    ★ 这种做法叫"约定数据结构"：所有出口都返回同一种形状的字典

    ★ 为什么把 understanding / pipeline 也放进来？
      以前它们是在成功分支里手动补的（r["understanding"] = ...），
      于是**所有失败分支都漏了** —— 拒答时前端"查询理解"一片空白，
      用户就没法判断"是模型理解错了还是知识库缺东西"。
      放进统一的出口里，就不会再有人漏掉。
    """
    return {
        "question": question,      # 原始问题
        "sql": sql,                # 最终执行的 SQL
        "ok": ok,                  # 是否成功
        "error": error,            # 失败原因
        "columns": columns or [],  # 列名
        "rows": rows or [],        # 结果行
        "tables": tables or [],    # 用了哪些表
        "metrics": metrics or [],  # 用了哪些指标
        "dicts": dicts or [],      # ★ 用了哪些数据字典（字段取值含义）
        "info": info,              # 检索命中方式说明
        "attempts": attempts,      # 实际尝试了几次
        "understanding": understanding,  # ★ 查询理解（失败时也要有，便于定位）
        "pipeline": pipeline,      # ★ v2=企业级 / legacy=旧兜底
    }


def _parse_review(raw):
    """
    容错解析模型返回的自审报告 JSON
    ★ 模型可能夹带 ```json 包裹或前后废话，先抽最外层 {..}，抽不到就整段当文本返回，
      绝不因解析失败而让整条查询报错（自审是"加分项"，不能拖垮主流程）。
    """
    if not raw:
        return None
    try:
        m = re.search(r"\{.*\}", raw, re.S)      # re.S = 让 . 也能匹配换行
        if m:
            return json.loads(m.group(0))         # 解析成功直接返回 dict
    except Exception:
        pass
    return {"overall": raw.strip(), "fields": [], "risks": [], "needs_fix": None}


def _review_sql(question, sql, definitions=""):
    """
    ★ SQL 逻辑自审层：生成 SQL 后、返回前，额外调一次 LLM 做【字段级逻辑诊断】
    仅诊断、不改 SQL、不重跑（见 prompt.build_review_prompt）。
    失败（模型超时 / JSON 解析失败 / 开关关闭）一律返回 None，不影响主结果。
    """
    if not config.SQL_REVIEW_ENABLED or not sql or _refusal_reason(sql):
        return None
    try:
        rp = prompt.build_review_prompt(question, sql, definitions)
        raw = llm.chat("", rp)                    # 独立 system（空），角色与要求都写进 user 里
        return _parse_review(raw)
    except Exception as e:
        print(f"⚠️ SQL 逻辑自审失败（不影响主结果）：{e}")
        return None


def ask_legacy(index, question, user=None):
    """
    处理一个自然语言问题，返回结果字典（原单路 RAG 流水线，保留作兜底）

    index = rag.build_index() 建好的索引（启动时建一次，之后反复用）
    user  = 可信用户；非空时同样执行字段级权限闸门（Legacy 不得绕过权限）。
    """
    meta = None
    if user is not None:
        meta = index.get("tables") or []
    # ═══════════════════════════════════════════════
    # ① 检索：找出这次该用哪些表、哪些指标
    # ═══════════════════════════════════════════════
    table_names, metric_names, info, _ = rag.retrieve(index, question)

    # ★ 命中指标的业务口径文本（供逻辑自审层核对字段逻辑，减少误报）
    defs = "\n".join(
        m.get("definition", "") for m in (index.get("metrics") or [])
        if m.get("name") in metric_names
    )

    # ═══════════════════════════════════════════════════════
    # ★ 构建 Legacy 语义上下文（补齐业务校验：与 V2 使用同一套 sql_validator）
    #   原 Legacy 只做 只读+表权限+字段权限，缺少 P1指标硬约束 / P2 JOIN / P3 列存在性。
    #   这里用命中的指标/表/关系拼出最小 SemanticContext，复用 sql_validator，
    #   使 Legacy 兜底路径的业务校验与 V2 保持一致，杜绝「回退即弱校验」的绕过。
    # ═══════════════════════════════════════════════════════
    legacy_ctx = semantic_layer.SemanticContext()
    legacy_ctx.p1_metrics = [m for m in (index.get("metrics") or [])
                             if m.get("name") in metric_names]
    retrieved_tables = set(table_names)
    legacy_ctx.p3_table_objs = [t for t in (index.get("tables") or [])
                                if t.get("name") in retrieved_tables]
    try:
        _legacy_rels = config_store.get_relationships(enabled_only=True)
    except Exception:
        _legacy_rels = []          # 配置库未初始化/关系表缺失时降级为空（不阻塞主流程）
    legacy_ctx.p2_relationships = [
        r for r in _legacy_rels
        if (r.get("from_entity") or "") in retrieved_tables
        or (r.get("to_entity") or "") in retrieved_tables
    ]
    legacy_resolved = {
        "bindings": {m["name"]: (m.get("definition", ""), semantic_layer.P1)
                     for m in legacy_ctx.p1_metrics},
        "metric_types": {m["name"]: m.get("metric_type")
                         for m in legacy_ctx.p1_metrics},
        "metrics": [m["name"] for m in legacy_ctx.p1_metrics],
    }

    # ═══════════════════════════════════════════════
    # ①.2 ★ 数据字典：召回"字段取值的含义"
    # ═══════════════════════════════════════════════
    #   ★ 为什么字典要单独一条检索线？
    #     表结构告诉模型"有个字段叫 app_entity_source"，
    #     但没告诉它"101 是车拖车、201 是慧拉车"。
    #     缺了这层，模型只能猜编码 —— 猜出来的是"能跑但全错"的 SQL。
    #   注意：召不到字典不拦截（有些问题本来就不涉及编码），
    #         它是"加分项"，不是"必需项"。
    dict_names, dict_reason = rag.retrieve_dicts(index, question)
    info = f"{info} | 字典:{dict_reason}"
    if dict_names:
        print(f"  ★ 命中数据字典：{dict_names}")

    # ═══════════════════════════════════════════════
    # ①.5 ★★ 指标级权限：问题在问一个【未开放】的指标 → 直接拒绝
    # ═══════════════════════════════════════════════
    #   ★ 为什么这道检查必须存在？
    #     停用的指标不进知识库（AI 检索不到口径），但这不够 ——
    #     模型可能看到表里有 amount 字段，就自己拼一个 SUM(amount)/COUNT(*) 出来。
    #     结果是一条"能跑、不报错、但口径是错的"SQL。
    #     企业里最贵的错误就是这个：静默算错，没人发现。
    #   ★ 和下面的"表为空就拒绝"是对称设计：
    #       表：检索不到   → 拒绝（防止编造表名字段）
    #       指标：问的是未开放的 → 拒绝（防止自己编口径）
    blocked_metric = rag.match_disabled_metric(index, question)
    if blocked_metric:
        return _result(
            question, sql="", ok=False,
            error=f"该指标未开放访问：{blocked_metric}",
            metrics=[blocked_metric], dicts=dict_names, info=info,
        )

    # ═══════════════════════════════════════════════
    # ② ★★ 兜底：一张表都没检索到 → 直接拒绝
    # ═══════════════════════════════════════════════
    if not table_names:
        #  not 列表：列表为空时为 True
        # ★ 为什么必须拒绝，而不是"让它试试"？
        #   因为不给表结构，模型会凭训练记忆编造表名和字段名，
        #   生成一条"能跑但完全错"的 SQL —— 这比报错危险得多。
        return _result(
            question, sql="", ok=False,
            error="未识别到相关数据表，已拦截（防止模型编造）",
            metrics=metric_names, dicts=dict_names, info=info,
        )

    # ═══════════════════════════════════════════════
    # ③ 组装 System Prompt：只注入命中的表、指标和字典
    # ═══════════════════════════════════════════════
    system_prompt = prompt.build_system_prompt(
        index["tables"], index["metrics"], table_names, metric_names,
        index.get("dicts"), dict_names, question,
    )

    # ═══════════════════════════════════════════════
    # ④ 调大模型，生成 SQL
    # ═══════════════════════════════════════════════
    raw_sql = llm.chat(system_prompt, question)     # system = 规矩和上下文，user = 问题
    sql = _finalize_sql(raw_sql)                    # ★ 清洗 + 补库名前缀

    # ═══════════════════════════════════════════════
    # ⑤ 如果模型自己说"无法回答"，直接返回（不执行）
    #    ★ 用 _refusal_reason 识别：行注释/块注释都认，且"不是 SELECT/WITH"一律当拒答
    # ═══════════════════════════════════════════════
    _refuse = _refusal_reason(sql)
    if _refuse:
        return _result(
            question, sql=sql, ok=False, error=_refuse,
            tables=table_names, metrics=metric_names, info=info,
        )
        #  lstrip("- ") = 去掉开头所有的 "-" 和空格

    # ═══════════════════════════════════════════════
    # ⑥ ★ 重试循环：安检 → 执行 → 失败就自愈 → 再试
    # ═══════════════════════════════════════════════
    for attempt in range(1, config.MAX_RETRY + 1):
        #             range(1, 4) = 1, 2, 3（最多三轮）

        # ── 第一道校验：安全护栏（只读，不许 DELETE/DROP）──
        allowed, why = guard.sql_guard(sql)
        if not allowed:
            return _result(
                question, sql=sql, ok=False,
                error=f"安全护栏拦截：{why}",
                tables=table_names, metrics=metric_names, dicts=dict_names,
                info=info, attempts=attempt,
            )

        # ── ★ 第二道校验：表级权限（只允许查询白名单里的表）──
        #   为什么 RAG 已经过滤过了，这里还要再查一遍？
        #     RAG 过滤 = 控制"模型看见什么"（软约束，靠模型自觉）
        #     这里校验 = 控制"什么能被执行"（硬约束，代码说了算）
        #   模型有可能从训练数据里知道一张敏感表的名字，凭记忆写出来 ——
        #   这一道就是拦这种情况的。
        tbl_chk = guard.check_tables_ex(sql, index["allowed_tables"])
        #         ★ 用 ex 版本：解析不可信时会 fail-closed，并给出具体原因
        if not tbl_chk["ok"]:
            return _result(
                question, sql=sql, ok=False,
                error=tbl_chk["message"] or f"越权访问被拦截：{tbl_chk['illegal']}",
                tables=table_names, metrics=metric_names, dicts=dict_names,
                info=info, attempts=attempt,
            )

        # ── ★ 业务校验（与 V2 同一套 sql_validator）：指标硬约束 / JOIN / 列存在性 ──
        #   ★ 补齐 Legacy 缺的业务校验（GAP-1）。校验 error 级问题 → 回灌模型自愈，
        #     用尽重试仍未达标 → 拒绝执行（fail-closed），与 V2 行为一致。
        v = sql_validator.validate_sql_ex(sql, legacy_ctx, legacy_resolved,
                                          index["allowed_tables"])
        if sql_validator._has_error(v["issues"]):
            err = _format_issues([i for i in v["issues"] if i["severity"] == "error"]) \
                  or "SQL 未通过业务校验"
            if attempt >= config.MAX_RETRY:
                r = _result(
                    question, sql=sql, ok=False,
                    error=f"SQL 未通过业务校验（已重试）：{err}",
                    tables=table_names, metrics=metric_names, dicts=dict_names,
                    info=info, attempts=attempt,
                )
                r["validation"] = v["issues"]
                r["validation_status"] = v["status"]
                return r
            fix_user_prompt = prompt.build_repair_prompt(question, sql, err)
            fixed_raw = llm.chat(system_prompt, fix_user_prompt)
            sql = _finalize_sql(fixed_raw)
            continue

        # ── ★ 字段级权限闸门（Legacy 不得绕过）──
        perm_decision = None
        if user is not None:
            perm_decision = permission.evaluate(sql, user, meta)
            if not perm_decision["allowed"]:
                return _result(
                    question, sql=sql, ok=False,
                    error="字段级权限拦截：" + "；".join(perm_decision["deny_reasons"]),
                    tables=table_names, metrics=metric_names, dicts=dict_names,
                    info=info, attempts=attempt,
                )

        # ── 执行（★ 必须走唯一出口 _exec_sql）──
        try:
            columns, rows = _exec_sql(sql)           # 真正去数据库跑
            if perm_decision and perm_decision.get("mask_map"):
                rows = permission.mask_rows(columns, rows, perm_decision["mask_map"])
            r = _result(                             # 成功，直接返回
                question, sql=sql, ok=True,
                columns=columns, rows=rows,
                tables=table_names, metrics=metric_names, dicts=dict_names,
                info=info, attempts=attempt,
            )
            r["review"] = _review_sql(question, sql, defs)   # ★ 成功路径：追加逻辑自审报告
            return r
        except NotExecutable as ne:                  # ★ 闸门拦下：不是查询语句 → 直接失败，不重试
            return _result(
                question, sql=sql, ok=False, error=str(ne),
                tables=table_names, metrics=metric_names, dicts=dict_names,
                info=info, attempts=attempt,
            )
        except Exception as e:                       # 执行报错，进这里
            err = str(e)
            if attempt >= config.MAX_RETRY:          # 已经是最后一次
                r = _result(
                    question, sql=sql, ok=False,
                    error=f"执行失败（已重试 {attempt} 次）：{err}",
                    tables=table_names, metrics=metric_names, dicts=dict_names,
                    info=info, attempts=attempt,
                )
                r["review"] = _review_sql(question, sql, defs)   # ★ 失败自愈路径：也追加逻辑自审报告
                return r
            # ── 自愈：把报错回喂模型，让它改 ──
            fix_user_prompt = prompt.build_repair_prompt(question, sql, err)
            fixed_raw = llm.chat(system_prompt, fix_user_prompt)
            #                    ↑ 注意：用【同一个 system_prompt】
            #                      这样它修的时候仍能看到表结构和规则，
            #                      不会在修 A 错误的同时引入 B 错误
            sql = _finalize_sql(fixed_raw)           # 修完还要再清洗 + 补前缀一遍


# ═══════════════════════════════════════════════════════════
# ★ 企业级流水线：Semantic Layer + 多路 RAG + Agent + SQL Validator
#   用户自然语言 → Query Understanding → Semantic Router →
#   分层检索 → SQL Agent → SQL Validator(自愈) → ClickHouse →
#   Result Validator → 自然语言回答
# ═══════════════════════════════════════════════════════════
def _format_issues(issues):
    """把校验问题拼成文本，回灌给 LLM 做自愈"""
    lines = []
    for i in issues:
        lines.append(f"[{i['tier']}/{i['rule']}] {i['message']} → 建议：{i['suggestion']}")
    return "\n".join(lines)


def _pre_execute_check(sql, ctx, resolved, allowed_tables, user=None, meta=None):
    """★ 统一的执行前置校验（所有执行路径必须经过这里）。

    返回：
        ok      : 是否允许执行
        hard    : True = 安全问题（只读/表权限/字段级 DENY），不允许自愈重试；
                  False = 业务校验问题，可回灌模型修正
        issues  : 校验问题列表（喂给自愈 + 返回给前端）
        error   : 可直接展示的错误说明
        status  : 校验状态（passed/failed/error/degraded/blocked）
        perm_decision : 字段级权限判定结果（含 mask_map，供执行后脱敏；user 为空时 None）

    ★ 为什么不让 _exec_sql 直接做这件事？
      _exec_sql 没有 ctx/resolved（语义上下文），只能做"是否查询语句"的判定。
      把 ctx 相关校验塞进去会让执行层依赖语义层，耦合更重；
      这里用"唯一校验函数 + 所有执行点必须调用"来保证一致性，
      并由 test_gate 的静态测试守住"自愈后必须重新校验"。

    ★ 字段级权限（本次新增）：user 不为空时才启用。
      - 表级白名单之后，把检查下沉到字段：SELECT * 展开、别名/子查询/表达式引用
        都要解析归属，DENY 字段任何位置禁止，MASK 字段仅允许作为输出列。
      - 生产链路 server.api_ask 总是传入可信 user；eval/CLI/单测不传 user 时跳过，
        保证向后兼容（这些路径不涉及真实用户权限）。
    """
    ok, why = guard.sql_guard(sql)
    if not ok:
        return {"ok": False, "hard": True, "issues": [],
                "error": f"安全护栏拦截：{why}", "status": "blocked",
                "perm_decision": None}

    r = guard.check_tables_ex(sql, allowed_tables)
    if not r["ok"]:
        return {"ok": False, "hard": True, "issues": [],
                "error": r["message"] or f"越权访问被拦截：{r['illegal']}",
                "status": "blocked", "perm_decision": None}

    v = sql_validator.validate_sql_ex(sql, ctx, resolved, allowed_tables)
    if sql_validator._has_error(v["issues"]):
        return {"ok": False, "hard": False, "issues": v["issues"],
                "error": "SQL 未通过校验", "status": v["status"],
                "perm_decision": None}

    # ★ 字段级权限（ALLOW/MASK/DENY）—— 表级白名单之后的最后一道执行前闸门
    perm_decision = None
    final_sql = sql
    if user is not None:
        perm_decision = permission.evaluate(sql, user, meta)
        if not perm_decision["allowed"]:
            return {"ok": False, "hard": True, "issues": v["issues"],
                    "error": "字段级权限拦截：" + "；".join(perm_decision["deny_reasons"]),
                    "status": "blocked", "perm_decision": perm_decision, "final_sql": sql}

        # ★ 行级权限：注入管理员定义的可信过滤条件（AST 改写），改写后必须重新校验
        rewritten, changed, rl_err = permission.apply_row_filters(sql, user)
        if rl_err:
            return {"ok": False, "hard": True, "issues": v["issues"],
                    "error": f"行级权限改写失败：{rl_err}，已按安全策略拒绝",
                    "status": "blocked", "perm_decision": perm_decision, "final_sql": sql}
        if changed:
            # ★ 改写后重新校验完整 SQL（只读 + 表 + 字段级），保证执行 SQL == 校验 SQL
            ok2, why2 = guard.sql_guard(rewritten)
            if not ok2:
                return {"ok": False, "hard": True, "issues": v["issues"],
                        "error": f"行级改写后安全护栏拦截：{why2}",
                        "status": "blocked", "perm_decision": perm_decision, "final_sql": sql}
            r2 = guard.check_tables_ex(rewritten, allowed_tables)
            if not r2["ok"]:
                return {"ok": False, "hard": True, "issues": v["issues"],
                        "error": r2["message"] or "行级改写后越权访问被拦截",
                        "status": "blocked", "perm_decision": perm_decision, "final_sql": sql}
            pd2 = permission.evaluate(rewritten, user, meta)
            if not pd2["allowed"]:
                return {"ok": False, "hard": True, "issues": v["issues"],
                        "error": "行级改写后字段级权限拦截：" + "；".join(pd2["deny_reasons"]),
                        "status": "blocked", "perm_decision": pd2, "final_sql": sql}
            perm_decision = pd2
            perm_decision["rewritten"] = True
            final_sql = rewritten

    return {"ok": True, "hard": False, "issues": v["issues"],
            "error": "", "status": v["status"], "perm_decision": perm_decision,
            "final_sql": final_sql}


def ask_v2(index, question, user=None):
    """
    企业级流水线主入口。失败时由 ask() 回退到 ask_legacy。
    index = rag.build_index() 的结果（首次调用会自动 enrich 实体/关系/文档向量并缓存）。
    user  = 可信用户（dict，含 username / role）；非空时启用字段级权限闸门与结果脱敏。
    """
    # ── trace：记录各阶段耗时，供观测/排障 ──
    trace = []
    _t = time.time()
    def _mark(name):
        nonlocal _t
        now = time.time()
        trace.append({"step": name, "ms": round((now - _t) * 1000, 1)})
        _t = now

    # ── 0. 语义索引增强（缓存，避免每次重建向量）──
    sidx = index.get("_semantic")
    if sidx is None:
        sidx = semantic_layer.enrich_index(index)
        index["_semantic"] = sidx
    _mark("enrich")

    # ★ 表元数据（供字段级权限做 SELECT * 展开与字段归属）；user 为空时用不到
    meta = None
    if user is not None:
        meta = index.get("tables") or sidx.get("tables") or []

    # ① Query Understanding（意图 + 指标/实体识别）
    understanding = query_understanding.understand(question, sidx)
    _mark("understand")

    def _rv(*args, **kw):
        """ask_v2 的统一出口：自动带上查询理解与流水线标记。

        ★ 为什么要包一层？以前是在"成功分支"里手动补 understanding，
          结果**每个失败分支都漏**（拒答/拦截时前端查询理解一片空白，
          用户没法判断是理解错了还是知识库缺东西）。包一层就不会再漏。
        ★ 用 *args 透传给 _result：各调用点都写成 _rv(question, ...)，
          签名写成 _rv(**kw) 会漏掉第一个位置参数（实测直接 TypeError，
          再被 ask() 静默回退到 legacy —— 症状是"结果看着对但 pipeline=legacy"）。
        """
        kw.setdefault("understanding", understanding)
        kw.setdefault("pipeline", "v2")
        return _result(*args, **kw)


    # ② Semantic Router + 多路检索（P0~P4 分层）
    ctx = semantic_layer.retrieve_semantic(sidx, question, understanding)
    _mark("retrieve")

    # ①.5 指标级权限：问的是未开放指标 → 拒绝
    blocked_metric = rag.match_disabled_metric(sidx, question)
    if blocked_metric:
        return _rv(question, sql="", ok=False,
                       error=f"该指标未开放访问：{blocked_metric}",
                       metrics=[blocked_metric], info="指标权限拦截")

    # ②.5 兜底：一张表/一个指标都没命中 → 拒绝（防编造）
    if not ctx.p3_tables and not ctx.p1_metrics:
        return _rv(question, sql="", ok=False,
                       error="未识别到相关数据表/指标，已拦截（防止模型编造）",
                       info=ctx.route_info)

    # ③ 冲突解决 + 分层 Prompt 组装（role → 时间口径 → P0→P1→P2→P3→P4）
    resolved = semantic_layer.resolve(ctx)
    role = config_store.get_prompt("role").format(dialect=config.SQL_DIALECT)
    # ★ 日历/节假日感知：把「今天几号 + 时间表达解析出的公历区间」注入 Prompt，
    #   避免模型对着「25号前7天 / 去年中秋节前7天」这类口径瞎猜或直接拒答。
    time_context = calendar_util.build_time_context(question)
    # ★ 配置分布类问题（“比例有几种搭配”）不套默认时间窗，否则会把全量配置
    #   算成“近30天新增”，组合数直接变少。这是确定性的口径约束，不靠模型猜。
    if needs_no_time_filter(question):
        time_context += ("\n【口径约束】本问题问的是配置/比例的搭配分布，"
                         "不是时间区间指标；除非用户明确要求，不要添加创建时间、"
                         "订单时间等任何时间过滤条件。")
    system_prompt = semantic_layer.build_prompt_parts(
        resolved, role, time_context=time_context)

    # ③.5 数据字典（编码→中文，P4 之外的补充线，复用现有）
    dict_names, dict_reason = rag.retrieve_dicts(sidx, question)
    # ★★ 关键修复：把命中的字典【注入 System Prompt】。
    #   此前 v2 只"检索"了字典（dict_names 只进 info/结果展示），
    #   却从未拼进 prompt —— 导致"配了字典模型也看不到"，
    #   用户用中文名提到某个取值（如「钻石会员」）时模型只能瞎猜或拒答。
    #   legacy 路径一直是注入的（prompt.build_system_prompt(..., dicts, dict_names)），
    #   这里用同一个渲染函数补上，保持两条流水线行为一致。
    dict_block = prompt._build_dict_block(sidx.get("dicts") or [], dict_names, question)
    if dict_block:
        system_prompt = system_prompt + "\n\n# " + dict_block + "\n"
    # ★ 运行时护栏（fail-loud）：字典召回了却拼不出块 = 接线断了。
    #   这种"静默不生效"最容易骗过人工测试（页面正常、就是答不对），必须留下痕迹。
    if dict_names and not dict_block:
        print(f"⚠️ 字典已召回但未能注入 prompt（注入接线可能断了）：{dict_names}")
    tsum = calendar_util.summary_line(question)
    info = f"{ctx.route_info} | 字典:{dict_reason}" + (f" | {tsum}" if tsum else "")
    _mark("resolve_prompt")

    # ④ SQL Agent：生成 SQL
    raw_sql = llm.chat(system_prompt, question)
    sql = _finalize_sql(raw_sql)
    _mark("generate")
    _refuse = _refusal_reason(sql)
    if _refuse:
        #  ★ 关键：模型说"无法回答"时必须【不执行】。否则它会一边声明无法回答、
        #    一边把 SQL 跑出一个数字，用户以为那是答案 —— 比直接报错危险得多。
        return _rv(question, sql=sql, ok=False, error=_refuse,
                   tables=ctx.p3_tables, metrics=resolved["metrics"],
                   dicts=dict_names, info=info)

    # ⑤ SQL Validator：生成后、执行前 5 维校验 + 自愈回灌
    allowed_tables = sidx["allowed_tables"]
    last_check = None
    last_issues = []
    for attempt in range(1, config.MAX_RETRY + 1):
        # ★ 统一闸门：只读 + 表权限 + 五维校验，一次调用全部覆盖
        chk = _pre_execute_check(sql, ctx, resolved, allowed_tables, user=user, meta=meta)
        last_check = chk
        last_issues = chk["issues"]
        if chk["ok"]:
            break  # 通过校验
        if chk["hard"]:
            # 安全类问题不允许"自愈"绕过：直接拒绝
            return _rv(question, sql=sql, ok=False, error=chk["error"],
                       tables=ctx.p3_tables, metrics=resolved["metrics"],
                       dicts=dict_names, info=info, attempts=attempt)
        if attempt >= config.MAX_RETRY:
            break  # 用尽重试（是否执行由循环【之后】的闸门统一判定，见下）
        # 自愈：把校验问题回灌模型修正
        #   ★ 必须带上【原始问题 + 上一版 SQL】：否则模型看不到用户要什么，
        #     会"修好语法、丢了语义"（实测：按月的查询被自愈成总数，月份维度整个丢失）。
        fix_user = (f"原始问题：{question}\n\n"
                    f"你上一版 SQL：\n{sql}\n\n"
                    f"该 SQL 未通过校验。请在【保持原问题统计口径与分组维度不变】的前提下，"
                    f"仅修正下列问题后只输出 SQL：\n" + _format_issues(chk["issues"]))
        fixed_raw = llm.chat(system_prompt, fix_user)
        sql = _finalize_sql(fixed_raw)

    # ★★ 执行闸门（fail-closed）：用尽重试后仍未通过 → 直接拒绝执行。
    #   以前这里是"带问题继续执行"——校验器就失去了"执行前闸门"的意义：
    #   一个已被判定有硬伤（如缺必要状态条件、引用了不存在字段）的 SQL 照样跑，
    #   用户拿到的是"看起来有数、其实口径错"的结果 —— 比直接报错危险得多。
    if not last_check or not last_check["ok"]:
        r = _rv(question, sql=sql, ok=False,
                error="SQL 未通过校验（已重试仍未达标），已拒绝执行以避免错误结果。"
                      "请查看下方校验详情，或补充/修正知识库后重试。",
                tables=ctx.p3_tables, metrics=resolved["metrics"],
                dicts=dict_names, info=info, attempts=attempt)
        r["validation"] = last_issues
        r["validation_status"] = last_check["status"] if last_check else "error"
        r["result_warnings"] = []
        _mark("validate_rejected")
        r["trace"] = trace
        return r

    # ★ 行级改写后，实际执行的 SQL 以闸门返回的 final_sql 为准（执行 SQL == 校验 SQL）
    sql = (last_check or {}).get("final_sql") or sql

    _mark("validate")

    # ⑥ ClickHouse 执行（★ 必须走唯一出口 _exec_sql：拒答/非查询语句绝不可能被执行）
    def _rejected(reason, attempts=0):
        """闸门拦下时的统一返回（不执行、不重试）"""
        r = _rv(question, sql=sql, ok=False, error=reason,
                tables=ctx.p3_tables, metrics=resolved["metrics"],
                dicts=dict_names, info=info, attempts=attempts)
        r["validation"] = last_issues
        r["validation_status"] = last_check["status"] if last_check else "error"
        r["result_warnings"] = []
        _mark("not_executable")
        r["trace"] = trace
        return r

    try:
        columns, rows = _exec_sql(sql)
    except NotExecutable as ne:
        return _rejected(str(ne))
    except Exception as e:
        err = str(e)
        # 执行失败也走一次自愈（沿用既有 repair 逻辑）
        # ★ 用 ex_attempt 避免覆盖上方 SQL Validator 循环的 attempt 变量（attempts 统计用）
        for ex_attempt in range(2, config.MAX_RETRY + 1):
            fix_user = prompt.build_repair_prompt(question, sql, err)
            fixed_raw = llm.chat(system_prompt, fix_user)
            sql = _finalize_sql(fixed_raw)

            # ★★ 关键修复：自愈 SQL 必须重新过完整闸门。
            #   旧实现这里直接 _exec_sql(sql) —— 模型在"修语法"时顺手写一张未授权表，
            #   就能绕过 sql_guard / check_tables / validate_sql（已实测复现）。
            chk = _pre_execute_check(sql, ctx, resolved, allowed_tables, user=user, meta=meta)
            last_check = chk
            last_issues = chk["issues"]
            if not chk["ok"]:
                if chk["hard"]:
                    # 安全类问题：不再重试，直接拒绝
                    return _rejected(chk["error"], ex_attempt)
                err = ("；".join(i["message"] for i in chk["issues"]
                                if i.get("severity") == "error")
                       or chk["error"])
                if ex_attempt >= config.MAX_RETRY:
                    r = _rv(question, sql=sql, ok=False,
                            error=f"自愈后的 SQL 未通过校验（已拒绝执行）：{err}",
                            tables=ctx.p3_tables, metrics=resolved["metrics"],
                            dicts=dict_names, info=info, attempts=ex_attempt)
                    r["validation"] = last_issues
                    r["validation_status"] = chk["status"]
                    r["result_warnings"] = []
                    _mark("repair_rejected")
                    r["trace"] = trace
                    return r
                continue

            # ★ 自愈 SQL 通过闸门后，同样以 final_sql（可能含行级改写）为准执行
            sql = chk.get("final_sql") or sql
            try:
                columns, rows = _exec_sql(sql)
                break
            except NotExecutable as ne:
                # ★ 自愈后模型改口"无法回答" → 也必须停下，不能当执行错误继续重试
                return _rejected(str(ne), ex_attempt)
            except Exception as e2:
                err = str(e2)
                if ex_attempt >= config.MAX_RETRY:
                    r = _rv(question, sql=sql, ok=False,
                                error=f"执行失败（已重试）：{err}",
                                tables=ctx.p3_tables, metrics=resolved["metrics"],
                                dicts=dict_names, info=info, attempts=ex_attempt)
                    r["validation"] = last_issues
                    r["validation_status"] = last_check["status"] if last_check else "error"
                    r["result_warnings"] = []
                    _mark("execute_fail")
                    r["trace"] = trace
                    return r

    _mark("execute")

    # ★ 字段级结果脱敏（执行后、任何返回/快照之前，确定性完成）
    #   只有 user 非空才可能产生 mask_map；user 为空时 perm_decision=None，跳过。
    perm_decision = (last_check or {}).get("perm_decision") or {}
    mask_map = perm_decision.get("mask_map") or {}
    if mask_map:
        rows = permission.mask_rows(columns, rows, mask_map)

    # ⑦ Result Validator（执行后结果校验）
    warnings = result_validator.validate_result(
        columns, rows, resolved, question,
        expected_multi_row=bool(understanding.get("dimensions")))

    # ⑧ 自然语言回答
    metric_defs = "\n".join(m.get("definition", "") for m in ctx.p1_metrics)
    nl = nl_answer.answer(question, columns, rows, metric_defs,
                          understanding.get("ambiguity", ""))

    # ★ 换表说明：用户点名表 vs SQL 实际用表（见 _table_swap_note）
    swap = _table_swap_note(question, sql, sidx, ctx)
    if swap:
        info = info + " | 换表说明：" + swap

    r = _rv(question, sql=sql, ok=True, columns=columns, rows=rows,
                tables=ctx.p3_tables, metrics=resolved["metrics"],
                dicts=dict_names, info=info, attempts=attempt)
    r["table_swap"] = swap                              # ★ 独立字段，前端可高亮
    r["review"] = _review_sql(question, sql, metric_defs)   # 保留既有逻辑自审（事后复核）
    r["validation"] = last_issues                           # SQL 校验报告
    r["validation_status"] = last_check["status"] if last_check else "error"
    r["result_warnings"] = warnings                         # 结果校验 warning
    r["answer"] = nl                                        # 自然语言回答
    r["perm_decision"] = perm_decision                      # ★ 字段级权限判定（供审计/前端）
    r["masked"] = bool(mask_map)                            # ★ 是否发生结果脱敏
    r["mask_map"] = mask_map                                # ★ {列名: 脱敏规则}
    _mark("result_validate_answer")
    r["trace"] = trace                                      # 各阶段耗时（可观测性）
    return r


# ── 可回退的"可恢复故障"白名单：只有基础设施类的瞬时故障才允许降级 legacy ──
#   ★ 为什么白名单这么窄？
#     legacy 是"校验更弱"的旧流程。若 V2 的【逻辑/校验】异常也回退，
#     就等于把"校验没通过"偷偷降级成"不校验地执行"—— 这正是审查报告点名的风险。
#     所以只有与业务逻辑无关的瞬时故障（超时/连接）才允许回退。
_RECOVERABLE_ERRORS = (TimeoutError, ConnectionError)


def _is_recoverable(e):
    """异常是否属于白名单内的可恢复故障（允许降级 legacy）"""
    if isinstance(e, _RECOVERABLE_ERRORS):
        return True
    msg = str(e).lower()
    return any(k in msg for k in ("timed out", "timeout", "connection refused",
                                  "connection reset", "temporarily unavailable"))


def ask(index, question, user=None):
    """对外统一入口：优先走企业级流水线 ask_v2。

    ★ fail-closed 降级策略（见 config.LEGACY_FALLBACK_MODE）：
      V2 抛异常时【默认不再无脑回退 legacy】。原因有二：
        ① legacy 校验更弱，静默回退会把"校验失败"变成"不校验地执行"；
        ② 静默降级会让监控指标失真（看不出 V2 到底稳不稳）。
      仅 whitelist 模式下的可恢复故障（超时/连接）才降级，且结果里留痕
      （pipeline/degraded/degrade_reason/degrade_stage），便于统计与告警。

    user  = 可信用户（dict，含 username / role）；非空时启用字段级权限闸门。
            生产链路 server.api_ask 总是传入；eval/CLI 不传（无真实用户权限语义）。
    """
    try:
        return ask_v2(index, question, user=user) if user is not None else ask_v2(index, question)
    except Exception as e:
        mode = getattr(config, "LEGACY_FALLBACK_MODE", "whitelist")
        app_env = getattr(config, "APP_ENV", "production")

        # ★ 生产环境禁止 always：always 会把"业务校验异常/程序 bug"也回退到弱校验的
        #   legacy 路径，等于静默绕过强校验。这里强制收敛为 whitelist（fail-closed）。
        if mode == "always" and app_env == "production":
            print("⛔ 生产环境禁止 LEGACY_FALLBACK_MODE=always，已按 whitelist 处理")
            mode = "whitelist"

        reason = f"{type(e).__name__}: {e}"
        recoverable = _is_recoverable(e)
        allow = (mode == "always") or (mode == "whitelist" and recoverable)
        if allow:
            # ★ 日志要说真话：只有真的命中白名单才叫"可恢复故障"；
            #   always 模式下哪怕 ValueError 也会回退，不能伪装成可恢复。
            kind = "可恢复故障" if recoverable else f"always 模式强制回退（{app_env}）"
            print(f"⚠️ V2 异常（{kind}：{reason}），按 mode={mode} 回退 legacy")
            r = (ask_legacy(index, question, user=user) if user is not None
                 else ask_legacy(index, question))
            r["pipeline"] = "legacy"
            r["degraded"] = True
            r["degrade_stage"] = "v2"
            r["degrade_reason"] = reason
            r["degrade_kind"] = "recoverable" if recoverable else "forced"
            return r
        # fail-closed：不执行未经校验流程的结果，把异常如实暴露出来
        print(f"⛔ V2 异常且不允许回退（fail-closed, mode={mode}）：{reason}")
        r = _result(question, sql="", ok=False,
                    error=f"企业级流水线异常，已中止（fail-closed）：{reason}",
                    pipeline="v2")
        r["degraded"] = False
        r["degrade_stage"] = "v2"
        r["degrade_reason"] = reason
        return r
