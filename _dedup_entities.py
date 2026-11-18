# -*- coding: utf-8 -*-
"""
_dedup_entities.py —— 业务实体别名冗余清理（幂等、可重复执行）。

★ 背景（企业业务语义知识库建设 · 任务 3）：
  entities 表 52 个实体里，有 9 组「同一主表登记了多个实体名」，属于别名冗余。
  冗余会让 Semantic Router 的实体识别与向量检索出现「同名不同实体」的噪音。

★ 合并规则（保守、可逆、可审计）：
  1. 只自动合并【无争议的后缀/简称冗余】（「代驾订单」vs「代驾订单表」、
     「客户积分批次」vs「客户积分批次表」等），保留一个规范名，其余【停用】
     （enabled=0，不删除，可恢复），并把停用名并入保留实体的 keywords 保留检索能力。
  2. 规范名选择：status=approved 优先；被 relationships 引用的优先；无冗余后缀优先。
  3. 语义明显不同的冗余（「担保」vs「保障」、「审核」vs「违规」等）【不自动合并】，
     仅在报告里列出，交人工确认 —— 机器不该替业务判断同名实体的语义边界。
  4. 停用实体若被 relationships 引用则跳过（避免 JOIN 铁律断链）。
  5. 全程走 config_store.upsert_entity，自动写 knowledge_history 审计。

★ 为什么停用（enabled=0）而不是删除？
  停用可恢复、可回溯；删除会让 knowledge_history 失去实体锚点，且 relationships
  若还引用它就会断链。enabled=0 的实体不进向量索引（semantic_layer.enrich_index 已过滤）。

运行：python3 _dedup_entities.py
"""
import config_store


# ── 安全自动合并组：保留名 -> 停用名列表（无争议的后缀/简称冗余）──
DEDUP = {
    "代驾订单": ["代驾订单表"],                          # 被 relationships id=1 引用
    "用户登录活跃": ["用户登录日活表"],
    "资金收支流水记录": ["流水记录"],                    # 被 relationships id=19 引用
    "客户积分批次": ["客户积分批次表"],
    "客户积分流水": ["客户积分完单记录表", "客户积分流水/完单记录表"],
}

# ── 需人工确认组（语义不同/命名完整度差异，不自动合并）──
REVIEW = [
    ("保障订单", "车辆运输保障订单", "简称 vs 全称，选哪个保留取决于业务习惯"),
    ("保障订单方案", "担保订单方案", "guaranty 的「保障/担保」译法不一致，需统一口径"),
    ("转卖单审核表", "转卖单违规表", "「审核」vs「违规」是同一表的不同用途视角，语义可能不同"),
    ("线下单司机定位统计表", "线下单位置统计表 / 线下接单违规记录",
     "同表三个不同视角（定位统计/位置统计/接单违规），需业务确认是否应拆成独立实体"),
]


def main():
    config_store.init_store()
    entities = {e["name"]: e for e in config_store.get_entities()}

    # relationships 引用的实体名（断链安全检查）
    referenced = set()
    for r in config_store.get_relationships(enabled_only=False):
        referenced.add((r.get("from_entity") or "").strip())
        referenced.add((r.get("to_entity") or "").strip())

    disabled = 0
    aliased = 0
    skipped_link = []

    for keep_name, dup_names in DEDUP.items():
        keep = entities.get(keep_name)
        if not keep:
            print("⚠ 保留实体「%s」不存在，跳过" % keep_name)
            continue

        # ① 停用冗余实体 + 收集别名
        kw = list(keep.get("keywords") or [])
        for dn in dup_names:
            dup = entities.get(dn)
            if not dup:
                print("  = 「%s」不存在，跳过" % dn)
                continue
            if dn in referenced:
                print("  ⚠ 「%s」被 relationships 引用，跳过停用（避免断链）" % dn)
                skipped_link.append(dn)
                continue
            # 停用（保持原 source，不改变其治理状态语义）
            dup["enabled"] = False
            ok, msg = config_store.upsert_entity(
                dup, source=(dup.get("source") or "manual"), operator="dedup_entities")
            print("  ✓ 停用「%s」→ enabled=0（%s）" % (dn, msg))
            disabled += 1
            # 冗余名并入保留实体 keywords，保留检索能力
            if dn not in kw:
                kw.append(dn)

        # ② 写回保留实体（含新别名）。人工治理 → source=manual（顺带纠正 source/status 不一致）
        keep["keywords"] = kw
        ok, msg = config_store.upsert_entity(keep, source="manual", operator="dedup_entities")
        print("✓ 保留「%s」，keywords 新增 %d 个别名（%s）" %
              (keep_name, len(kw) - len(keep.get("keywords") or []), msg))
        aliased += 1

    print("\n===== 合并结果 =====")
    print("停用冗余实体：%d 个；保留实体（加别名）：%d 个" % (disabled, aliased))
    if skipped_link:
        print("因被 relationships 引用而跳过停用的：%s" % "、".join(skipped_link))

    print("\n===== 需人工确认（未自动合并）=====")
    for a, b, why in REVIEW:
        print("  · %s  ↔  %s —— %s" % (a, b, why))

    # ③ 复核：同一主表仍有多实体启用的（应只剩「需人工确认」那几组）
    print("\n===== 合并后仍冗余的组（供核对）=====")
    import sqlite3
    con = sqlite3.connect("agent_config.db")
    rows = con.execute(
        "SELECT table_name, COUNT(*), GROUP_CONCAT(name, '；') FROM entities "
        "WHERE enabled=1 GROUP BY table_name HAVING COUNT(*)>1 ORDER BY COUNT(*) DESC"
    ).fetchall()
    con.close()
    for r in rows:
        print("  · %s（%d）: %s" % (r[0], r[1], r[2]))
    if not rows:
        print("  （无）")


if __name__ == "__main__":
    main()
