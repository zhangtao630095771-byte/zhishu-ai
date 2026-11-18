# -*- coding: utf-8 -*-
"""
_p0_relationships.py —— P0-3 实体关系治理（幂等、可重复执行）

★ 作用：
  1. relationships 新增 4 个治理字段：allow_auto_join / agg_risk / confidence / scenario。
  2. 按 cardinality + 现有治理字段回填（纯机械、确定性）：
     - agg_risk：1:N / N:1 → high（一对多 Join 有重复统计风险）；1:1 → low。
     - allow_auto_join：1:1 → 1（可自动 Join）；1:N / N:1 → 0（须人工确认后再允许）。
     - confidence：approved+manual → high；否则 medium。
  3. 全程写 knowledge_history 审计；不修改 pre_agg_required（那是业务口径，须人工）。

★ 只做无争议的机械回填；「1:N 但 pre_agg_required=0」的关系打印出来交人工确认。
"""
import json
import sqlite3
import datetime


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    con = sqlite3.connect("agent_config.db")
    cur = con.cursor()

    # ① 新增字段（幂等）
    cols = {r[1] for r in cur.execute("PRAGMA table_info(relationships)")}
    for col, ddl in [
        ("allow_auto_join", "INTEGER DEFAULT 0"),
        ("agg_risk", "TEXT DEFAULT 'low'"),
        ("confidence", "TEXT DEFAULT 'medium'"),
        ("scenario", "TEXT"),
    ]:
        if col not in cols:
            cur.execute("ALTER TABLE relationships ADD COLUMN %s %s" % (col, ddl))
            print("  ✓ ADD COLUMN %s" % col)
        else:
            print("  = 列 %s 已存在，跳过" % col)

    # ② 回填（机械规则）
    rows = cur.execute(
        "SELECT id, from_entity, to_entity, cardinality, pre_agg_required, status, source "
        "FROM relationships").fetchall()

    risk_1n = []
    updated = 0
    for r in rows:
        rid, fe, te, card, preagg, status, source = r
        is_1n = card in ("1:N", "N:1")
        agg_risk = "high" if is_1n else "low"
        allow_auto = 0 if is_1n else 1
        confidence = "high" if (status == "approved" and (source or "") == "manual") else "medium"
        after = {"agg_risk": agg_risk, "allow_auto_join": allow_auto, "confidence": confidence}
        cur.execute(
            "UPDATE relationships SET agg_risk=?, allow_auto_join=?, confidence=? WHERE id=?",
            (agg_risk, allow_auto, confidence, rid))
        cur.execute(
            "INSERT INTO knowledge_history(entity_type, entity_id, action, before_json, after_json, operator, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("relationship", str(rid), "update", None, json.dumps(after, ensure_ascii=False),
             "p0_relationships", _now()))
        updated += 1
        if is_1n and not preagg:
            risk_1n.append("  · id=%s %s → %s（%s，pre_agg_required=0）" % (rid, fe, te, card))

    con.commit()
    con.close()

    print("\n① 回填完成：%d 条关系已设置 agg_risk/allow_auto_join/confidence" % updated)
    print("\n② 需人工确认「1:N 但未设预聚合」的关系（%d 条，可能存在重复统计风险）：" % len(risk_1n))
    for line in risk_1n:
        print(line)
    print("\n（说明：不自动改 pre_agg_required；业务确认后按需置 1）")


if __name__ == "__main__":
    main()
