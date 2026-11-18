# -*- coding: utf-8 -*-
"""
business_domain_service.py —— 业务域识别 + 语义对象注入（Agent 使用标准语义知识）

★ 定位（第六步核心）：让 Agent 处理问题时「先识别业务域/主题 → 获取审核通过的
  语义对象（Dataset/Field/Metric/Dimension/Relationship/AI Context）→ 注入 Prompt」。
  业务域不只是一个 RAG 标签，而是【选择语义模型 + 限定知识范围】的依据。

★ 与 semantic_object_builder 的关系：
  semantic_object_builder 负责「从知识表动态组装 OSSIE 对象」（数据来源）；
  本模块负责「识别业务域/主题 + 把语义对象渲染成 Prompt 约束段」（编排）。
  二者不重复存储、不重复建设 —— 语义对象的正文仍在 7 张知识表，动态派生。

★ 业务域识别（规则映射，先规则后 LLM）：
  从本次命中的实体名/指标名 → 反查 topic_assets → 归属主题 → 主题 → 域。
  命中多个域时允许组合多个已发布语义模型，但只在同一主题内组装（跨域由上层校验）。
"""
import config
import config_store
import semantic_object_builder


def _enabled():
    """当前是否启用语义接入（复用 SEMANTIC_MODEL_ENABLED 开关，一键回滚）。"""
    return bool(getattr(config, "SEMANTIC_MODEL_ENABLED", False))


def match_subjects(ctx, understanding):
    """从本次命中的实体/指标反查业务主题（返回 [(subject_name, domain_name)]）。

    ★ 反查逻辑：实体名/指标名 → topic_assets(scope=subject) → 主题 → 业务域。
      未命中返回空列表（此时走原有 P0~P4 检索，零影响）。
    """
    if not _enabled():
        return []
    asset_keys = set()
    for e in (getattr(ctx, "p2_entities", None) or []):
        if isinstance(e, dict) and e.get("name"):
            asset_keys.add(e["name"])
    for en in ((understanding or {}).get("entities") or []):
        if en:
            asset_keys.add(en)
    for m in (getattr(ctx, "p1_metrics", None) or []):
        if isinstance(m, dict) and m.get("name"):
            asset_keys.add(m["name"])
    for mn in ((understanding or {}).get("metrics") or []):
        if mn:
            asset_keys.add(mn)
    if not asset_keys:
        return []

    try:
        subject_of = config_store.get_subjects_by_assets(asset_keys)
    except Exception:
        return []
    subject_names = set()
    for names in subject_of.values():
        subject_names.update(names)
    if not subject_names:
        return []

    try:
        domain_map = config_store.get_subject_domain_map(subject_names)
    except Exception:
        domain_map = {}
    # 按主题出现次数排序（命中资产越多的主题越优先），限制注入数量
    counts = {}
    for names in subject_of.values():
        for sn in names:
            counts[sn] = counts.get(sn, 0) + 1
    ranked = sorted(subject_names, key=lambda s: -counts.get(s, 0))
    max_n = int(getattr(config, "SEMANTIC_MODEL_MAX", 2) or 2)
    return [(sn, domain_map.get(sn, "")) for sn in ranked[:max_n]]


def build_context_block(sidx, question, understanding, ctx):
    """把匹配到的业务域语义对象渲染成 Prompt 约束段。

    返回空串表示未命中（或开关关闭）——调用方无需改动，行为不变。
    ★ 只注入【本次命中的实体/指标 + 相关关系】，不注入整个主题（避免 Prompt 过长）。
    """
    subjects = match_subjects(ctx, understanding)
    if not subjects:
        return ""
    # 本次命中的实体名 / 指标名
    hit_entities = set()
    for e in (getattr(ctx, "p2_entities", None) or []):
        if isinstance(e, dict) and e.get("name"):
            hit_entities.add(e["name"])
    for en in ((understanding or {}).get("entities") or []):
        if en:
            hit_entities.add(en)
    hit_metrics = set()
    for m in (getattr(ctx, "p1_metrics", None) or []):
        if isinstance(m, dict) and m.get("name"):
            hit_metrics.add(m["name"])
    for mn in ((understanding or {}).get("metrics") or []):
        if mn:
            hit_metrics.add(mn)

    blocks = []
    for subject_name, domain_name in subjects:
        objs = semantic_object_builder.get_semantic_objects(subject_name)
        block = _render_subject(subject_name, domain_name, objs, hit_entities, hit_metrics)
        if block:
            blocks.append(block)
    if not blocks:
        return ""
    header = ("# 【业务域语义约束】（以下为人工审核通过的结构化业务语义，"
              "优先级高于经验参考；若与向量检索到的零散知识冲突，以本段为准）")
    return header + "\n\n" + "\n\n".join(blocks)


def _render_subject(subject_name, domain_name, objs, hit_entities, hit_metrics):
    """渲染单个业务主题的语义对象（只渲染本次命中的实体/指标/关系）。"""
    lines = [f"## 业务主题「{subject_name}」" + (f"（业务域 {domain_name}）" if domain_name else "")]
    # 只保留命中的 dataset（无命中集合时全量）
    datasets = objs.get("datasets") or []
    if hit_entities:
        datasets = [d for d in datasets if d.get("name") in hit_entities]
    if datasets:
        lines.append("### 数据集（Dataset）")
        for d in datasets:
            lines.append(f"- {d.get('name')}（物理表 {d.get('source')}）")
            time_f = [f["name"] for f in (d.get("fields") or [])
                      if (f.get("dimension") or {}).get("is_time")]
            if time_f:
                lines.append(f"  时间维度：{', '.join(time_f)}")
    # 只保留命中的指标
    metrics = objs.get("metrics") or []
    if hit_metrics:
        metrics = [m for m in metrics if m.get("name") in hit_metrics]
    if metrics:
        lines.append("### 指标（Metric，必须严格按此口径）")
        for m in metrics:
            expr = (m.get("expression") or {}).get("sql")
            if expr:
                lines.append(f"- 【MUST】{m.get('name')} = {expr}")
            elif m.get("description"):
                lines.append(f"- {m.get('name')}：{m['description']}")
    # 关系：端点命中实体才注入
    rels = objs.get("relationships") or []
    if rels and hit_entities:
        rels = [r for r in rels if r.get("from") in hit_entities or r.get("to") in hit_entities]
    if rels:
        lines.append("### 关系（Relationship）")
        for r in rels:
            lines.append(f"- {r.get('from')} → {r.get('to')}")
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)
