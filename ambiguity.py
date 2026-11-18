# -*- coding: utf-8 -*-
"""
ambiguity.py —— 歧义检测模块（Clarification Engine 的确定性检测层）

★ 定位：在「查询理解 → 澄清决策」之间，做【确定性】歧义识别（不调 LLM、不连库）。
  输入：用户问题 + 查询理解结果 + 已审核知识（实体清单）
  输出：结构化歧义清单，供 clarification.decide_full 路由到 A/B/C/D 四策略。

★ 为什么是确定性规则、而不是再调一次 LLM？
  澄清决策是【闸门】，必须可复现、可单测。任务验收 §5 要求
  「同一问题相同条件下，多次独立运行的口径保持一致」——
  LLM 有随机性，不能当闸门；只有确定性规则能保证可复现。

★ 七类歧义（任务 §二），本阶段（第二阶段）先落地高频两类：
  1. 时间范围歧义 → detect_time_ambiguity
  3. 业务实体歧义 → detect_entity_ambiguity
  其余（指标口径 / 统计范围 / 数据来源 / 关联关系 / 过滤条件）在第三阶段接入。

★ 严重度：high = 必须澄清（不同解释会显著影响结果）；low = 可默认（有已审核默认口径）。
"""
import re

# 澄清槽位
SLOT_TIME = "time"
SLOT_ENTITY = "entity"
SLOT_METRIC = "metric"

SEV_HIGH = "high"
SEV_LOW = "low"


# ═══════════════════════════════════════════════════════════
# ① 时间范围歧义
# ═══════════════════════════════════════════════════════════
# 注册/锚点时间词（锚点 = 有一个明确时间维度已锁定）
_ANCHOR_WORDS = ("注册", "新注册", "首次注册")
# 行为词（行为时间维度）：「发单」「下单」「成交」「完单」「有X订单」等
#   正则：动词 + 0~6 个非标点字符 + 单/订单
_ACTION_RE = re.compile(r"(?:发|下|成交|完|买|有|做过)[^，。！？；、]{0,6}(?:单|订单)")
# 明确时间范围词（用于判断「问题是否给了时间」）
_TIME_WORDS = ("本月", "上月", "当月", "今年", "去年", "本年", "上半年", "下半年",
               "至今", "累计", "截至目前", "近", "最近", "近30天", "近7天", "近半年")
# 行为时间已有修饰的信号词（出现在行为词之前时，认为行为时间已明确）
_ACTION_TIME_MODS = ("内", "后", "当月", "本月", "上月", "至今", "累计", "期间",
                     "以内", "之前", "以来", "截至目前")


def _has_time_word(q):
    """问题里是否出现明确时间范围表达（自然月 / 相对时间词）。"""
    return any(w in q for w in _TIME_WORDS) or bool(re.search(r"\d+\s*月", q))


def _action_has_time(q):
    """行为词附近是否有时间修饰（「10月内发单」「注册后发单」「注册后7天内」）。"""
    for m in _ACTION_RE.finditer(q):
        ctx = q[max(0, m.start() - 8):m.start()]
        if any(k in ctx for k in _ACTION_TIME_MODS):
            return True
    return False


def detect_time_ambiguity(question, understanding=None):
    """时间范围歧义检测。

    核心场景（#337/#339）：「10月注册客户有发小板订单的客户占比」——
    「10月」只锁定了【注册时间】，但「发小板订单」的【行为时间】未明确：
      ① 注册后至当前数据截止时间（累计）
      ② 注册后且在锚点区间内（如 10 月内）
      ③ 注册后指定时间窗口（需补充窗口长度）

    返回歧义清单（空列表 = 无时间歧义）。每项含 2~4 个候选口径选项。
    """
    q = question or ""
    u = understanding or {}
    if not (any(k in q for k in _ANCHOR_WORDS) and _ACTION_RE.search(q)):
        return []
    # 时间范围：优先理解结果，其次正则兜底；两者都无 → 连注册时间都没给（不属本规则）
    if not (u.get("time_range") or _has_time_word(q)):
        return []
    # 行为时间已明确修饰 → 无歧义
    if _action_has_time(q):
        return []
    return [{
        "type": "time_scope",
        "slot": SLOT_TIME,
        "severity": SEV_HIGH,
        "message": ("「发单/下单」等行为发生的时间范围未明确（注册时间已锁定，"
                    "但行为发生在注册后什么时候不清楚）"),
        "options": [
            {"value": "since_reg", "label": "注册后至当前",
             "desc": "统计注册后截至【当前数据截止时间】发生的行为（累计口径）",
             "diff": "时间跨度最长，含跨期行为"},
            {"value": "within_anchor", "label": "与注册时间同区间",
             "desc": "统计【与注册时间同一时间区间内】发生的行为（如 10 月内）",
             "diff": "只统计该区间内的行为，跨期行为不计"},
            {"value": "since_reg_window", "label": "注册后指定时间窗口",
             "desc": "统计注册后【指定天数/月数内】发生的行为",
             "diff": "需补充窗口长度，如「注册后 7 天内」"},
        ],
        "default": None,
        "default_source": None,
    }]


# ═══════════════════════════════════════════════════════════
# ② 业务实体歧义 / 映射确认
# ═══════════════════════════════════════════════════════════
def _entity_names(ent):
    """实体名 + 别名（keywords），去空。"""
    names = [ent.get("name") or ""]
    names += list(ent.get("keywords") or [])
    return [n for n in names if n]


def _find_entity(name, entities):
    for e in (entities or []):
        if e.get("name") == name:
            return e
    return None


def detect_entity_ambiguity(question, understanding, entities):
    """业务实体歧义 / 映射确认检测（任务 §四）。

    规则：
      ① 理解层识别出的实体，其 status 非 approved → 阻断（未核准，不许静默换表）。
      ② 理解层识别出的实体名不在清单（映射断裂）→ 阻断。
      ③ 问题提到【同一粒度（grain）下的多个候选实体】且理解层未点名任何一个
         → 实体歧义 → 澄清（如「小板订单」vs「代驾订单」都是订单粒度）。
         ★ 关键：不同粒度是【正常关联】（客户 × 小板订单），不是歧义。
      ④ 其余 → 无实体歧义。

    返回歧义清单（空列表 = 无实体歧义/阻断）。
    """
    q = question or ""
    u = understanding or {}
    ent_list = entities or []

    # ── ①② 理解层已识别出的实体 → 校验映射与核准状态 ──
    for name in (u.get("entities") or []):
        ent = _find_entity(name, ent_list)
        if ent is None:
            return [{
                "type": "entity_unknown", "slot": SLOT_ENTITY, "severity": SEV_HIGH,
                "message": f"识别到的实体「{name}」未在已审核实体清单中，映射不明确，禁止静默换表",
                "options": [], "default": None, "default_source": None, "block": True,
            }]
        if (ent.get("status") or "approved") != "approved":
            return [{
                "type": "entity_unapproved", "slot": SLOT_ENTITY, "severity": SEV_HIGH,
                "message": f"业务实体「{name}」尚未通过业务审核（status={ent.get('status')}），"
                           "不生成可能误导的 SQL",
                "options": [], "default": None, "default_source": None, "block": True,
            }]

    # ── ③ 同粒度多候选 → 实体歧义 ──
    mentioned = [e for e in ent_list
                 if any(n and n in q for n in _entity_names(e))]
    if not mentioned:
        return []
    recognized = set(u.get("entities") or [])
    by_grain = {}
    for e in mentioned:
        by_grain.setdefault(e.get("grain") or "", []).append(e)
    for grain, ents in by_grain.items():
        if len(ents) < 2:
            continue
        if any(e.get("name") in recognized for e in ents):
            continue                     # 理解层已点名其中一个 → 明确
        options = [{
            "value": e["name"], "label": e["name"],
            "desc": e.get("comment") or "（无说明）",
            "diff": f"落地表 {e.get('table_name') or '（未登记）'}",
        } for e in ents]
        return [{
            "type": "entity_ambiguous", "slot": SLOT_ENTITY, "severity": SEV_HIGH,
            "message": f"问题涉及多个「{grain}」类实体，请确认以哪个为准",
            "options": options, "default": None, "default_source": None, "block": False,
        }]
    return []


def resolve_entities(question, understanding, entities):
    """解析问题涉及的所有【已审核】实体（写进口径对象 caliber，防静默换表）。

    ★ 目的：即使理解层（LLM）降级没识别出实体，只要问题里的实体词能映射到
      approved 实体，就把「实体→表」写进 caliber 注入 SQL 生成，防止模型自己
      从 RAG 召回表时【静默换表】。

    返回已审核实体列表（问题提到的全部，含跨粒度关联的实体）。
    """
    q = question or ""
    mentioned = [e for e in (entities or [])
                 if any(n and n in q for n in _entity_names(e))]
    return [e for e in mentioned if (e.get("status") or "approved") == "approved"]


# ═══════════════════════════════════════════════════════════
# ③ 汇总入口（第二阶段：时间 + 实体）
# ═══════════════════════════════════════════════════════════
def detect(question, understanding, entities=None):
    """检测全部（本阶段：时间 + 实体）歧义，返回歧义清单。

    ★ 顺序即优先级：阻断（block）排最前，其次实体歧义，最后时间歧义。
    """
    issues = []
    issues += detect_entity_ambiguity(question, understanding, entities)
    issues += detect_time_ambiguity(question, understanding)
    return issues
