# -*- coding: utf-8 -*-
"""
semantic_layer.py —— 企业级「语义层」（Semantic Layer）

★ 职责：把知识按优先级分成四层，并做冲突解决 + 多路检索编排：
    P0 系统规则（sql_rules）
    P1 指标定义（Metric Registry，权威口径，不可被覆盖）
    P2 实体 / 关系 / Join 规则（Entity & Relationship Registry）
    P3 表字段 Schema（Schema Registry）
    P4 普通业务文档（Doc Registry，仅补充上下文）

★ 设计原则：
    ① 本文件【不重写】任何现有模块，只在其上编排；rag / knowledge / config_store 全部复用。
    ② 优先级铁律：高优先级胜出，低优先级只补充、不覆盖。
    ③ RAG 检索到的 P3/P4 上下文，绝不覆盖 P0~P2 的硬约束。

★ 与 rag.build_index 的关系：
    调用方先 `idx = rag.build_index()`，再 `idx = semantic_layer.enrich_index(idx)`，
    得到带 entities/relationships/docs 向量的「语义索引」，之后传给 retrieve_semantic。
"""
import config
import config_store
import knowledge
import rag
import governance


# ═══════════════════════════════════════════════════════════
# 优先级常量
# ═══════════════════════════════════════════════════════════
# ★ 这里 P0~P4 是【执行时检索优先级】（决定检索/注入顺序），数值 0~4。
#   落库侧的分层已显式建模为每张知识表的 knowledge_level 字段，
#   其「表名 → 层级」映射的唯一事实源在 config_store.KNOWLEDGE_LEVEL（两者数值对齐）。
P0, P1, P2, P3, P4 = 0, 1, 2, 3, 4
TIER_NAME = {0: "P0", 1: "P1", 2: "P2", 3: "P3", 4: "P4"}


# ═══════════════════════════════════════════════════════════
# ① 给索引补充 实体 / 关系 / 文档 向量
# ═══════════════════════════════════════════════════════════
def enrich_index(index):
    """
    在 rag.build_index() 产出的索引上，补充 Semantic Layer 需要的知识与其向量。
    ★ 不破坏原索引的任何字段（tables/metrics/dicts/... 原样保留）。
    """
    model = index.get("model")
    if model is None:
        return index

    entities = [e for e in knowledge.get_entities() if e.get("enabled", True)]  # 停用实体不进向量索引
    # ★ 治理准入（status）：approved/无 status 可见；candidate 仅非严格模式可见
    _vis_e = config_store.get_visible_knowledge_ids("entities", "name")
    if _vis_e is not None:
        entities = [e for e in entities if e["name"] in _vis_e]
    relationships = knowledge.get_relationships(enabled_only=True)
    _vis_r = config_store.get_visible_knowledge_ids("relationships", "id")
    if _vis_r is not None:
        relationships = [r for r in relationships if r["id"] in _vis_r]
    docs = knowledge.get_kb_docs(enabled_only=True)
    # ★ 治理准入（status）：业务文档是 P4 层，优先级最低，但不能当"免检通道"——
    #   AI 生成的文档同样必须复核后才进检索。
    _vis_doc = config_store.get_visible_knowledge_ids("kb_docs", "id")
    if _vis_doc is not None:
        docs = [d for d in docs if d["id"] in _vis_doc]

    # ── 实体向量：doc = 实体名 + 别名 + 主表 + 注释 ──
    ent_items = []
    for e in entities:
        kw = " ".join(e.get("keywords") or [])
        doc = f"{e['name']}（{kw}）：主表 {e.get('table_name') or '?'}，{e.get('grain') or ''}。{e.get('comment') or ''}"
        ent_items.append({
            "name": e["name"],
            "keywords": [e["name"]] + (e.get("keywords") or []),
            "doc": doc,
            "raw": e,
        })
    ent_vectors = list(model.embed([it["doc"] for it in ent_items])) if ent_items else []

    # ── 文档向量：doc = 标题 + 正文前 200 字 + 关键词 ──
    doc_items = []
    for d in docs:
        kw = " ".join(d.get("keywords") or [])
        body = (d.get("content") or "")[:400]
        doc_items.append({
            "name": d.get("title") or f"doc#{d.get('id')}",
            "keywords": (d.get("keywords") or []),
            "doc": f"{d.get('title') or ''} {kw} {body}",
            "raw": d,
        })
    doc_vectors = list(model.embed([it["doc"] for it in doc_items])) if doc_items else []

    index["entities"] = ent_items
    index["entity_vectors"] = ent_vectors
    index["relationships"] = relationships
    index["docs"] = doc_items
    index["doc_vectors"] = doc_vectors
    return index


# ═══════════════════════════════════════════════════════════
# ② SemanticContext —— 一次检索汇聚出的分层知识
# ═══════════════════════════════════════════════════════════
def entity_table_name(entity):
    """统一从实体对象取「主表名」——只允许在这里定义"拆几层"。

    ★ 为什么需要这个函数？
      实体在链路里有两种形态：
        ① 裸实体：knowledge.get_entities() / ctx.p2_entities —— table_name 在顶层
        ② 索引包装项：index["entities"] —— table_name 在 raw 下
      以前两处各写各的，出现了「对裸实体又取一次 raw」的 bug（司机表因此丢失）。
      统一入口后，调用方不需要知道当前是哪一种形态。

    返回表名（str）或 None；不抛异常，便于在检索链路里做防御性判断。
    """
    if not isinstance(entity, dict):
        return None
    return entity.get("table_name") or (entity.get("raw") or {}).get("table_name")


# ── 问题类型 vs 指标类型：避免问“比例”却召回“金额”指标 ──
_RATIO_HINTS = ("比例", "占比", "百分比", "系数", "配置")
_AMOUNT_HINTS = ("金额", "收入", "流水", "成本", "利润", "gmv", "客单价", "产值")
_COUNT_HINTS = ("次数", "数量", "单量", "人数", "个数", "笔数", "总数", "总量")


def question_measure_kinds(question):
    """判断问题在问哪类度量：ratio / amount / count（可多选）。

    ★ 为什么“费”不直接算金额？
      「信息费比例」的主语是比例，不是金额；只按关键词“费”会把比例问题误判成金额问题，
      导致后面把金额指标留下、比例指标丢掉。
    """
    q = (question or "").lower()
    kinds = set()
    if any(k in q for k in _RATIO_HINTS):
        kinds.add("ratio")
    if any(k in q for k in _COUNT_HINTS):
        kinds.add("count")
    # “率”单独判断：排除“效率/概率”这类非比率词
    if "率" in q and "效率" not in q and "概率" not in q:
        kinds.add("ratio")
    # 只有【没有】比例/比率信号时，才把金额词当作金额问题
    if any(k in q for k in _AMOUNT_HINTS) and "ratio" not in kinds:
        kinds.add("amount")
    return kinds


def metric_measure_kind(metric):
    """判断指标属于哪类度量：ratio / amount / count / unknown。"""
    mt = (metric.get("metric_type") or "").lower()
    if mt in ("ratio", "rate"):
        return "ratio"
    if mt == "amount":
        return "amount"
    if mt == "count":
        return "count"
    name = metric.get("name") or ""
    if any(k in name for k in ("率", "占比", "比例")):
        return "ratio"
    if any(k in name for k in _COUNT_HINTS):
        return "count"
    if any(k in name for k in ("金额", "费", "收入", "流水", "成本", "利润", "gmv", "客单价")):
        return "amount"
    return "unknown"


def filter_metrics_for_question(question, metrics):
    """按问题度量类型过滤明显不相关的指标，返回 (保留, 被丢弃名称)。

    ★ 保守策略：
      ① 用户点名了某个指标名 → 永远保留（用户意图优先）；
      ② 问比例时，只丢“金额类”指标；计数指标保留（比率常需要计数做分母）；
      ③ 问金额时，只丢“纯比率”指标；
      ④ 问题没有明显度量信号 → 不过滤。
    """
    kinds = question_measure_kinds(question)
    if not kinds:
        return list(metrics), []
    kept, dropped = [], []
    for m in metrics:
        name = m.get("name") or ""
        if name and name in (question or ""):
            kept.append(m)
            continue
        kind = metric_measure_kind(m)
        if "ratio" in kinds and kind == "amount":
            dropped.append(name)
            continue
        if "amount" in kinds and "ratio" not in kinds and kind == "ratio":
            dropped.append(name)
            continue
        # 问“总数/数量/次数”时，金额指标不参与
        if ("count" in kinds and "amount" not in kinds and "ratio" not in kinds
                and kind == "amount"):
            dropped.append(name)
            continue
        kept.append(m)
    return kept, dropped


def filter_metrics_by_entity(metrics, entity_tables, question):
    """已识别实体时，过滤与实体业务线无关的金额/计数指标。

    ★ 规则：
      ① 用户点名 / 没有依赖表的指标 → 保留；
      ② 通用比率（完单率）→ 保留（可能跨表算分子分母）；
      ③ 业务线专属比率（大板完单率/代驾完单率）→ 看表是否同线；
      ④ 其余金额/计数 → 依赖表必须与实体主表有交集，否则丢弃。
    """
    if not entity_tables:
        return list(metrics), []
    kept, dropped = [], []
    for m in metrics:
        mtabs = set(m.get("tables") or [])
        name = m.get("name") or ""
        kind = metric_measure_kind(m)
        named = bool(name and name in (question or ""))
        line_specific_ratio = (kind == "ratio" and
                               any(p in name for p in ("大板", "代驾", "小板")))
        if named or not mtabs:
            kept.append(m)
        elif kind == "ratio" and not line_specific_ratio:
            kept.append(m)
        elif mtabs & set(entity_tables):
            kept.append(m)
        else:
            dropped.append(name)
    return kept, dropped


class SemanticContext:
    def __init__(self):
        self.p0_rules = ""                       # P0 系统规则文本
        self.p1_metrics = []                     # P1 命中指标（完整记录）
        self.p2_entities = []                    # P2 命中实体
        self.p2_relationships = []               # P2 命中关联/Join 规则
        self.p3_tables = []                      # P3 命中表名
        self.p3_table_objs = []                  # P3 命中表对象（含 columns）
        self.p4_docs = []                        # P4 命中文档
        self.conflicts = []                      # 冲突记录 [(tier, key, a, b)]
        self.route_info = ""                     # 路由说明（日志用）

    def all_table_names(self):
        names = list(self.p3_tables)
        for m in self.p1_metrics:
            for t in (m.get("tables") or []):
                if t not in names:
                    names.append(t)
        for e in self.p2_entities:
            t = entity_table_name(e)
            if t and t not in names:
                names.append(t)
        return names


# ═══════════════════════════════════════════════════════════
# ③ 多路检索（Multi-path Retrieval）
#    对每一层分别做 关键词精确匹配 + 向量检索 + 元数据过滤
# ═══════════════════════════════════════════════════════════
def retrieve_semantic(index, question, understanding=None):
    """
    按优先级路由检索，产出 SemanticContext。

    参数：
        index        = enrich_index(rag.build_index()) 的结果
        question     = 用户原始问题
        understanding = query_understanding 的结构化结果（可选；有则精准路由）
    """
    model = index.get("model")
    ctx = SemanticContext()

    # ── P0：系统规则（始终全量注入，不可被覆盖）──
    try:
        ctx.p0_rules = config_store.get_prompt("sql_rules")
    except Exception:
        ctx.p0_rules = ""

    # ★ 提前建立「实体名 → 主表」映射，供 P1 指标按实体过滤：
    #   避免问「小板订单」却向量召回「代驾完单量(status=40)」「大板完单量(state=6)」等
    #   跨业务线指标，导致口径冲突（P1 校验/逻辑自审把 status=6 与 status=40 混在一起误报）。
    ent_pool = index.get("entities") or []
    ent_by_name = {e.get("name"): e for e in ent_pool}
    wanted_tables_early = set()
    if understanding:
        for en in (understanding.get("entities") or []):
            e = ent_by_name.get(en)
            tbl = entity_table_name(e)
            if tbl:
                wanted_tables_early.add(tbl)

    # ── P1：指标定义（权威）──
    #   路由策略：understanding 命中的指标名 → 直接取权威定义（不走阈值门槛）；
    #   再补一次向量召回（阈值门），防止漏。
    metric_pool = {m["name"]: m for m in (index.get("metrics") or [])}
    hit_metric_names = set()
    if understanding:
        for mn in (understanding.get("metrics") or []):
            if mn in metric_pool:
                hit_metric_names.add(mn)
    # 向量兜底
    if index.get("metrics") and index.get("metric_vectors"):
        mv, _, _ = rag._hybrid_retrieve(
            question, index["metrics"], index["metric_vectors"],
            model, config.METRIC_THRESHOLD, config.METRIC_TOP_K,
        )
        hit_metric_names.update(mv)
    # ★ 按实体过滤：只保留依赖 wanted_tables 的指标（无 tables 的通用指标保留）
    if wanted_tables_early:
        hit_metric_names = {
            n for n in hit_metric_names
            if n in metric_pool and (
                not set(metric_pool[n].get("tables") or []) or
                set(metric_pool[n].get("tables") or []) & wanted_tables_early
            )
        }
    ctx.p1_metrics = [metric_pool[n] for n in hit_metric_names if n in metric_pool]

    # ★ S4：按问题类型降噪（问比例时不要把金额指标及其费用表硬塞进来）
    #   只丢“明显不兼容”的指标；用户点名的指标、计数指标都保留。
    ctx.p1_metrics, _metric_dropped = filter_metrics_for_question(question, ctx.p1_metrics)

    # ── P2：实体 / 关系 ──
    #   实体：由 understanding.entities + 向量召回 → entities.table_name
    #   （ent_pool / ent_by_name 已在 P1 前定义，此处复用）
    rel_pool = index.get("relationships") or []

    # 分离「实体名」与「表名」两个集合，关系匹配用精确集合判定（替代表名子串匹配）
    wanted_entities = set()
    wanted_tables = set(ctx.all_table_names())          # P1 指标依赖的表
    if understanding:
        wanted_entities.update(understanding.get("entities") or [])

    # 实体向量召回
    if ent_pool and index.get("entity_vectors"):
        ents, _, _ = rag._hybrid_retrieve(
            question, ent_pool, index["entity_vectors"],
            model, config.TABLE_THRESHOLD, config.TABLE_TOP_K,
        )
        for n in ents:
            if n in ent_by_name:
                ctx.p2_entities.append(ent_by_name[n]["raw"])
                wanted_entities.add(n)

    # 把实体名对应的主表并入 wanted_tables（供关系匹配 + P3 合并）
    #   ★ ent_by_name 存的是 ent_item（table_name 在其 raw 里）
    entity_tables = set()
    for ename in wanted_entities:
        e = ent_by_name.get(ename)
        tbl = entity_table_name(e)
        if tbl:
            wanted_tables.add(tbl)
            entity_tables.add(tbl)

    # ★ S4：已识别实体时，金额/计数指标必须与实体主表有交集，否则丢弃。
    #   例：问“大板信息费总额”，不该再带“代驾信息费 / 司机补缴信息费次数”；
    #       问“司机信息费比例”，不该带“司机补缴信息费次数”（它依赖线下单/支付流水）。
    if entity_tables:
        ctx.p1_metrics, _drop2 = filter_metrics_by_entity(
            ctx.p1_metrics, entity_tables, question)
        _metric_dropped.extend(_drop2)

    # 关系：按「实体名 / 主表」精确匹配。
    #   ★ 原实现用 `t in join_sql` 子串匹配，dwd_tu_order_all 会误命中
    #     ods_tu_order_pricing_record_all 等含同名字串的表；改为精确集合匹配。
    rel_hit = []
    for rel in rel_pool:
        fe = rel.get("from_entity") or ""
        te = rel.get("to_entity") or ""
        fe_tbl = (ent_by_name.get(fe) or {}).get("table_name") or fe
        te_tbl = (ent_by_name.get(te) or {}).get("table_name") or te
        if (fe in wanted_entities or te in wanted_entities or
                fe_tbl in wanted_tables or te_tbl in wanted_tables):
            rel_hit.append(rel)
            # ★ 关系命中 → 把对端表纳入 wanted_tables。
            #   否则 P3 实体过滤会把关系对端表当「跨业务线表」丢掉：模型看得到
            #   JOIN 关系（p2_relationships），却看不到对端表的字段结构（表被过滤、
            #   schema 没注入），只能退而求其次用错表。
            #   实测「来源渠道为APP的客户」：用户注册明细表(ods_user_register_detail_all)
            #   与客户表有 1:N 关系、关系也命中了，但因登记了独立实体「用户注册明细」，
            #   被实体过滤误杀 → 模型拿订单表 channel_source 顶替，把「注册渠道」算成「发单渠道」。
            wanted_tables.add(fe_tbl)
            wanted_tables.add(te_tbl)
    # 去重（按 id）
    seen = set()
    for rel in rel_hit:
        if rel.get("id") not in seen:
            seen.add(rel.get("id"))
            ctx.p2_relationships.append(rel)

    # ── P3：表 Schema（复用 rag 的混合检索）──
    if index.get("tables") and index.get("table_vectors"):
        tnames, tscores, treason = rag._hybrid_retrieve(
            question, index["tables"], index["table_vectors"],
            model, config.TABLE_THRESHOLD, config.TABLE_TOP_K,
        )
        # ★ 用户【明确点名】的表：直接纳入候选，并豁免下面的实体过滤。
        #
        #   【为什么需要】向量分数在有些场景分不开"该留的"和"噪声"：
        #     实测「查询8月份资金流水表中…」→ 该留的资金流水表 0.5686，
        #     纯噪声的大板统计表 0.5449，只差 0.024 —— 任何阈值都切不开。
        #     而"用户亲手写出的词"是比分数强得多的证据：
        #     「资金流水」与「ods_tu_record_all：资金流水记录明细」有 4 字连续重合。
        #   【为什么要在过滤之前】点名的表可能压根没进 top_k（如类型配置表排名第 9），
        #     不先并进候选就永远轮不到它。
        named, named_why = rag.named_tables(question, index["tables"])
        named_set = set(named)
        for t in named:
            if t not in tnames:
                tnames.append(t)

        # ★ S4：字段关键词命中的表，等同“用户点名”，同样豁免实体过滤。
        #   场景：用户只写字段名（“信息费默认比例”），没写“司机”；
        #   P2 实体可能误判成别的业务线，若不放行，司机表会被当成跨业务线删掉。
        field_named = rag.field_keyword_tables(question, index["tables"])
        for t in field_named:
            if t not in named_set:
                named_set.add(t)
                named_why[t] = f"字段关键词「{t}」"
            if t not in tnames:
                tnames.append(t)

        # ★ 按实体过滤：识别出实体主表后，把【明确属于别的业务线】的表摘掉。
        #
        #   【原来的写法有什么问题？】
        #     原来是"白名单"：只保留 `wanted_tables`（实体主表 ∪ 指标依赖表），
        #     其余一律当跨业务线丢弃。但一个业务实体往往带【卫星表】——
        #     代驾订单 → 代驾费用表、代驾司机激活费表、询价记录表……
        #     它们不属于任何已注册实体（实体表只登记"主表"），于是被误杀。
        #     线上真实反馈：问「代驾司机缴纳激活费」，激活费表被当成跨业务线丢掉
        #     → 模型拿不到表 → 直接"表结构不足以计算"拒答。
        #
        #   【新规则】只掉"能被证明属于别的业务线"的表，判不出来就别乱掉：
        #     ① 在 wanted_tables 里                          → 留
        #     ② 归属实体明确，且不属于本次命中的业务线        → 丢（这才是真的跨业务线）
        #     ③ 查不到归属的表（卫星表/中间表）              → 只有相似度够高才留
        #        （阈值见 config.SATELLITE_KEEP_THRESHOLD 的注释，附实测依据）
        if wanted_tables:
            # 表 → 归属实体：用实体注册表的"主表"反查
            tbl_owner = {}
            for en, e in ent_by_name.items():
                t = entity_table_name(e)
                if t:
                    tbl_owner.setdefault(t, en)
            # 本次要守住的业务线 = 命中实体 + wanted_tables 的归属实体
            session_lines = set(wanted_entities)
            session_lines |= {tbl_owner[t] for t in wanted_tables if t in tbl_owner}

            score_of = dict(tscores or [])
            kept, dropped, rescued = [], [], []
            for t in tnames:
                if t in wanted_tables:
                    kept.append(t)
                    continue
                if t in named_set:             # ★ 用户点名 → 不受实体过滤影响
                    kept.append(t)
                    continue
                owner = tbl_owner.get(t)
                if owner and owner not in session_lines:
                    dropped.append(t)          # 明确属于别的业务线
                elif score_of.get(t, 0) >= config.SATELLITE_KEEP_THRESHOLD:
                    kept.append(t)             # 无归属但分很高 → 疑似本业务线的卫星表
                    if not owner:
                        rescued.append(t)
                else:
                    dropped.append(t)
            tnames = kept
            if rescued:
                treason = (f"{treason}；保留疑似本业务线卫星表 "
                           f"{', '.join(rescued)}（相似度 ≥ {config.SATELLITE_KEEP_THRESHOLD}）")
            # 点名的表单独说明：审核时要能一眼看出"这张表是因为用户点名才进来的"
            named_kept = [t for t in named if t in kept]
            if named_kept:
                treason = (f"{treason}；用户点名，豁免实体过滤 "
                           + "、".join(f"{t}（命中「{named_why[t]}」）" for t in named_kept))
            if dropped:
                treason = f"{treason}；已按实体过滤掉跨业务线表 {', '.join(dropped)}"
        # 合并 P1/P2 带出的表（指标依赖表可能向量分不够高）
        for t in ctx.all_table_names():
            if t not in tnames:
                tnames.append(t)
        ctx.p3_tables = tnames
        ctx.p3_table_objs = [t for t in index["tables"] if t["name"] in tnames]
        ctx.route_info = treason

    # ── P4：业务文档（向量补充，高阈值，绝不覆盖上层）──
    if index.get("docs") and index.get("doc_vectors"):
        doc_by_name = {it["name"]: it for it in index["docs"]}
        docs, _, _ = rag._hybrid_retrieve(
            question, index["docs"], index["doc_vectors"],
            model, max(config.DICT_THRESHOLD, 0.5), config.DICT_TOP_K,
        )
        ctx.p4_docs = [doc_by_name[n]["raw"] for n in docs if n in doc_by_name]

    # 把“指标降噪”写进路由说明，审核时能看出为什么少召回了一个指标
    if _metric_dropped:
        note = "按问题类型过滤无关指标：" + "、".join(_metric_dropped)
        ctx.route_info = f"{ctx.route_info}；{note}" if ctx.route_info else note

    return ctx


# ═══════════════════════════════════════════════════════════
# ④ 冲突解决（Conflict Resolution）
#   规则：高优先级胜出；同层冲突按 "manual > ai"、最近更新决胜（此处记录冲突日志）。
# ═══════════════════════════════════════════════════════════
def resolve(ctx):
    """
    把 SemanticContext 汇编成「注入 Prompt 的分层文本」+ 冲突日志。
    返回 dict：{p0,p1,p2,p3,p4, conflicts, bindings}
      bindings: {conflict_key: (value, tier)} —— 供 SQL Validator 比对硬约束
    """
    conflicts = list(ctx.conflicts)

    # ── 同名指标冲突检测（P1 内部）──
    seen_metric = {}
    for m in ctx.p1_metrics:
        ck = m.get("conflict_key") or m["name"]
        if ck in seen_metric and seen_metric[ck] != m["name"]:
            conflicts.append((P1, ck, seen_metric[ck], m["name"]))
        seen_metric[ck] = m["name"]

    p0 = ctx.p0_rules

    # P1：权威指标定义（标注 MUST）
    p1_lines = []
    bindings = {}
    for m in ctx.p1_metrics:
        p1_lines.append(f"- 【MUST】{m['name']} = {m['definition']}")
        bindings[m["name"]] = (m["definition"], P1)
    p1 = ("本次涉及的【权威指标定义】（必须严格按此口径计算，不得用字段名自行猜测）：\n"
          + "\n".join(p1_lines)) if p1_lines else ""

    # P2：实体 + Join 规则（标注 MUST for join）
    p2_lines = []
    for e in ctx.p2_entities:
        p2_lines.append(f"- 实体「{e.get('name')}」主表={e.get('table_name')}，粒度={e.get('grain')}：{e.get('comment')}")
    for rel in ctx.p2_relationships:
        tag = "【JOIN 铁律】" if rel.get("pre_agg_required") else "【JOIN】"
        p2_lines.append(f"{tag} {rel.get('join_sql')}  ({rel.get('cardinality')})"
                        + (f" —— {rel.get('rule_note')}" if rel.get('rule_note') else ""))
        # 一对多的 Join 规则作为硬约束
        if rel.get("pre_agg_required"):
            bindings[f"join:{rel.get('from_entity')}->{rel.get('to_entity')}"] = \
                (rel.get("join_sql"), P2)
    p2 = ("本次涉及的【实体与关联规则】：\n" + "\n".join(p2_lines)) if p2_lines else ""

    # P3：表结构（仅本次命中表）
    p3_lines = []
    for t in ctx.p3_table_objs:
        p3_lines.append(f"表 {t['name']}:\n{t['columns']}")
    p3 = ("# 可用的表结构（只提供与本次问题相关的表）：\n" + "\n\n".join(p3_lines)) if p3_lines else ""

    # P4：文档（仅补充）
    p4_lines = []
    for d in ctx.p4_docs:
        p4_lines.append(f"# 参考文档《{d.get('title')}》\n{d.get('content')}")
    p4 = ("\n\n".join(p4_lines)) if p4_lines else ""

    return {
        "p0": p0, "p1": p1, "p2": p2, "p3": p3, "p4": p4,
        "conflicts": conflicts, "bindings": bindings,
        "tables": ctx.p3_tables, "metrics": [m["name"] for m in ctx.p1_metrics],
        # ★ 指标类型映射（供 Result Validator 判定比率列等），消费已建的 metric_type 列
        "metric_types": {m["name"]: m.get("metric_type") for m in ctx.p1_metrics},
    }


def build_prompt_parts(resolved, role_text, time_context=""):
    """
    把分层结果拼成 System Prompt 各段（顺序：role → 时间口径 → P0→P1→P2→P3→P4）。
    RAG 补回的 P3/P4 只在 P0~P2 之后出现，结构上保证「不覆盖」。

    time_context：由 calendar_util 生成的「权威时间口径」（今天几号 + 时间表达
                  解析出的公历区间）。紧跟 role 注入，保证模型在写 SQL 前就拿到
                  明确日期，而不是对着「中秋节前7天」瞎猜或直接拒答。
    """
    parts = [role_text]
    if time_context:
        parts.append(time_context)
    if resolved["p0"]:
        parts.append("# 系统规则（强制，不可违背）\n" + resolved["p0"])
    if resolved["p1"]:
        parts.append("# 指标定义（权威口径，必须严格遵循）\n" + resolved["p1"])
    if resolved["p2"]:
        parts.append("# 实体与关联规则（Join 硬约束）\n" + resolved["p2"])
    if resolved["p3"]:
        parts.append(resolved["p3"])
    if resolved["p4"]:
        parts.append("# 补充业务文档（仅供参考，不得覆盖上述规则/口径）\n" + resolved["p4"])
    return "\n\n".join(parts)
