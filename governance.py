# -*- coding: utf-8 -*-
"""governance.py —— 知识治理状态过滤（生产检索准入的统一出口）。

哪些知识能进"生产检索 / 语义解析"：
  · 一律排除：draft / reviewing / deprecated / rejected / archived（永不可作为生产事实）。
  · candidate（AI 生成、未复核）：严格模式下排除，非严格模式保留。
  · 缺失 status（如 kb_docs 等未纳入治理的表）：按 approved 兼容处理。

★ 当前默认：严格模式（GOVERNANCE_STRICT_STATUS 默认 True）
  2026-10-27 已完成一轮全量复核（candidate 145 → approved 142 / rejected 3），
  六层知识无 candidate 残留，开启严格模式无实质召回损失。
  此后新增的 AI 知识一律先落 candidate，须经 `_review_governance.py apply`
  复核才生效 —— 这是《数据治理方案》要求的合规态。
  过渡期如需放宽，在 .env 写 GOVERNANCE_STRICT_STATUS=false。
"""
import config


def strict():
    """当前是否严格模式（动态读取 config，改环境变量后无需重启模块）。"""
    return bool(getattr(config, "GOVERNANCE_STRICT_STATUS", False))


def visible_status(status):
    """给定 status，判断该对象能否进入生产检索。"""
    s = (status or "").strip().lower()
    if s in ("", "approved"):
        return True
    if s == "candidate":
        return not strict()
    return False        # draft/reviewing/deprecated/rejected/archived


def visible_source(source):
    """按来源判断注释能否进生产（ai 等价 candidate）。"""
    s = (source or "manual").strip().lower()
    return not (s == "ai" and strict())
