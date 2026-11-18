# -*- coding: utf-8 -*-
"""
clarification.py —— 多轮澄清交互（Query Clarification）决策与状态机

★ 定位（增量，不新建问数 Agent）
  在现有「Query Understanding → Semantic 检索 → SQL 生成」链路里，插一个**澄清决策环节**：
    用户问题 → 意图解析 → 可信候选消歧 → {直接执行 | 发起澄清 | 知识不足}
    → 用户确认 → 合并意图 → 交给现有 agent.ask 生成并执行（复用 RAG / Validator / Guard）。

★ 铁律（对应方案核心原则）
  ① 能可靠确定就【直接执行】，不多问一句。
  ② 候选【只来自已审核正式知识】：status ∈ {approved, 无 status} 且 enabled。
     candidate（AI 生成未复核）绝不当正式口径，也绝不当候选 ——
     顺带补上 enrich_index 未按 status 过滤 metrics 的既有缺口。
  ③ 用户选择 = 确认一个口径，**不改变该指标的业务定义**（定义仍从权威指标取）。
  ④ 找不到可信定义 → 明确 needs_knowledge，不编造表/字段/口径。
  ⑤ 已确认条件跨轮次保留，改一个条件不重置其它条件。
  ⑥ 一次最多 2 轮；仍无法消歧 → 转知识治理，不无限追问。

★ 纯逻辑模块：不连数据库、不发 LLM。
  数据（指标清单 / 用户回答）由调用方传入，便于单测（见 test_clarification.py）。
  会话状态持久化在 config_store（clarify_sessions 表），本模块只负责状态机逻辑。
"""
import json
import re
from datetime import date, timedelta

import governance
import calendar_util
import ambiguity              # ★ 歧义检测（时间/实体等确定性规则，见 ambiguity.py）

# ── 状态常量 ──
STATUS_PENDING = "pending"          # 待澄清（已发起）
STATUS_CLARIFYING = "clarifying"    # 澄清进行中（等待用户回答）
STATUS_NEEDS_KNOWLEDGE = "needs_knowledge"  # 知识不足，需补充定义/转管理员
STATUS_READY = "ready"              # 意图已消歧，可执行
STATUS_EXECUTING = "executing"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

MAX_CLARIFY_ROUNDS = 2              # 澄清轮次上限（超过转知识治理）

# 决策动作
ACTION_EXECUTE = "execute"
ACTION_CLARIFY = "clarify"
ACTION_UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════
# ① 可信指标过滤 + 候选消歧
# ═══════════════════════════════════════════════════════════
def _trusted(metric):
    """候选必须已审核 + 已开放。status 缺失按 approved 兼容（与 governance 一致）。"""
    st = (metric.get("status") or "").strip()
    return bool(metric.get("enabled", True)) and governance.visible_status(st or "approved")


def approved_metrics(metrics):
    """过滤出「可参与查询决策」的正式指标（approved + enabled）。"""
    return [m for m in (metrics or []) if _trusted(m)]


def _match_reason(question, m):
    """判断某指标与问题是否相关，返回 match 类别（exact/keyword）或 None。

    ★ 为什么不做「词根/概念」模糊匹配？
      试过按「金额/量/率」这类共享 token 召回，结果是「爽约赔付金额」会把
      代驾订单金额、大板订单金额全当候选 —— 用户明明在问一个【不存在的具体指标】，
      却被误判成"多个金额口径需澄清"。这是【臆造歧义】，违反"不编造"铁律。
      所以只认两条硬证据：
        ① 指标名整体出现在问题里（exact，最强，可锁口径）
        ② 指标注册的关键词出现在问题里（keyword）
    """
    q = question or ""
    name = m.get("name") or ""
    if not name:
        return None
    if name in q:
        return "exact"
    for kw in (m.get("keywords") or []):
        if kw and kw in q:
            return "keyword"
    return None


def resolve_metric_candidates(question, metrics):
    """
    从正式指标清单里找出与问题相关的候选，返回 (candidates, exact_named)。
      exact_named=True 表示问题【点名】了某个指标名 → 该指标口径锁死，不再消歧。
    """
    q = question or ""
    cands, exact_named = [], False
    for m in approved_metrics(metrics):
        r = _match_reason(q, m)
        if not r:
            continue
        if r == "exact":
            exact_named = True
        cands.append({
            "name": m.get("name"),
            "definition": m.get("definition") or "",
            "metric_type": m.get("metric_type"),
            "tables": m.get("tables") or [],
            "status": m.get("status") or "approved",
            "source": m.get("source") or "manual",
            "match": r,
            "required_predicates": m.get("required_predicates") or [],
            "forbidden_predicates": m.get("forbidden_predicates") or [],
        })
    return cands, exact_named


# ═══════════════════════════════════════════════════════════
# ② 决策路由
# ═══════════════════════════════════════════════════════════
def _candidate(m, match="exact"):
    """从指标记录构造候选 dict（统一口径，供 decide / resolve 复用）。"""
    return {
        "name": m.get("name"),
        "definition": m.get("definition") or "",
        "metric_type": m.get("metric_type"),
        "tables": m.get("tables") or [],
        "status": m.get("status") or "approved",
        "source": m.get("source") or "manual",
        "match": match,
        "required_predicates": m.get("required_predicates") or [],
        "forbidden_predicates": m.get("forbidden_predicates") or [],
    }


def decide(question, understanding=None, metrics=None):
    """
    澄清决策路由。返回：
      {action: execute|clarify|unknown, candidates:[...], reason:str}

    ★ 核心规则（修复「多指标被当成单选」退化）：
      · 多个【不同】的指标名 = 用户要【一起查】的多个指标，不是互斥选项 —— 直接执行、全部保留。
      · 只有「同一概念命中多个可信口径、且用户未点名任何一个」才是真实歧义 → clarify。
      · 有指标意图但 0 可信候选 → unknown（知识不足，停止猜测）。

    判断拆成四步（对应方案规则3）：
      A 指标识别：用户要求哪些指标？           → named（LLM 识别）/ cands（exact+keyword 命中）
      B 指标绑定：每个指标分别对应哪条正式定义？ → 按 name 精确匹配 approved 清单
      C 语义消歧：同一概念是否有多个冲突定义？   → 仅当 named 空 且 cands≥2 时触发
      D 执行可行性：交给 agent.ask（RAG/Validator/Guard）继续判断
    """
    u = understanding or {}
    cands, exact_named = resolve_metric_candidates(question, metrics)

    # named：LLM 识别出的指标名（authoritative 清单里的名字）
    named = [n for n in (u.get("metrics") or []) if n]

    # 把 named 绑定到正式定义（cands 里已有则复用，否则按 name 精确匹配正式清单）
    pool = {c["name"]: c for c in cands}
    bound = []
    for n in named:
        if n in pool:
            bound.append(pool[n])
        else:
            for m in approved_metrics(metrics):
                if m.get("name") == n:
                    bound.append(_candidate(m, "exact"))
                    break
    uniq_named = list({c["name"]: c for c in bound}.values())

    # ── ① 用户明确写出指标名（exact_named）→ 直接执行，全部保留 ──
    if exact_named:
        merged = {c["name"]: c for c in list(cands) + uniq_named}
        return {"action": ACTION_EXECUTE, "candidates": list(merged.values()),
                "reason": "问题明确写出指标名，直接执行"}

    # ── ② LLM 识别出明确指标名（named 非空）→ 多个指标一起查，不单选 ──
    if uniq_named:
        return {"action": ACTION_EXECUTE, "candidates": uniq_named,
                "reason": f"识别到 {len(uniq_named)} 个指标，一起查询"}

    # ── ③ 用户未点名、LLM 也未识别出指标名，仅 keyword 命中多个候选 → 澄清 ──
    uniq = {}
    for c in cands:
        uniq.setdefault(c["name"], c)
    uniq_cands = list(uniq.values())
    if len(uniq_cands) == 1:
        return {"action": ACTION_EXECUTE, "candidates": uniq_cands,
                "reason": "可信候选唯一，直接执行"}
    if len(uniq_cands) >= 2:
        return {"action": ACTION_CLARIFY, "candidates": uniq_cands,
                "reason": f"存在 {len(uniq_cands)} 个可信口径，需用户确认"}

    # 0 候选：判断用户是否真的在问一个指标（有"量/金额/率/数"意图信号）
    if _looks_like_metric(question):
        return {"action": ACTION_UNKNOWN, "candidates": [],
                "reason": "未检索到可信指标定义，缺少业务口径，停止猜测"}
    return {"action": ACTION_EXECUTE, "candidates": [],
            "reason": "无指标意图（描述性/展示性请求），交给现有流程处理"}


def _looks_like_metric(question):
    q = question or ""
    return any(k in q for k in ("量", "金额", "率", "数", "GMV", "客单价", "占比", "笔数"))


# ═══════════════════════════════════════════════════════════
# ②-b ★ 完整澄清决策（第二阶段：指标 + 时间 + 实体 统一路由）
# ═══════════════════════════════════════════════════════════
def decide_full(question, understanding, metrics, entities=None, confirmed_slots=None):
    """完整澄清决策：指标消歧（复用 decide）+ 时间/实体歧义（ambiguity.detect）。

    决策优先级（任务 §三）：
      D 阻断 —— 实体映射不明确 / 未核准（禁止静默换表）
      C 澄清 —— 时间歧义 > 实体歧义 > 指标歧义（一次只问最高优先级一个槽位）
      A 执行 —— 无歧义，直接生成 SQL
      B 默认口径 —— 轻微歧义 + 已审核默认口径（第三阶段接入）

    confirmed_slots：已确认的槽位（多轮澄清复用），已确认的槽位不再重复检测。

    返回：
      {action, candidates, reason, clarify_slot, clarify_options, ambiguities, caliber}
    """
    slots = confirmed_slots or {}
    # ① 实体歧义（含阻断）② 时间歧义 ③ 指标消歧（现有 decide）
    entity_issues = ([] if slots.get("entities")
                     else ambiguity.detect_entity_ambiguity(question, understanding, entities))
    time_issues = ([] if (slots.get("time") or {}).get("action_scope")
                   else ambiguity.detect_time_ambiguity(question, understanding))
    metric_decision = decide(question, understanding, metrics)
    ambiguities = entity_issues + time_issues

    # ── D 阻断：实体未核准 / 映射不明确 ──
    for iss in entity_issues:
        if iss.get("block"):
            return {
                "action": ACTION_UNKNOWN, "candidates": [],
                "reason": iss.get("message", "实体映射不明确，禁止静默换表"),
                "clarify_slot": None, "clarify_options": [],
                "ambiguities": ambiguities, "caliber": {},
            }

    # ── C 澄清：时间 > 实体 > 指标 ──
    if time_issues:
        iss = time_issues[0]
        return {
            "action": ACTION_CLARIFY,
            "candidates": metric_decision.get("candidates", []),
            "reason": iss.get("message", "时间范围存在歧义"),
            "clarify_slot": iss.get("slot"),
            "clarify_options": iss.get("options", []),
            "ambiguities": ambiguities, "caliber": {},
        }
    if entity_issues:
        iss = entity_issues[0]
        return {
            "action": ACTION_CLARIFY,
            "candidates": metric_decision.get("candidates", []),
            "reason": iss.get("message", "业务实体存在歧义"),
            "clarify_slot": iss.get("slot"),
            "clarify_options": iss.get("options", []),
            "ambiguities": ambiguities, "caliber": {},
        }
    if metric_decision["action"] == ACTION_CLARIFY:
        d = dict(metric_decision)
        d["clarify_slot"] = "metric"
        d["clarify_options"] = [
            {"value": c["name"], "label": c["name"], "definition": c.get("definition", "")}
            for c in metric_decision.get("candidates", [])
        ]
        d["ambiguities"] = ambiguities
        d["caliber"] = {}
        return d
    if metric_decision["action"] == ACTION_UNKNOWN:
        return {
            "action": ACTION_UNKNOWN, "candidates": [],
            "reason": metric_decision.get("reason", "缺少业务口径"),
            "clarify_slot": None, "clarify_options": [],
            "ambiguities": ambiguities, "caliber": {},
        }

    # ── A 执行 ──
    return {
        "action": ACTION_EXECUTE,
        "candidates": metric_decision.get("candidates", []),
        "reason": metric_decision.get("reason", "口径明确，直接执行"),
        "clarify_slot": None, "clarify_options": [],
        "ambiguities": ambiguities, "caliber": {},
    }


def build_slot_clarification(question, issue, round_no=1):
    """根据歧义检测结果（issue）生成一次澄清问题（复用前端 clarify 渲染）。

    每个选项展示：口径名称（label）、统计范围说明（desc）、与其它选项的区别（diff）。
    """
    slot = issue.get("slot") or "metric"
    options = []
    for o in (issue.get("options") or []):
        options.append({
            "value": o.get("value"),
            "label": o.get("label"),
            "definition": o.get("desc") or o.get("definition") or "",
            "diff": o.get("diff") or "",
            "source": o.get("source") or "",
            "status": o.get("status") or "",
        })
    return {
        "slot": slot,
        # ★ 结构化澄清协议（方案三.1）：type 区分组件渲染方式，required 标记必填
        "type": "time_range" if slot == "time" else "single_choice",
        "required": True,
        "multiple": False,                 # 单轮只选一个口径；多选能力保留给未来扩展
        "question": _slot_question(slot, question),
        "options": options,
        "allow_free_text": True,
        "allow_cancel": True,
        "round": round_no,
        "max_rounds": MAX_CLARIFY_ROUNDS,
        "hint": issue.get("message") or "选择后系统将按该口径继续生成 SQL。",
    }


# ═══════════════════════════════════════════════════════════
# ③ 澄清问题生成
# ═══════════════════════════════════════════════════════════
def build_clarification(question, candidates, slot="metric", round_no=1, extra_slots=None):
    """
    生成一次澄清问题。一次只问一个最重要的问题（slot）。
    每个候选项必须带正式定义 / 来源 / 审核状态 / 影响说明。
    """
    options = []
    for c in candidates:
        impact = _metric_impact(c)
        options.append({
            "value": c["name"],
            "label": c["name"],
            "definition": c["definition"],
            "source": c["source"],
            "status": c["status"],
            "impact": impact,
            "metric_type": c["metric_type"],
        })
    return {
        "slot": slot,
        # ★ 结构化澄清协议（方案三.1）
        "type": "time_range" if slot == "time" else "single_choice",
        "required": True,
        "multiple": False,
        "question": _slot_question(slot, question),
        "options": options,
        "allow_free_text": True,
        "allow_cancel": True,
        "round": round_no,
        "max_rounds": MAX_CLARIFY_ROUNDS,
        "hint": "选择后系统将按该指标的正式定义继续生成 SQL；「其他」可自行说明或提交知识补充。",
        "extra_slots": extra_slots or [],
    }


def _slot_question(slot, question):
    if slot == "metric":
        return "你想统计哪种口径？请选择对应指标。"
    if slot == "time":
        return "请确认统计时间范围（或直接输入，如「上一个自然月」「9月1日至9月30日」）。"
    return "请补充或确认以下条件。"


def _metric_impact(c):
    """给出该候选口径对结果的实质影响说明（用于让用户判断选哪个）。"""
    preds = c.get("required_predicates") or []
    forb = c.get("forbidden_predicates") or []
    notes = []
    if preds:
        notes.append("需过滤：" + "、".join(preds))
    if forb:
        notes.append("禁止过滤：" + "、".join(forb))
    if c.get("metric_type"):
        notes.append(f"类型={c['metric_type']}")
    return "；".join(notes) if notes else "—"


# ═══════════════════════════════════════════════════════════
# ④ 会话状态
# ═══════════════════════════════════════════════════════════
def new_state(username, question, understanding=None, metrics=None, entities=None,
              decision=None):
    """创建一次澄清任务的初始状态（含口径对象 caliber）。

    ★ decision 可选：调用方（server）已用 decide_full 决策时传入，避免重复决策；
      不传时内部用 decide_full（无时间/实体歧义时与旧 decide 行为一致）。
    """
    u = understanding or {}
    if decision is None:
        decision = decide_full(question, u, metrics, entities)
    # ★ 实体映射确认（即使理解层降级未识别，也把已审核映射写进 caliber 防静默换表）
    ents = ambiguity.resolve_entities(question, u, entities)
    return {
        "session_id": "",                 # 由服务端回填（sessions.token）
        "query_id": "",                   # 由服务端生成
        "username": username,
        "original_question": question,
        "intent": {
            "intent": u.get("intent", "summary"),
            "metrics": u.get("metrics") or [],
            "entities": u.get("entities") or [],
            "dimensions": u.get("dimensions") or [],
            "filters": u.get("filters") or [],
            "time_range": u.get("time_range") or "",
            "ambiguity": u.get("ambiguity") or "",
        },
        "confirmed_slots": {
            "metric": decision["candidates"][0]["name"]
                        if decision["action"] == ACTION_EXECUTE and decision["candidates"]
                        else None,
            # ★ 业务实体（任务§五 entity_definition）：问题涉及的已审核实体映射
            "entities": [{
                "name": e["name"], "table": e["table_name"],
                "grain": e.get("grain") or "", "definition": e.get("comment") or "",
                "status": e.get("status") or "approved",
                "source": e.get("source") or "manual",
                "version": e.get("version") or 1,
            } for e in ents],
            "time": _resolve_time(question),
            "filters": list(u.get("filters") or []),
            # ★ 口径对象元信息（任务§五，全部存 confirmed_slots JSON，不新增表字段）
            "assumptions": [],
            "selected_option": None,
            "source_version": {"entities": [e.get("version") for e in ents]},
            "confirmed_by": None,
            "confirmed_at": None,
        },
        "pending_clarifications": [],
        "candidate_evidence": decision["candidates"],
        "clarification_round": 0,
        "status": STATUS_READY if decision["action"] == ACTION_EXECUTE else STATUS_PENDING,
        "action": decision["action"],
        "reason": decision["reason"],
        "final_sql": "",
        "execution_result": None,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def _resolve_time(question):
    """用 calendar_util 确定性解析时间表达，返回规范区间 dict（含左闭右开边界）。"""
    today = date.today()
    exprs = [e for e in calendar_util.parse_time_expressions(question, today) if e.get("start")]
    if not exprs:
        return None
    e = exprs[0]                         # 取第一个明确区间（与现有主链路一致）
    start, end = e["start"], e["end"]     # end 为闭区间最后一天
    return {
        "expr": e["expr"],
        "desc": e["desc"],
        "start": start.isoformat(),
        "end": end.isoformat(),
        # ★ 左闭右开边界（方案 §5 示例口径）：[start 00:00:00, end_exclusive 00:00:00)
        "end_exclusive": (end + timedelta(days=1)).isoformat(),
    }


def prev_natural_month(today=None):
    """上一个自然月，返回 (start, end_inclusive, end_exclusive) 三个 date。
    按业务时区（本机本地时间）计算；2026-10 的上一自然月 = [2026-09-01, 2026-10-01)。
    """
    today = today or date.today()
    first = today.replace(day=1)
    last_prev = first - timedelta(days=1)
    start = last_prev.replace(day=1)
    end_exclusive = first                     # 本月 1 号 00:00:00
    return start, last_prev, end_exclusive


# ═══════════════════════════════════════════════════════════
# ⑤ 回答合并
# ═══════════════════════════════════════════════════════════
def apply_answer(state, answer, metrics=None, entities=None):
    """
    处理用户回答，返回 (state, outcome)。
    outcome ∈ {next_clarify, ready, cancelled, needs_knowledge}
    原则：只改用户指定的条件，其余 confirmed_slots 保留。
    ★ 第二阶段扩展：支持 time / entity 槽位（此前只有 metric）。
    """
    state = dict(state)                    # 不原地改调用方对象
    answer = (answer or "").strip()
    round_no = int(state.get("clarification_round") or 0) + 1
    state["clarification_round"] = round_no
    state["updated_at"] = _now_iso()

    # ── 取消 ──
    if answer in ("取消", "cancel", "取消查询"):
        state["status"] = STATUS_CANCELLED
        return state, "cancelled"

    # 当前澄清槽位
    pending = state.get("pending_clarifications") or []
    slot = pending[0].get("slot", "metric") if pending else "metric"

    # ── ★ time 槽位：行为时间口径 ──
    if slot == "time":
        opt = _match_slot_option(answer, pending[0].get("options") or [])
        if opt is not None:
            state["confirmed_slots"]["time"] = _apply_time_scope(
                state["confirmed_slots"].get("time"), opt)
            state["confirmed_slots"]["selected_option"] = {
                "slot": "time", "value": opt.get("value"), "label": opt.get("label")}
            state["confirmed_slots"]["confirmed_by"] = state.get("username")
            state["confirmed_slots"]["confirmed_at"] = _now_iso()
            state["pending_clarifications"] = []
            return _finalize(state, metrics, entities)
        # 自由文本时间（如「注册后7天内」「10月内」）
        t = _resolve_time(answer)
        if t:
            state["confirmed_slots"]["time"] = t
            state["confirmed_slots"]["confirmed_by"] = state.get("username")
            state["confirmed_slots"]["confirmed_at"] = _now_iso()
            state["pending_clarifications"] = []
            return _finalize(state, metrics, entities)
        state["status"] = STATUS_NEEDS_KNOWLEDGE
        state["pending_clarifications"] = [{
            "slot": "time", "question": "无法识别时间口径，请选择选项或输入具体时间范围。",
            "user_text": answer,
        }]
        return state, "needs_knowledge"

    # ── ★ entity 槽位：业务实体 ──
    if slot == "entity":
        opt = _match_slot_option(answer, pending[0].get("options") or [])
        if opt is not None:
            ent = _find_entity_by_name(opt.get("value"), entities)
            state["confirmed_slots"]["entities"] = [_entity_slot(ent or opt)]
            state["confirmed_slots"]["selected_option"] = {
                "slot": "entity", "value": opt.get("value"), "label": opt.get("label")}
            state["confirmed_slots"]["confirmed_by"] = state.get("username")
            state["confirmed_slots"]["confirmed_at"] = _now_iso()
            state["pending_clarifications"] = []
            return _finalize(state, metrics, entities)
        state["status"] = STATUS_NEEDS_KNOWLEDGE
        state["pending_clarifications"] = [{
            "slot": "entity", "question": "无法识别业务实体，请选择选项或补充说明。",
            "user_text": answer,
        }]
        return state, "needs_knowledge"

    # ── metric 槽位（原有逻辑）──
    cands = state.get("candidate_evidence") or []
    chosen = _match_option(answer, cands)
    if chosen is None:
        # 尝试从自由文本里解析出有效条件（时间/过滤/点名指标）
        merged_free = _merge_free_text(state, answer, metrics)
        if merged_free is not None:
            state = merged_free
            return _finalize(state, metrics, entities)
        state["status"] = STATUS_NEEDS_KNOWLEDGE
        state["pending_clarifications"] = [{
            "slot": "metric", "question": "无法识别所选口径，请补充业务定义或转管理员维护。",
            "user_text": answer,
        }]
        return state, "needs_knowledge"

    # ── 命中候选：写入 confirmed_slots.metric，保留其它条件 ──
    state["confirmed_slots"]["metric"] = chosen
    state["intent"]["metrics"] = [chosen]
    state["pending_clarifications"] = []
    return _finalize(state, metrics, entities)


def _match_slot_option(answer, options):
    """从 slot options 里匹配用户选择，返回 option dict 或 None。"""
    for o in (options or []):
        if answer in (o.get("value"), o.get("label")):
            return o
    return None


def _apply_time_scope(time, option):
    """把行为时间口径选项落到 confirmed_slots.time（含 action_scope）。"""
    t = dict(time or {})
    t["action_scope"] = option.get("value")
    t["action_scope_label"] = option.get("label")
    t["action_scope_desc"] = option.get("definition") or option.get("desc") or ""
    return t


def _entity_slot(ent):
    """把实体（dict 或 option dict）规范化为 confirmed_slots.entity。"""
    return {
        "name": ent.get("name") or ent.get("label") or "",
        "table": ent.get("table") or ent.get("table_name") or "",
        "grain": ent.get("grain") or "",
        "definition": ent.get("comment") or ent.get("definition") or "",
        "status": ent.get("status") or "approved",
        "source": ent.get("source") or "manual",
        "version": ent.get("version") or 1,
    }


def _find_entity_by_name(name, entities):
    for e in (entities or []):
        if e.get("name") == name:
            return e
    return None


def _match_option(answer, candidates):
    """回答命中候选（支持选项 value / label / 中文名直接匹配）。"""
    for c in candidates:
        if answer in (c.get("value"), c.get("name"), c.get("label")):
            return c.get("name")
    return None


def _merge_free_text(state, answer, metrics):
    """自由文本：尝试解析出【点名指标】/【时间】/【补充过滤】并合并，能解析就返回；否则 None。

    原则：只改用户指定的条件，其余 confirmed_slots 原样保留（方案原则⑥）。
    """
    if not answer:
        return None
    # ① 点名指标（自由文本里出现了某个可信指标名）→ 锁口径
    cands, exact_named = resolve_metric_candidates(answer, metrics)
    if exact_named and cands:
        state["confirmed_slots"]["metric"] = cands[0]["name"]
        state["intent"]["metrics"] = [cands[0]["name"]]
        return state
    # ② 时间表达（「上个月」「上周」「9月1日至9月30日」）→ 更新 time，保留其它
    t = _resolve_time(answer)
    if t:
        state["confirmed_slots"]["time"] = t
        return state
    # ③ 补充过滤（「只看临沂」「仅已完成」）→ 追加到 filters，保留指标与时间
    extra = _parse_filter(answer)
    if extra:
        filters = list(state["confirmed_slots"].get("filters") or [])
        if extra not in filters:
            filters.append(extra)
        state["confirmed_slots"]["filters"] = filters
        return state
    return None


def _parse_filter(answer):
    """把「只看/仅/只要 X」解析成一条过滤条件；不是过滤意图则返回 None。"""
    m = re.match(r"^\s*(?:只看|仅看|只要|仅|筛选|过滤)[：:\s]*(.+?)\s*$", answer)
    return m.group(1) if m else None


def _finalize(state, metrics, entities=None):
    """确认条件后的最终判定：口径唯一可信 → ready；仍有歧义 → 下一轮 / 知识不足。"""
    metric = state["confirmed_slots"].get("metric")
    if metric:
        # 已锁定指标：校验该指标是否仍是可信正式定义
        allc, _ = resolve_metric_candidates(state["original_question"], metrics)
        lock = [c for c in allc if c["name"] == metric]
        if lock:
            state["status"] = STATUS_READY
            state["action"] = ACTION_EXECUTE
            state["candidate_evidence"] = lock
            return state, "ready"
        # 锁定的指标不在可信清单里（被停用/降级）→ 知识不足
        state["status"] = STATUS_NEEDS_KNOWLEDGE
        state["reason"] = f"指标「{metric}」无已审核正式定义，停止生成 SQL"
        return state, "needs_knowledge"

    # 没锁指标：重做完整决策（含时间/实体），但已确认的槽位不再重复检测
    d = decide_full(state["original_question"], state.get("intent"), metrics, entities,
                    confirmed_slots=state.get("confirmed_slots"))
    if d["action"] == ACTION_CLARIFY and state["clarification_round"] >= MAX_CLARIFY_ROUNDS:
        state["status"] = STATUS_NEEDS_KNOWLEDGE
        state["reason"] = f"澄清 {MAX_CLARIFY_ROUNDS} 轮后仍无法消歧，请补充业务定义或转管理员"
        return state, "needs_knowledge"
    if d["action"] == ACTION_CLARIFY:
        slot = d.get("clarify_slot") or "metric"
        state["status"] = STATUS_CLARIFYING
        state["pending_clarifications"] = [_pending_for_slot(
            state["original_question"], d, slot, state["clarification_round"] + 1)]
        return state, "next_clarify"
    if d["action"] == ACTION_UNKNOWN:
        state["status"] = STATUS_NEEDS_KNOWLEDGE
        state["reason"] = d.get("reason") or state.get("reason")
        return state, "needs_knowledge"
    state["status"] = STATUS_READY
    state["action"] = ACTION_EXECUTE
    return state, "ready"


def _pending_for_slot(question, decision, slot, round_no):
    """按槽位生成一次待确认澄清问题（metric 走旧 build_clarification，其余走 slot）。"""
    if slot == "metric":
        return build_clarification(question, decision.get("candidates", []),
                                   "metric", round_no)
    issue = {"slot": slot, "options": decision.get("clarify_options", []),
             "message": decision.get("reason", "")}
    return build_slot_clarification(question, issue, round_no)


# ═══════════════════════════════════════════════════════════
# ⑥ 生成可执行意图（交给现有 agent.ask）
# ═══════════════════════════════════════════════════════════
def build_final_question(state):
    """把已确认条件合并回一个明确的自然语言问题，交给现有 agent.ask 执行。"""
    q = state.get("original_question") or ""
    slots = state.get("confirmed_slots") or {}
    metric = slots.get("metric")
    t = slots.get("time")
    entities = slots.get("entities") or []
    parts = []
    if metric:
        parts.append(f"统计指标「{metric}」")
    if entities:
        names = "、".join(e["name"] for e in entities if e.get("name"))
        tables = "、".join(e["table"] for e in entities if e.get("table"))
        parts.append(f"业务实体「{names}」" + (f"（落地表 {tables}）" if tables else ""))
    if t:
        parts.append(f"时间范围为{t['start']} 至 {t['end']}")
        # ★ 行为时间口径（时间澄清确认后落地为明确约束）
        if t.get("action_scope"):
            scope_map = {
                "since_reg": "行为发生在注册后至当前数据截止时间（累计）",
                "within_anchor": "行为发生在与注册时间同一时间区间内",
                "since_reg_window": "行为发生在注册后指定时间窗口内（需进一步明确窗口长度）",
            }
            parts.append(scope_map.get(t["action_scope"], t.get("action_scope_label") or ""))
    filters = slots.get("filters") or []
    if filters:
        parts.append("筛选条件：" + "、".join(filters))
    if parts:
        return f"{q}（已确认口径：{'；'.join(parts)}）"
    return q


def build_understanding_override(state):
    """从已确认意图构造 understanding_override，锁死指标/实体，避免 LLM 再猜。"""
    intent = state.get("intent") or {}
    slots = state.get("confirmed_slots") or {}
    metric = slots.get("metric")
    entities = slots.get("entities") or []
    t = slots.get("time") or {}
    override = {
        "intent": intent.get("intent", "summary"),
        "metrics": [metric] if metric else (intent.get("metrics") or []),
        "entities": [e["name"] for e in entities if e.get("name")]
                    or (intent.get("entities") or []),
        "dimensions": intent.get("dimensions") or [],
        "time_range": t.get("expr", "") or intent.get("time_range", ""),
        "filters": slots.get("filters") or intent.get("filters") or [],
        "ambiguity": "",
        "degraded": False,
    }
    # ★ 行为时间口径（供 SQL 生成阶段落实）
    if t.get("action_scope"):
        override["action_scope"] = t["action_scope"]
        override["action_scope_label"] = t.get("action_scope_label", "")
    return override


def summarize(state, result=None):
    """把本次采用的口径/时间/过滤整理成给用户看的摘要。"""
    slots = state.get("confirmed_slots") or {}
    return {
        "metric": slots.get("metric"),
        "time": (slots.get("time") or {}).get("desc") or (slots.get("time") or {}).get("start"),
        "filters": slots.get("filters") or [],
        "status": state.get("status"),
    }


# ── 工具 ──
def _now_iso():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def to_json(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def from_json(s):
    try:
        return json.loads(s) if s else None
    except Exception:
        return None
