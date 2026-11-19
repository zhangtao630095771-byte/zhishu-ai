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
import concurrent.futures   # ★ 尾段并行（自然语言回答 ∥ 事后自审，互不依赖）

# ★ 企业级架构升级新增的语义层模块（Semantic Layer + 多路 RAG + Validator）
import config_store        # 配置库（角色 / 规则 / 实体 / 关系 / 文档）
import semantic_layer      # P0~P4 分层注册表 + 冲突解决 + 多路检索
import query_understanding # 查询理解（意图 + 指标识别）
import sql_validator       # SQL 生成前 5 维校验
import result_validator    # SQL 执行后结果校验
import permission          # ★ 字段级权限引擎（ALLOW/MASK/DENY + AST 拦截 + 结果脱敏）
import nl_answer           # 自然语言回答生成
import calendar_util       # ★ 日历/节假日感知（时间口径解析 + 当前日期）
import sql_experience      # ★ SQL 经验中心（历史成功案例参考 / 失败经验预防）
import requirement_review  # ★ 需求一致性审核（第六维：这条 SQL 是否回答了"用户这次问的问题"）
import sql_modify         # ★ SQL 多轮修改结构保护（AST 差异校验 + 授权范围识别，P0）
import exceptions         # ★ LLM 错误分类（区分 4xx 不可重试 / 429 退避 / 超时网络可重试）
import semantic_builder   # ★ 语义上下文构建审计（统一注册表核对命中知识治理状态，只观测不阻断）
import business_domain_service  # ★ 业务域识别 + 语义对象注入（Agent 使用标准语义知识，动态映射）
import task_state         # ★ 任务状态/事件协议（SSE 改造用，零依赖，无循环 import 风险）


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
    if config.DB_TYPE == "clickhouse":
        sql = guard.normalize_union(sql)
        #   ② 裸 UNION → UNION ALL（ClickHouse 只认 ALL/DISTINCT，裸 UNION 会让
        #      字段级权限闸门解析失败而 fail-closed 拒绝；只对 ClickHouse 改写，
        #      标准 SQL 里裸 UNION 是合法去重语义，不能动）
    if config.DB_TYPE == "clickhouse" and config.CK_DATABASE:
        sql = guard.qualify_tables(sql, config.CK_DATABASE)
        #   ③ 补库名前缀（只对真实数仓有意义；SQLite 没有"库"这一层）
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
            understanding=None, pipeline=None, experience=None, **extra):
    """
    统一的结果格式（这样调用方不用猜返回了什么字段）
    ★ 这种做法叫"约定数据结构"：所有出口都返回同一种形状的字典

    ★ 为什么把 understanding / pipeline 也放进来？
      以前它们是在成功分支里手动补的（r["understanding"] = ...），
      于是**所有失败分支都漏了** —— 拒答时前端"查询理解"一片空白，
      用户就没法判断"是模型理解错了还是知识库缺东西"。
      放进统一的出口里，就不会再有人漏掉。
    """
    d = {
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
        # ★ 本次召回/采用了哪些历史经验（成功案例/失败模式/规则）
        #   失败分支也要有：否则"为什么这次没参考到经验"永远查不出来
        "experience": experience,
    }
    # ★ 私有附加字段（下划线开头）：只在进程内传递，服务端不会放进响应体。
    #   典型用法：_exp_match —— 原始经验匹配对象，落库拿到 record_id 后补使用日志。
    d.update(extra)
    return d


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


def _sql_risk_level(sql, ctx, resolved=None, last_check=None):
    """确定 SQL 风险等级（LOW/MEDIUM/HIGH），只基于结构 + 校验结果，不看长度。

    LOW    ：单表简单查询（简单过滤/聚合、无口径依赖、无 JOIN）
    MEDIUM ：多表 JOIN / 复杂指标 / 多个筛选 / 业务口径依赖
    HIGH   ：一对多或多对多 JOIN / 跨主题 / 复杂指标计算 / 粒度冲突 / 校验报错
    """
    tables = getattr(ctx, "p3_tables", None) or []
    metrics = getattr(ctx, "p1_metrics", None) or []
    rels = getattr(ctx, "p2_relationships", None) or []
    sql_up = (sql or "").upper()

    has_join = bool(rels) or " JOIN " in sql_up
    has_subquery = re.search(r"\(\s*SELECT\b", sql or "", re.I) is not None
    has_cte = re.search(r"\bWITH\b", sql_up) is not None
    has_ratio = ("/" in (sql or "")) or "NULLIF" in sql_up
    has_cond_agg = re.search(r"\bCASE\b|\bIF\s*\(|\bMULTIIF\s*\(", sql_up) is not None

    issues = (last_check or {}).get("issues") or []
    has_error = any(i.get("severity") == "error" for i in issues)

    # 一对多/多对多/笛卡尔：关系基数提示，或校验器 join_pre_agg 命中
    many = any(s in str(rels) for s in ("一对多", "多对多", "笛卡尔"))
    join_preagg = any(i.get("rule") == "join_pre_agg" for i in issues)

    if (many or join_preagg or has_cte or has_subquery or has_error
            or (has_join and has_cond_agg) or (has_join and has_ratio)
            or (has_join and len(tables) >= 3)):
        return "HIGH"
    if has_join or metrics or has_ratio or has_cond_agg or issues:
        return "MEDIUM"
    return "LOW"


def _review_sql(question, sql, definitions="", risk="MEDIUM"):
    """
    ★ SQL 逻辑自审层（分层）：按风险等级决定是否 / 如何调 LLM 做逻辑诊断。
      仅诊断、不改 SQL、不重跑（见 prompt.build_review_prompt）。
      失败（超时 / JSON 解析失败 / 开关关闭）一律返回 None，不影响主结果。

    risk 分级：
      LOW    → 跳过（规则校验已覆盖，避免无条件完整 LLM 自审）
      MEDIUM → 轻量自审（只出整体结论 + 风险点，短输出 + 短超时）
      HIGH   → 深度自审（逐字段诊断，长输出 + 较长超时）
    """
    if not config.SQL_REVIEW_ENABLED or not sql or _refusal_reason(sql):
        return None
    if risk == "LOW":
        return None                       # ★ 简单查询不再无条件完整自审
    deep = (risk == "HIGH")
    timeout = (config.SQL_REVIEW_TIMEOUT_HIGH if deep
               else config.SQL_REVIEW_TIMEOUT_MEDIUM)
    try:
        rp = prompt.build_review_prompt(question, sql, definitions, deep=deep)
        # ★ 不设 max_tokens：实测该端点带 max_tokens 会返回空串；靠 light prompt 短输出 + timeout 控时。
        raw = llm.chat("", rp, timeout=timeout)
        return _parse_review(raw)
    except Exception as e:
        print(f"⚠️ SQL 逻辑自审失败（不影响主结果）：{e}")
        return None


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


def _build_system_prompt(sidx, ctx, resolved, question, exp_block="", sm_block=""):
    """★ 组装 system prompt 的**唯一**位置（权威性从高到低）。

    返回 (text, dict_names, dict_reason, parts)

    ★ 为什么要收敛成一个函数？
      改造前这里有 4 处手工 `system_prompt = system_prompt + ...`：
        build_prompt_parts → 字典块 → 同名字段硬约束 → 经验块
      顺序全靠这几十行代码的**书写顺序**保证，没有任何地方能一眼看出
      "最终 prompt 到底长什么样"。顺序一旦被后来的改动插错位置
      （比如把经验块插到指标口径前面），系统不会报错，只会**答错**，
      而且极难排查 —— 这类"静默失效"本项目已经栽过一次（字典检索了却没注入）。

    ★ 为什么用"逐段追加 + 记录 parts"，而不是拼一个 list 再 join？
      join 会改变分隔符（某些段之间是一个换行、某些是两个），
      而 prompt 的细微差异会实打实影响生成质量。
      这里严格保持改造前的字节序，确保行为完全等价（有测试守着）。
    """
    # ① 角色 + 时间口径 + P0~P4 分层知识（权威性最高的部分）
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
    text = semantic_layer.build_prompt_parts(resolved, role, time_context=time_context)
    parts = [("base(P0~P4)", text)]

    # ② 数据字典（编码→中文）。放在 P0~P4 之后：它是"翻译表"，不是权威口径。
    dict_names, dict_reason = rag.retrieve_dicts(sidx, question)
    # ★★ 关键修复：把命中的字典【注入 System Prompt】。
    #   此前 v2 只"检索"了字典（dict_names 只进 info/结果展示），
    #   却从未拼进 prompt —— 导致"配了字典模型也看不到"，
    #   用户用中文名提到某个取值（如「钻石会员」）时模型只能瞎猜或拒答。
    #   legacy 路径一直是注入的，这里用同一个渲染函数补上，保持两条流水线行为一致。
    dict_block = prompt._build_dict_block(
        sidx.get("dicts") or [], dict_names, question,
        tables=sidx.get("tables") or [], table_names=resolved.get("tables") or [])
    if dict_block:
        text = text + "\n\n# " + dict_block + "\n"
        parts.append(("dicts", dict_block))
    # ★ 运行时护栏（fail-loud）：字典召回了却拼不出块 = 接线断了。
    #   这种"静默不生效"最容易骗过人工测试（页面正常、就是答不对），必须留下痕迹。
    if dict_names and not dict_block:
        print(f"⚠️ 字典已召回但未能注入 prompt（注入接线可能断了）：{dict_names}")

    # ③ 系统级硬约束（同名字段跨表不得串味）
    #   ★ 统一主流程在此显式拼一次硬约束块（build_hard_constraints_block 是共享组件，
    #     不依赖任何已退役的旧 Prompt 组装函数）。
    hard_block = prompt.build_hard_constraints_block(
        sidx.get("tables") or [], resolved.get("tables") or [])
    if hard_block:
        text = text + "\n\n" + hard_block + "\n"
        parts.append(("hard_constraints", hard_block))

    # ③.5 语义模型约束（★ 审核通过的结构化语义，优先级高于经验参考、低于硬约束）
    #   ★ 为什么放这里？经验是"参考"，语义模型是"已审核事实"——结构上必须高于经验。
    #     sm_block 默认空串时零行为变化（字节序与接入前完全一致）。
    if sm_block:
        text = text + "\n\n" + sm_block + "\n"
        parts.append(("semantic_model", sm_block))

    # ④ 历史经验（★ 必须最后。经验是"参考"，结构上不允许它覆盖指标定义与 JOIN 铁律）
    if exp_block:
        text = text + "\n\n" + exp_block + "\n"
        parts.append(("experience", exp_block))

    return text, dict_names, dict_reason, parts


def _merge_understanding_for_modify(new_u, modify_context):
    """★ 增量修改：把「展示用理解」合并上轮上下文。

    规则（只往"安全"方向合并，避免静默删除用户未明确改的条件）：
      以本轮 understand 结果为底；
      若某字段本轮未产出（空/缺），则用上一轮对应字段补全。
    这样"去掉应用主体分组"时，本轮 dimensions=[月] 会被保留，不会回退成 [月,应用主体]；
    而"再增加完成率"时，本轮若只识别到 [完成率]，展示仍可能只显示完成率
    （SQL 已由 LLM 在完整上下文下正确生成，结果无误）—— 这是已知的可视化边界，不影响正确性。
    """
    base = (modify_context or {}).get("base_understanding") or {}
    merged = dict(new_u or {})
    for k, v in base.items():
        if k not in merged or merged[k] in (None, "", [], {}):
            merged[k] = v
    return merged


def _modify_prompt_block(question, modify_context):
    """★ 增量修改上下文段：注入上一轮查询，约束 LLM 只改用户要求的部分。

    必须以"保留未被修改部分"为硬约束，否则模型容易把上一轮指标/时间/过滤整个丢掉，
    退化成"凭记忆重答"。同时点名一个高频陷阱：某字段既作分组又作等值过滤时，
    去掉分组不能顺手删掉过滤（否则分组恒为单一值）。
    """
    base = (modify_context or {}).get("base_understanding") or {}
    base_q = modify_context.get("base_question") or ""
    base_sql = modify_context.get("base_sql") or ""
    base_tables = modify_context.get("base_tables") or []
    lines = [
        "# 上一轮查询（本次为【增量修改】，请在上一轮基础上调整）",
        "—— 你在处理一个「对已有查询做修改」的请求，不是从零开始的独立新问题"
        "（除非本轮问题明显是全新主题，此时忽略上一轮）。",
        "",
        f"上一轮用户问题：{base_q}",
        f"上一轮结构化意图：{json.dumps(base, ensure_ascii=False)}",
        f"上一轮使用的数据表：{', '.join(base_tables)}" if base_tables else "",
        f"上一轮生成的 SQL：{base_sql}" if base_sql else "上一轮生成的 SQL：（无）",
        "",
        "## 硬性约束（务必遵守）",
        "1) 只按用户【明确要求】的修改调整；未被明确修改的指标、实体、时间范围、"
        "过滤条件、分组维度必须【原样保留】，禁止因修改其中一个维度就清空其余已确认条件。",
        "2) 禁止更换数据表、禁止更换字段、禁止改变 SUM/COUNT/COUNT DISTINCT 等聚合方式、"
        "禁止改变 JOIN 关系——除非用户【明确要求】。即使你的知识库里有「看似更合适」的表或口径，"
        "也不得擅自替换（若确有冲突，宁可保留原表并在输出里说明，不要静默换表）。",
        "3) 若用户要求去掉某个分组维度，但该维度字段上同时存在一个等值过滤（例如 "
        "app_entity_source = 0），除非用户明确要求「按该字段分别统计」或「只查看该值」，"
        "否则【保留该过滤条件】——否则分组维度会变成分组恒为单一值的冗余分组。",
        "4) 禁止新增/删除用户未提及的指标或过滤；重新生成完整 SQL 并重新经过校验。",
    ]
    return "\n".join(lines)


def _dump_prompt(question, text, parts):
    """把最终 system prompt 落盘（PROMPT_DUMP=1 时开启）

    ★ 为什么需要这个开关？
      排障时最常见的问题是「模型为什么这么写 SQL」。
      改造前只能靠猜或者临时加 print —— 而 print 出来的东西又被
      日志轮转吃掉、也没法逐段对比。落成文件就能直接 diff 两次改动的差异。
      默认关闭：一次取数一个文件，长期开会把磁盘写满。
    """
    if not getattr(config, "PROMPT_DUMP", False):
        return
    try:
        import hashlib as _h
        import os as _os
        import time as _t
        d = getattr(config, "PROMPT_DUMP_DIR", "logs/prompt")
        _os.makedirs(d, exist_ok=True)
        ts = _t.strftime("%Y%m%d-%H%M%S")
        tag = _h.sha1((question or "").encode("utf-8")).hexdigest()[:8]
        path = _os.path.join(d, f"{ts}-{tag}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# 问题\n{question}\n\n")
            f.write(f"# 分段（共 {len(parts)} 段，合计 {len(text)} 字符）\n")
            for name, seg in parts:
                f.write(f"## {name}  {len(seg)} 字符\n")
            f.write("\n# 最终 system prompt\n")
            f.write(text)
    except Exception as e:
        print(f"⚠️ Prompt 落盘失败（不影响取数）：{e}")


def ask_v2(index, question, user=None, understanding_override=None, modify_context=None,
           want_answer=False, events=None, is_cancelled=None):
    """
    统一主流程主入口（双流水线已收敛，全仓唯一业务编排）。
    index = rag.build_index() 的结果（首次调用会自动 enrich 实体/关系/文档向量并缓存）。
    user  = 可信用户（dict，含 username / role）；非空时启用字段级权限闸门与结果脱敏。
    understanding_override = 由【澄清模块】确认后的结构化意图（可选）。
        多轮澄清确认了口径后，用它锁死指标，跳过 LLM 意图识别，避免再次猜口径。
        为 None 时行为与改造前完全一致。
    modify_context = 由【增量修改】带入的上一轮查询上下文（可选）。
        当前窗口内用户对上一轮查询做自然语言修改时携带：保留未被明确修改的指标/
        时间/过滤/维度，只对用户要求的部分重新生成 SQL。为 None 时行为与改造前一致。
    events = 阶段事件回调（可选，SSE 改造新增）：形如 events(name, data)。
        每个阶段边界调用一次，把"执行到哪一步"实时通知出去；为 None 时零行为变化。
        eval / CLI / 评测直调 agent.ask 不传，签名与行为与改造前完全一致。
    is_cancelled = 取消探测回调（可选）：返回 True 表示用户已取消，应在执行 SQL 前中止。
        为 None 时永不取消（与改造前一致）。
    """
    # ── trace：记录各阶段耗时，供观测/排障 ──
    trace = []
    # ★ 经验检索结果（提前初始化为 None：_rv 会在每个出口读它，
    #   若在检索前就有失败分支返回（如指标未开放），读未赋值变量会直接 NameError）
    exp_match = None
    _t = time.time()
    def _mark(name):
        nonlocal _t
        now = time.time()
        trace.append({"step": name, "ms": round((now - _t) * 1000, 1)})
        _t = now

    # ★ SSE 阶段事件发射（可选）：events 为 None 时零开销、零行为变化
    def _emit(name, data=None):
        if events:
            try:
                events(name, data or {})
            except Exception as e:
                print(f"⚠️ 阶段事件发射失败（不影响取数）：{e}")

    # ★ 取消探测（可选）：执行 SQL 这类重操作前检查，取消则抛 TaskCancelled
    def _check_cancel(stage=""):
        if is_cancelled and is_cancelled():
            raise task_state.TaskCancelled(f"任务在「{stage}」阶段被用户取消")

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
    #   ★ 多轮澄清：若上层已确认口径，直接用 override，不再让 LLM 重新理解
    #     （否则上一轮用户选的指标可能被二次识别成别的，等于白确认）。
    if understanding_override is not None:
        understanding = understanding_override
    else:
        understanding = query_understanding.understand(question, sidx)
    _mark("understand")
    _emit(task_state.EV_UNDERSTANDING, {
        "intent": understanding.get("intent"),
        "metrics": understanding.get("metrics") or [],
        "entities": understanding.get("entities") or [],
        "dimensions": understanding.get("dimensions") or [],
        "time_range": understanding.get("time_range") or "",
    })

    # ★ 增量修改：把上一轮查询上下文并入「展示用理解」与系统提示，
    #   保证未被用户明确修改的指标/时间/过滤/维度被保留，且 LLM 看到上轮完整 SQL。
    #   （检索用理解见下方 retrieve_semantic 调用处：额外并入上轮指标/实体，
    #    确保模型仍看得到原表结构；展示用理解见本处：新理解为底，缺的用上轮补全。）
    if modify_context:
        understanding = _merge_understanding_for_modify(understanding, modify_context)

    # ★★ 状态链（七个状态分开记，绝不合并成一个"成功"）：
    #   生成成功 ≠ 校验通过 ≠ 需求已核 ≠ 执行成功 ≠ 结果合理 ≠ 业务正确 ≠ 需求完成。
    #   ★ 为什么用可变字典 + 出口快照？
    #     ask_v2 有 10 多个 return 点，逐个手写 status_chain 必然漏（历史教训：
    #     understanding 就是这样"只在成功分支补"，导致失败分支全空）。
    #     这里让每个出口在 _rv 里拿一份【当下时刻】的快照，天然不会漏。
    chain = {
        "generated": False,             # 模型是否产出了可执行语句
        "gate_passed": False,           # 只读 + 表权限 + 五维校验 + 字段权限是否全过
        "requirement_verdict": "skipped",  # pass/fail/unsure/skipped（"跑过"≠"通过"）
        "executed": False,              # 是否真的执行了 SQL
        "exec_ok": False,               # 是否拿到结果集
        "result_sane": None,            # 结果体检是否无疑点（None=没跑到这一步）
        "business_verified": None,      # 事后回填：人工审核 approved
        "requirement_fulfilled": None,  # 事后回填：用户确认结果对
        "requirement_attempts": 0,      # 需求审核触发了几次定向修复
    }
    # ★ 需求契约/审核结果的"可变容器"：必须在 _rv 定义之前创建，
    #   否则任何在生成之前就返回的分支（指标未开放、零召回）读它会 NameError。
    req_state = {"contract": None, "result": None, "blocked": False}

    def _rv(*args, **kw):
        """ask_v2 的统一出口：自动带上查询理解与流水线标记。

        ★ 为什么要包一层？以前是在"成功分支"里手动补 understanding，
          结果**所有失败分支都漏**（拒答/拦截时前端查询理解一片空白，
          用户就没法判断"是模型理解错了还是知识库缺东西"）。
          放进统一的出口里，就不会再有人漏掉。
        ★ 用 *args 透传给 _result：各调用点都写成 _rv(question, ...)，
          签名写成 _rv(**kw) 会漏掉第一个位置参数（实测直接 TypeError，
          再被 ask() 静默回退到 legacy —— 症状是"结果看着对但 pipeline=legacy"）。
        """
        kw.setdefault("understanding", understanding)
        kw.setdefault("pipeline", "v2")
        # ★ 经验命中情况随每个出口返回。同时把「原始 match 对象」挂在私有字段上，
        #   供服务端在落库拿到 record_id 后补一条使用日志（响应体里不会出现该字段）。
        kw.setdefault("experience", exp_match.summary() if exp_match is not None else None)
        kw.setdefault("_exp_match", exp_match)
        # ★ 状态链快照：dict(chain) 复制一份，避免后续阶段推进把已返回的结果也改掉
        kw.setdefault("status_chain", dict(chain))
        # ★ 需求审核结果随每个出口返回（用 req_state 取值：它在本函数很早处就存在，
        #   任何早退分支读它都不会 NameError）
        kw.setdefault("req_review", req_state["result"])
        return _result(*args, **kw)


    # ② Semantic Router + 多路检索（P0~P4 分层）
    #   ★ 增量修改：检索用理解额外并入上一轮指标/实体，确保模型仍能看到原表结构
    #     （用户只说"去掉应用主体分组"时，新问题可能不再点名原指标，召回不能丢表）。
    if modify_context:
        _retrieval_u = dict(understanding or {})
        _base_u = (modify_context.get("base_understanding") or {})
        for _k in ("metrics", "entities", "tables"):
            _bv = _base_u.get(_k) or []
            _nv = _retrieval_u.get(_k) or []
            _mv = list(_nv) + [v for v in _bv if v not in _nv]
            if _mv:
                _retrieval_u[_k] = _mv
        # ★ P0-4：原 SQL 实际使用的表必须优先保留，不得因本轮实体识别缺失/跨业务线
        #   过滤被排除。把 base_tables 显式并入检索表集合（带去库前缀的原始表名）。
        _bt = (modify_context.get("base_tables") or [])
        if _bt:
            _cur = _retrieval_u.get("tables") or []
            _retrieval_u["tables"] = list(_cur) + [t for t in _bt if t not in _cur]
        ctx = semantic_layer.retrieve_semantic(sidx, question, _retrieval_u)
    else:
        ctx = semantic_layer.retrieve_semantic(sidx, question, understanding)
    _mark("retrieve")
    _emit(task_state.EV_RETRIEVAL, {
        "tables": ctx.p3_tables or [],
        "metrics": ctx.p1_metrics or [],
        "route": getattr(ctx, "route_info", ""),
    })

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

    # ③ 冲突解决 + 分层 Prompt 组装（role → 时间口径 → P0→P1→P2→P3→P4 → 字典 → 硬约束 → 经验）
    #   ★ 组装逻辑已收敛到 _build_system_prompt（唯一位置），这里只负责"按什么顺序调"。
    resolved = semantic_layer.resolve(ctx)

    # ③.1 ★ 语义上下文审计（只观测、不阻断）：用统一注册表核对本次命中知识的治理状态。
    #   严格模式下 candidate/停用对象本不该混入；这里只打日志与 info 摘要，绝不拦截取数。
    _ctx_audit = {}
    try:
        _ctx_audit = semantic_builder.audit_context(ctx)
    except Exception as _e:
        print(f"⚠️ 语义上下文审计失败（不影响取数）：{_e}")

    # ③.7 ★ SQL 经验检索（成功案例参考 / 失败经验预防 / 已发布规则）
    #   为什么先检索再组装？因为经验块要作为【最后一段】拼进 prompt。
    #   为什么整段包 try？经验是加分项，它挂了最多"这次没参考到历史"，
    #   绝不能让取数本身失败。
    if getattr(config, "EXP_ENABLED", True):
        try:
            exp_match = sql_experience.retrieve(
                sidx, question, understanding, resolved,
                allowed_tables=sidx.get("allowed_tables") or [], user=user,
                # ★ 增量修改：检索时带上上一轮问题。
                #   "再加完成率"这种 5 字问句，单靠自身做向量检索几乎召不回真正的
                #   基线案例（问句太短、语义信息不足），等于每轮都在从零参考经验。
                context_question=((modify_context or {}).get("base_question") or "")
                if getattr(config, "EXP_MODIFY_CTX", True) else "")
        except Exception as e:
            print(f"⚠️ 经验检索失败（不影响取数）：{e}")
            exp_match = None

    # ③.5 ★ 业务域语义约束注入（第六步：业务域识别 → 语义对象获取 → 注入）
    #   ★ 命中业务域/主题 → 动态组装审核通过的语义对象作为权威约束（优先级高于经验参考）；
    #     未命中 / 开关关闭 → 空串，行为与接入前完全一致。
    #     整段包 try：语义注入是加分项，绝不能让取数本身失败。
    sm_block = ""
    if getattr(config, "SEMANTIC_MODEL_ENABLED", False):
        try:
            sm_block = business_domain_service.build_context_block(
                sidx, question, understanding, ctx)
        except Exception as e:
            print(f"⚠️ 业务域语义注入失败（不影响取数）：{e}")
            sm_block = ""

    system_prompt, dict_names, dict_reason, _pparts = _build_system_prompt(
        sidx, ctx, resolved, question,
        exp_block=(exp_match.prompt_block if exp_match is not None else ""),
        sm_block=sm_block)
    _dump_prompt(question, system_prompt, _pparts)

    # ★ 增量修改：把上一轮查询上下文作为独立段落注入系统提示，
    #   让 LLM 在"保留未被修改部分"的前提下只改用户要求的部分。
    if modify_context:
        system_prompt = system_prompt + "\n\n" + _modify_prompt_block(question, modify_context)

    tsum = calendar_util.summary_line(question)
    info = f"{ctx.route_info} | 字典:{dict_reason}" + (f" | {tsum}" if tsum else "")
    info += semantic_builder.audit_summary(_ctx_audit)
    _mark("resolve_prompt")

    # ④ SQL Agent：生成 SQL
    #   ★ 唯一例外：问题与某条「正式黄金 SQL」完全一致，且开关打开 → 直接采用它作为候选。
    #     采用后仍然要过下面的 ⑤ 校验闸门（校验的 SQL == 执行的 SQL 这条铁律不变），
    #     所以"复用"不等于"免检"。默认关闭（EXP_EXACT_REUSE=0），待评测后再决定是否开启。
    _check_cancel("SQL生成")
    _emit(task_state.EV_SQL_GENERATING, {"tables": ctx.p3_tables or []})
    raw_sql = ""
    if (exp_match is not None and exp_match.decision == "direct_reuse"
            and getattr(config, "EXP_EXACT_REUSE", False)
            and (exp_match.best_case or {}).get("sql_raw")):
        raw_sql = exp_match.best_case["sql_raw"]
        info += f" | 精确复用:{exp_match.best_case.get('case_no')}"
    if not raw_sql:
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

    # ④.9 ★ 需求契约：把"这次查询必须满足什么"固化下来
    #   ★ 只用于【判定】，绝不参与 SQL 生成 —— 模型仍然完全自主生成，
    #     本步只是把用户的要求写成可比对的期望（指标/维度/过滤/时间/替换对）。
    #   ★ enabled_for 是成本闸门：modify_only 模式下，普通首轮查询根本不建契约，
    #     等于零开销、零行为变化。
    if requirement_review.enabled_for(question, modify_context):
        try:
            req_state["contract"] = requirement_review.build_contract(
                question, understanding, modify_context)
        except Exception as e:
            print(f"⚠️ 需求契约构建失败（不影响取数）：{e}")
            req_state["contract"] = None

    # ★ 到这里才算"生成成功"（拒答/非查询语句在上面已经 return）
    chain["generated"] = True
    _emit(task_state.EV_SQL_VALIDATING, {"sql": sql})

    # ⑤ SQL Validator：生成后、执行前 5 维校验 + 自愈回灌
    allowed_tables = sidx["allowed_tables"]
    last_check = None
    last_issues = []
    fix_calls = 0                       # ★ 自动定点修复次数（任务书要求最多一次）
    modify_violated = False             # ★ 修改任务是否越界（未授权结构变更且修复未果）
    for attempt in range(1, config.MAX_RETRY + 1):
        # ★ 统一闸门：只读 + 表权限 + 五维校验 + 字段权限，一次调用全部覆盖
        chk = _pre_execute_check(sql, ctx, resolved, allowed_tables, user=user, meta=meta)
        last_check = chk
        last_issues = chk["issues"]
        if chk["ok"]:
            chain["gate_passed"] = True
            # ── ⑤.0 ★ SQL 修改结构保护（P0）：增量修改默认继承原 SQL 业务逻辑 ──
            #   ★ 只允许改用户【明确要求】的部分；表/聚合/业务过滤/分组等未授权变化一律拦截。
            #     自愈修复也会重跑这里（在循环内），保证「每次自愈都再次结构校验」。
            if modify_context and (modify_context.get("base_sql") or ""):
                _diff = sql_modify.diff_structure(modify_context["base_sql"], sql)
                _unauth = sql_modify.unauthorized_changes(_diff, question)
                if _unauth:
                    last_issues = [{
                        "severity": "error", "tier": "P0", "rule": "modify_scope",
                        "message": "修改越界（未授权变更）：" + "、".join(_unauth),
                        "suggestion": sql_modify.format_diff(_diff),
                    }]
                    if attempt >= config.MAX_RETRY or fix_calls >= config.MAX_AUTO_FIX:
                        modify_violated = True
                        break       # 修复次数用尽 → 循环外统一拒绝执行
                    # 受约束定点修复：把越界项回灌，要求恢复原结构（只改授权部分）
                    fix_user = sql_modify.build_repair_prompt(
                        question, modify_context["base_sql"], sql, _unauth, _diff)
                    fix_calls += 1
                    fixed_raw = llm.chat(system_prompt, fix_user)
                    sql = _finalize_sql(fixed_raw)
                    info += " | 修改越界修复:%d" % fix_calls
                    continue        # 修复后重过完整闸门 + 结构校验（下一轮循环）
            # ── ⑤.1 ★ 需求一致性审核（第六维，必须排在安全闸门【之后】）──
            #   ★ 顺序不可交换：越权/写操作永远先被拒，
            #     绝不能因为"模型说这是用户要的"而放行一条未授权 SQL。
            if req_state["contract"] is not None:
                q_sql = chk.get("final_sql") or sql     # 判"真正要执行的那条"
                # ★ 把历史经验摘要一起喂给审核：
                #   让"生成"和"审核"看到同一批证据，否则会出现
                #   "生成时参考了黄金 SQL，审核时却说写法不对"的自相矛盾。
                _digest = ""
                if exp_match is not None:
                    try:
                        _digest = exp_match.audit_digest()
                    except Exception as e:
                        print(f"⚠️ 经验摘要生成失败（不影响审核）：{e}")
                req_result = requirement_review.review(
                    q_sql, req_state["contract"],
                    exp_digest=_digest,
                    validator_issues=chk["issues"],
                    base_question=(modify_context or {}).get("base_question", ""),
                    base_sql=(modify_context or {}).get("base_sql", ""))
                req_state["result"] = req_result
                # ★ degraded（判不了）只在"没有硬错误"时才记 skipped；
                #   若确定性层已经抓到 error，那结论是可信的 fail，不能被降级成 skipped。
                if req_result.get("degraded") and req_result["verdict"] != "fail":
                    chain["requirement_verdict"] = "skipped"
                else:
                    chain["requirement_verdict"] = req_result["verdict"]

                _req_block = getattr(config, "REQ_REVIEW_BLOCK", False)
                # ── unsure：需求本身不明确 → 改 SQL 没用，直接停下去问用户 ──
                if _req_block and req_result["verdict"] == "unsure":
                    req_state["blocked"] = True
                    req_state["block_reason"] = "unsure"
                    break
                if (_req_block and req_result["verdict"] == "fail"
                        and req_result.get("errors")):
                    if attempt >= config.MAX_RETRY or fix_calls >= config.MAX_AUTO_FIX:
                        req_state["blocked"] = True
                        req_state["block_reason"] = "fail"
                        break       # 修复次数用尽 → 循环外统一拒绝执行
                    # 把具体问题回灌给模型，让它【自主】改（不由系统改写 SQL）
                    fix_user = prompt.build_requirement_repair_prompt(
                        question, sql, req_result["issues"],
                        base_question=(modify_context or {}).get("base_question", ""),
                        base_sql=(modify_context or {}).get("base_sql", ""))
                    fix_calls += 1                      # ★ 计一次定点修复
                    fixed_raw = llm.chat(system_prompt, fix_user)
                    sql = _finalize_sql(fixed_raw)
                    chain["requirement_attempts"] += 1
                    info += " | 需求一致性修复:%d" % chain["requirement_attempts"]
                    continue        # 修完必须重过完整闸门 + 重审（下一轮循环）
            break  # 通过校验
        if chk["hard"]:
            # 安全类问题不允许"自愈"绕过：直接拒绝
            return _rv(question, sql=sql, ok=False, error=chk["error"],
                       tables=ctx.p3_tables, metrics=resolved["metrics"],
                       dicts=dict_names, info=info, attempts=attempt)
        if attempt >= config.MAX_RETRY:
            break  # 用尽重试（是否执行由循环【之后】的闸门统一判定，见下）
        if fix_calls >= config.MAX_AUTO_FIX:
            break  # ★ 定点修复次数用尽（任务书要求最多一次），不再无限自愈
        # 自愈：把校验问题回灌模型修正
        #   ★ 必须带上【原始问题 + 上一版 SQL】：否则模型看不到用户要什么，
        #     会"修好语法、丢了语义"（实测：按月的查询被自愈成总数，月份维度整个丢失）。
        fix_user = (f"原始问题：{question}\n\n"
                    f"你上一版 SQL：\n{sql}\n\n"
                    f"该 SQL 未通过校验。请在【保持原问题统计口径与分组维度不变】的前提下，"
                    f"仅修正下列问题后只输出 SQL：\n" + _format_issues(chk["issues"]))
        fix_calls += 1                      # ★ 计一次定点修复
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

    # ★★ 修改越界闸门（fail-closed）：增量修改时，模型改了未授权的结构且修复未果 → 拒绝执行。
    #   ★ 保护原 SQL 业务逻辑：不能因为模型"重解释"就换表/换口径/换聚合。
    if modify_violated:
        r = _rv(question, sql=sql, ok=False,
                error="本次为【增量修改】，但生成的 SQL 变更了未授权的部分"
                      "（如表/聚合方式/业务过滤/分组），且自动修复后仍未恢复，"
                      "已拒绝执行以保护原查询业务逻辑。请重新描述本次要修改的具体内容。",
                tables=ctx.p3_tables, metrics=resolved["metrics"],
                dicts=dict_names, info=info, attempts=attempt)
        r["validation"] = last_issues
        r["validation_status"] = "blocked"
        r["result_warnings"] = []
        r["modify_violation"] = last_issues   # ★ 结构差异详情，供前端展示
        _mark("validate_rejected")
        r["trace"] = trace
        return r

    # ★★ 需求闸门（fail-closed）：安全检查全过，但"没答对问题"且修复用尽 → 拒绝执行。
    #   ★ 与上面那段分开，是为了让用户能分清"SQL 不合法"和"SQL 合法但不是我要的"——
    #     这两种失败的处理方式完全不同（前者补知识库，后者补充需求/确认歧义）。
    if req_state["blocked"]:
        _reason = req_state.get("block_reason") or "fail"
        _err = ("SQL 虽通过安全与口径校验，但本次需求本身存在歧义、无法确认，"
                "已暂停执行，请确认下面的问题后再查。"
                if _reason == "unsure" else
                "SQL 虽通过安全与口径校验，但未满足本次查询要求（已重试仍未达标），"
                "已拒绝执行，避免给出不符合需求的结果。")
        r = _rv(question, sql=sql, ok=False, error=_err,
                tables=ctx.p3_tables, metrics=resolved["metrics"],
                dicts=dict_names, info=info, attempts=attempt)
        r["validation"] = last_issues
        r["validation_status"] = last_check["status"] if last_check else "error"
        r["result_warnings"] = []
        # ★ 说明"我卡在哪、需要你确认什么"——比只回一句"已拒绝执行"有用得多
        try:
            _sem = (req_state["result"] or {}).get("semantic") or {}
            r["needs_clarification"] = requirement_review.build_clarification(
                req_state["contract"],
                (req_state["result"] or {}).get("issues") or [],
                verdict=_reason,
                assumptions=_sem.get("assumptions") or [])
        except Exception as e:
            print(f"⚠️ 歧义说明生成失败（不影响拒绝本身）：{e}")
            r["needs_clarification"] = None
        # ★ 失败经验回流：只在这种"确定没满足需求、且没执行"的情况下写。
        #   灰度期（BLOCK=0）不写 —— 那时 fail 里混着误报，写进去会污染经验库。
        if (getattr(config, "REQ_FAIL_RECORD", True) and sql
                and _reason != "unsure"):
            try:
                sql_experience.record_requirement_failure(
                    question, sql, (req_state["result"] or {}).get("issues") or [],
                    tables=ctx.p3_tables, metrics=resolved.get("metrics") or [],
                    understanding=understanding)
            except Exception as e:
                print(f"⚠️ 需求失败经验写入失败（不影响拒绝）：{e}")
        _mark("requirement_rejected")
        r["trace"] = trace
        return r

    # ★ 行级改写后，实际执行的 SQL 以闸门返回的 final_sql 为准（执行 SQL == 校验 SQL）
    sql = (last_check or {}).get("final_sql") or sql

    # ⑤.5 ★ 失败经验辅助校验（生成后、执行前）
    #   只做**确定性**检查（指纹相同 / 命中历史错表 / 缺状态条件），不调模型、不猜。
    #   ★ 默认只提示不拦截：经验检查的误报一旦能阻断执行，就会变成"新的事故来源"。
    #     只有 EXP_FAIL_REPAIR=1（评测 C/D 组）时，才对 error 级命中做一轮定向修复。
    exp_issues = []
    if exp_match is not None and getattr(config, "EXP_FAIL_PREVENT", True):
        try:
            exp_issues = sql_experience.check_sql(sql, exp_match)
        except Exception as e:
            print(f"⚠️ 失败经验检查失败（不影响取数）：{e}")
            exp_issues = []
        if exp_issues and getattr(config, "EXP_FAIL_REPAIR", False) and any(
                i.get("severity") == "error" for i in exp_issues):
            try:
                hint = sql_experience.build_repair_hint(exp_issues)
                fix_user = (f"原始问题：{question}\n\n你上一版 SQL：\n{sql}\n\n"
                            f"该 SQL 命中了历史错误经验。请在【保持统计口径与分组维度不变】的"
                            f"前提下，修正下列问题后只输出 SQL：\n{hint}")
                fixed = _finalize_sql(llm.chat(system_prompt, fix_user))
                chk2 = _pre_execute_check(fixed, ctx, resolved, allowed_tables,
                                          user=user, meta=meta)
                # ★ 修复版必须重新过闸门才算数；不通过就保留原 SQL（不能"越修越松"）
                if chk2["ok"]:
                    sql = chk2.get("final_sql") or fixed
                    last_check = chk2
                    last_issues = chk2["issues"]
                    exp_issues = sql_experience.check_sql(sql, exp_match)
                    info += " | 经验修复:1次"
            except Exception as e:
                print(f"⚠️ 经验定向修复失败（沿用原 SQL）：{e}")

    _mark("validate")

    # ⑥ ClickHouse 执行（★ 必须走唯一出口 _exec_sql：拒答/非查询语句绝不可能被执行）
    _check_cancel("SQL执行")
    _emit(task_state.EV_SQL_EXECUTING, {"sql": sql})

    def _rejected(reason, attempts=0):
        """闸门拦下时的统一返回（不执行、不重试）"""
        r = _rv(question, sql=sql, ok=False, error=reason,
                tables=ctx.p3_tables, metrics=resolved["metrics"],
                dicts=dict_names, info=info, attempts=attempts)
        r["validation"] = last_issues
        r["validation_status"] = last_check["status"] if last_check else "error"
        r["result_warnings"] = []
        r["experience_warnings"] = exp_issues
        _mark("not_executable")
        r["trace"] = trace
        return r

    try:
        chain["executed"] = True        # ★ 真的走到执行（与 ok 分离：执行了不代表拿到结果）
        columns, rows = _exec_sql(sql)
        chain["exec_ok"] = True
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
                chain["executed"] = True
                columns, rows = _exec_sql(sql)
                chain["exec_ok"] = True
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
    # ★ 结果体检结论进状态链：执行成功 ≠ 结果可信（比率越界/整列 NULL 都是"有数但可疑"）
    chain["result_sane"] = not bool(warnings)

    # ⑧ 自然语言回答 ＋ 事后自审（★ 并行）
    #
    # ★ 为什么把这两件事并行？
    #   两者【互不依赖】：一个把结果翻成人话，一个是事后逻辑诊断；
    #   它们只共享 metric_defs，谁都不读对方的结果。
    #   串行时实测 1.9s + 5.0s ≈ 6.9s；并行后 ≈ max(1.9, 5.0) ≈ 5.0s，
    #   一次取数省掉 1.9 秒，且返回结构一字未改（前端无需改动）。
    #
    # ★ 为什么不用 asyncio？
    #   本模块是纯同步代码（agent 被 CLI / 评测 / ablation 直接调用），
    #   引入 async 会把调用方全部染色。线程池只影响这两次 IO 等待。
    #
    # ★ 为什么 nl 的异常照旧往上抛、review 的异常吞掉？
    #   保持原语义：nl 是主结果的一部分（失败就是这次取数失败），
    #   review 是加分项（_review_sql 内部已 try，即使这里抛也不该影响结果）。
    metric_defs = "\n".join(m.get("definition", "") for m in ctx.p1_metrics)
    ambiguity = understanding.get("ambiguity", "")
    # ★ 分层自审：按风险等级决定是否/如何做 LLM 逻辑自审（LOW 跳过 / MEDIUM 轻量 / HIGH 深度）
    risk = _sql_risk_level(sql, ctx, resolved, last_check)
    nl, review = "", None
    # ★ 默认不再生成自然语言回答（want_answer=False）：改为前端「生成结论」按钮按需调用，
    #   省掉每次取数都多一次 LLM 往返。逻辑自审（review）按 risk 分层触发。
    if want_answer:
        if getattr(config, "TAIL_PARALLEL", True):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2,
                                                       thread_name_prefix="tail") as ex:
                f_nl = ex.submit(nl_answer.answer, question, columns, rows,
                                 metric_defs, ambiguity)
                f_rv = ex.submit(_review_sql, question, sql, metric_defs, risk=risk)
                nl = f_nl.result()                  # nl 的异常照旧抛出
                try:
                    review = f_rv.result()
                except Exception as e:
                    print(f"⚠️ SQL 逻辑自审失败（不影响主结果）：{e}")
        else:
            nl = nl_answer.answer(question, columns, rows, metric_defs, ambiguity)
            review = _review_sql(question, sql, metric_defs, risk=risk)
    else:
        review = _review_sql(question, sql, metric_defs, risk=risk)

    # ★ 换表说明：用户点名表 vs SQL 实际用表（见 _table_swap_note）
    swap = _table_swap_note(question, sql, sidx, ctx)
    if swap:
        info = info + " | 换表说明：" + swap

    r = _rv(question, sql=sql, ok=True, columns=columns, rows=rows,
                tables=ctx.p3_tables, metrics=resolved["metrics"],
                dicts=dict_names, info=info, attempts=attempt)
    r["table_swap"] = swap                              # ★ 独立字段，前端可高亮
    r["review"] = review                                    # 保留既有逻辑自审（事后复核）
    r["sql_risk"] = risk                                    # ★ 分层自审风险等级（LOW/MEDIUM/HIGH，可观测）
    r["validation"] = last_issues                           # SQL 校验报告
    r["validation_status"] = last_check["status"] if last_check else "error"
    r["result_warnings"] = warnings                         # 结果校验 warning
    # ★ 经验辅助校验结果（失败模式命中）；空列表 = 没命中，前端不用展示
    r["experience_warnings"] = exp_issues
    r["answer"] = nl                                        # 自然语言回答
    r["nl_meta"] = {"metric_defs": metric_defs,             # ★ 供前端「生成结论」按钮按需调用
                    "ambiguity": ambiguity}
    r["perm_decision"] = perm_decision                      # ★ 字段级权限判定（供审计/前端）
    r["masked"] = bool(mask_map)                            # ★ 是否发生结果脱敏
    r["mask_map"] = mask_map                                # ★ {列名: 脱敏规则}
    _mark("result_validate_answer")
    r["trace"] = trace                                      # 各阶段耗时（可观测性）
    return r


def ask(index, question, user=None, understanding_override=None, modify_context=None,
        want_answer=False, events=None, is_cancelled=None):
    """对外统一入口：全仓唯一生产取数主流程（ask_v2）。

    ★ 双流水线已收敛（P0 专项）：Legacy 兜底路径已退役，不再存在。
      任何异常一律【fail-closed】如实返回，绝不降级到弱校验路径——
      瞬时故障（超时/连接）应由连接层自愈（ssh_tunnel.ensure_tunnel）
      与 `_exec_sql` 的重试消化，而不是靠"换一条更弱的流水线"掩盖。
      这样既保证「只有一套业务编排逻辑」，也保证监控指标不失真。

    user  = 可信用户（dict，含 username / role）；非空时启用字段级权限闸门。
            生产链路 server.api_ask 总是传入；eval/CLI 不传（无真实用户权限语义）。
    understanding_override = 澄清模块确认后的意图（可选，锁口径用）。
    modify_context = 增量修改上下文（可选，见 ask_v2）。
    events = 阶段事件回调（可选，SSE 改造新增）：透传给 ask_v2。
    is_cancelled = 取消探测回调（可选）：透传给 ask_v2，用户取消时中止执行。
    """
    try:
        return ask_v2(index, question, user=user,
                      understanding_override=understanding_override,
                      modify_context=modify_context, want_answer=want_answer,
                      events=events, is_cancelled=is_cancelled)
    except task_state.TaskCancelled as tc:
        # ★ 用户主动取消：不是错误，如实返回"已取消"，绝不 fail-closed 成"异常"
        r = _result(question, sql="", ok=False, error=f"已取消：{tc}", pipeline="v2")
        r["cancelled"] = True
        return r
    except Exception as e:
        # fail-closed：不执行任何未经统一闸门校验的结果，把异常如实暴露出来
        reason = f"{type(e).__name__}: {e}"
        print(f"⛔ 统一主流程异常（fail-closed，不降级）：{reason}")
        # ★ 错误分类：LLM 4xx（402 余额/401 密钥/403 拒绝）给友好文案 + 结构化错误码，
        #   不再把技术堆栈甩给用户；其余异常维持原技术信息（便于排障）。
        le = exceptions.classify_llm_error(e)
        friendly = le.friendly() if le is not None else None
        r = _result(question, sql="", ok=False,
                    error=(friendly or f"企业级流水线异常，已中止（fail-closed）：{reason}"),
                    pipeline="v2")
        # ★ 保留 degrade_* 字段（前端/审计已在读）：degraded 恒为 False，
        #   只记录异常原因与阶段，供排障与告警使用，不再有"降级到第二套流水线"的语义。
        r["degraded"] = False
        r["degrade_stage"] = "v2"
        r["degrade_reason"] = reason
        if le is not None:
            r["error_code"] = le.status_code or le.kind   # ★ 结构化错误码（供统计/告警）
            if le.kind == "fatal":
                r["error_type"] = "llm_fatal"             # ★ 明确"不可重试"类别
        return r
