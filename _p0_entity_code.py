# -*- coding: utf-8 -*-
"""
_p0_entity_code.py —— P0-2 实体编码 + 业务主键（幂等、可重复执行）

★ 作用：
  1. 实体编码 entity_code：机械补全（ENT_<table_name>），只填空的、不改已有人工编码。
  2. 业务主键 primary_key：只从 grain 的 (xxx) / （xxx 唯一） 提示做【建议】，不自动落库，
     写成报告交人工确认（主键涉及真实 schema，不可机械猜）。

★ 原则：编码是确定性标识，可安全回填；主键是业务口径，只出建议。
"""
import re
import json
import sqlite3
import datetime


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 从 grain 提取主键候选：优先 (xxx) 或 （xxx 唯一…），复合键「按 a + b」单独处理
def _pk_hint(grain):
    if not grain:
        return None, ""
    # 中文括号「（xxx 唯一）」
    m = re.search(r"[（(]([a-zA-Z_][a-zA-Z0-9_]*)\s*唯一", grain)
    if m:
        return m.group(1), "grain 标注「唯一」"
    # 半角括号 (order_no) / (order_no 可多条)
    m = re.search(r"\(([a-zA-Z_][a-zA-Z0-9_]*)[^)]*\)", grain)
    if m:
        key = m.group(1)
        note = "grain 括号提示" + ("（但标注可多条，非唯一）" if "多条" in grain else "")
        return key, note
    # 复合键「按 a + b 关联」
    m = re.search(r"按\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[+＋]\s*([a-zA-Z_][a-zA-Z0-9_]*)", grain)
    if m:
        return "%s+%s" % (m.group(1), m.group(2)), "grain 复合键提示（按 a+b）"
    return None, ""


def main():
    con = sqlite3.connect("agent_config.db")
    cur = con.cursor()

    rows = cur.execute(
        "SELECT name, table_name, grain, COALESCE(entity_code,''), COALESCE(primary_key,'') "
        "FROM entities WHERE enabled=1 ORDER BY name").fetchall()

    code_filled = 0
    pk_proposals = []
    for name, table, grain, code, pk in rows:
        # ① entity_code 机械补全
        if not code and table:
            new_code = "ENT_" + table
            cur.execute("UPDATE entities SET entity_code=?, updated_at=? WHERE name=?",
                        (new_code, _now(), name))
            cur.execute(
                "INSERT INTO knowledge_history(entity_type, entity_id, action, before_json, after_json, operator, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("entity", name, "update", None, json.dumps({"entity_code": new_code}, ensure_ascii=False),
                 "p0_entity_code", _now()))
            code_filled += 1
        # ② primary_key 建议（把 "[]"/"null"/"{}" 这类空占位也当空）
        if (not pk) or (str(pk).strip() in ("[]", "null", "None", "{}", "")):
            hint, note = _pk_hint(grain)
            if hint:
                pk_proposals.append((name, table, hint, note))

    con.commit()

    # 输出建议报告
    lines = [
        "# 实体业务主键建议（P0-2）",
        "",
        "> 生成时间：2026-11-17　本清单【只建议、不自动落库】；主键涉及真实表结构，须人工确认后回填。",
        "",
        "| 实体 | 物理表 | 建议主键 | 依据 |",
        "|---|---|---|---|",
    ]
    for name, table, hint, note in pk_proposals:
        lines.append("| %s | %s | %s | %s |" % (name, table, hint, note))
    lines += [
        "",
        "## 说明",
        "",
        "1. 仅当 grain 明确标注了 `(key)` / `（key 唯一）` / `按 a+b` 时才给出建议；其余实体的主键需结合真实表 schema 判断。",
        "2. 「可多条」标注的主键（如 order_no）仅作关联键，不满足唯一性，需业务确认。",
    ]
    with open("docs/实体业务主键建议_P0-2.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    con.close()
    print("① entity_code 机械补全：%d 个实体" % code_filled)
    print("② primary_key 建议：%d 条（已写 docs/实体业务主键建议_P0-2.md）" % len(pk_proposals))
    for name, table, hint, note in pk_proposals:
        print("   · %s（%s）→ %s  [%s]" % (name, table, hint, note))


if __name__ == "__main__":
    main()
