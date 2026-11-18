# -*- coding: utf-8 -*-
"""
_seed_business_domains.py —— 业务域初始化种子（幂等，从现有知识资产推断）

★ 用途（业务域体系第一步）：按现有实体/指标的名称与主表，推断并初始化业务域
  （订单/司机/财务/营销/救援），每域建 1 个默认业务主题，并把相关实体/指标关联到主题。
  不编造数据 —— 业务域归属完全由现有实体名/表名关键词推导。

★ 幂等：已存在的业务域/主题/资产关联不覆盖、不重复。

★ 运行：.venv/bin/python _seed_business_domains.py
  之后：到「业务域」页查看/调整，或直接调用 /api/domains 等接口。

★ 关键词 → 业务域映射（可扩展）：
  订单域 order     ：订单/完单/发单/接单/询价
  司机域 driver    ：司机/driver
  财务域 finance   ：保证金/费用/财务/流水/积分/deposit/fee/pay/point
  营销域 marketing ：商城/客户/注册/激活/车贴/mall/customer/register
  救援域 rescue    ：保障/担保/救援/guaranty/rescue
"""
import config_store

# 业务域定义：编码 → (名称, 描述, 默认主题名, 关键词列表)
DOMAINS = {
    "order": {
        "display_name": "订单", "description": "订单全生命周期业务域",
        "subject": "order_operation",
        "keywords": ("订单", "完单", "发单", "接单", "询价"),
    },
    "driver": {
        "display_name": "司机", "description": "司机管理与运营业务域",
        "subject": "driver_operation",
        "keywords": ("司机", "driver"),
    },
    "finance": {
        "display_name": "财务", "description": "资金、费用、流水与积分业务域",
        "subject": "finance",
        "keywords": ("保证金", "费用", "财务", "流水", "积分", "deposit", "fee", "pay", "point"),
    },
    "marketing": {
        "display_name": "营销", "description": "客户、商城与营销活动业务域",
        "subject": "marketing",
        "keywords": ("商城", "客户", "注册", "激活", "车贴", "mall", "customer", "register"),
    },
    "rescue": {
        "display_name": "救援", "description": "保障、担保与救援业务域",
        "subject": "rescue",
        "keywords": ("保障", "担保", "救援", "guaranty", "rescue"),
    },
}


def _match_domain(text):
    """按关键词匹配业务域（返回编码或 None）。"""
    t = (text or "").lower()
    for code, cfg in DOMAINS.items():
        for kw in cfg["keywords"]:
            if kw.lower() in t:
                return code
    return None


def main():
    # ① 建业务域 + 默认主题（幂等）
    for code, cfg in DOMAINS.items():
        config_store.upsert_business_domain(
            code, cfg["display_name"], cfg["description"], "", "", True)
        config_store.upsert_business_subject(
            cfg["subject"], code, cfg["display_name"] + "经营", cfg["description"], True)
    print(f"已初始化 {len(DOMAINS)} 个业务域 + 默认主题")

    # ② 实体 → 业务域归类
    domain_subject = {code: cfg["subject"] for code, cfg in DOMAINS.items()}
    entity_count = 0
    for e in config_store.get_entities():
        if not e.get("enabled", True):
            continue
        text = f"{e.get('name')} {e.get('table_name')}"
        code = _match_domain(text)
        if code:
            config_store.add_scope_asset(domain_subject[code], "entity", e.get("name"), scope="subject")
            entity_count += 1
    print(f"关联实体 {entity_count} 个")

    # ③ 指标 → 业务域归类（按依赖表名 + 指标名）
    entity_table_map = {e.get("table_name"): e.get("name")
                        for e in config_store.get_entities() if e.get("enabled", True)}
    entity_domain = {}
    for e in config_store.get_entities():
        if not e.get("enabled", True):
            continue
        code = _match_domain(f"{e.get('name')} {e.get('table_name')}")
        if code:
            entity_domain[e.get("name")] = code
    metric_count = 0
    for m in config_store.get_metrics(enabled_only=False):
        code = _match_domain(f"{m.get('name')} {' '.join(m.get('tables') or [])}")
        if code:
            config_store.add_scope_asset(domain_subject[code], "metric", m.get("name"), scope="subject")
            metric_count += 1
    print(f"关联指标 {metric_count} 个")

    # ④ 汇总
    print("\n=== 业务域初始化结果 ===")
    for d in config_store.list_business_domains():
        subj = d.get("subjects") or []
        assets = 0
        for s in subj:
            assets += len(config_store.list_scope_assets(s, scope="subject"))
        print(f"  {d['name']}（{d['display_name']}）→ 主题 {subj}，资产 {assets} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
