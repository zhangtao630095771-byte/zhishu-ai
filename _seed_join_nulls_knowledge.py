# -*- coding: utf-8 -*-
"""
_seed_join_nulls_knowledge.py —— 把「ClickHouse LEFT JOIN 未匹配补值」沉淀为 SQL 规则知识

★ 定位（任务 P1）：把这类问题结构化沉淀到 SQL 规则知识（sql_experience_rules），
  作为 ClickHouse 专属知识，含完整知识点 + SQL 模板 + 适用范围 + 例外，
  供后续生成 SQL 时检索参考（layer=join_rule）。

★ 与 _seed_join_nulls_rule.py 的区别：
  前者写【prompts.sql_rules】（每次生成都注入，权威）；
  本脚本写【sql_experience_rules】（结构化知识，可治理、可检索、带模板 SQL）。
  两者内容一致，落点不同：一个管"每次必看"，一个管"可检索复用"。

★ 幂等：已存在同主题规则时跳过，不重复插入。
运行：.venv/bin/python _seed_join_nulls_knowledge.py
"""
import sql_experience as se

# 唯一性指纹（title 特征串）
_TITLE_FINGERPRINT = "join_use_nulls"

RULE = {
    "topic": "ClickHouse LEFT JOIN 未匹配补值机制",
    "layer": "join_rule",
    "title": ("ClickHouse LEFT/RIGHT JOIN 未匹配记录不能用 IS NULL 判断关联"
              "（join_use_nulls=0 补类型默认值）"),
    "content": (
        "ClickHouse 的 join_use_nulls 配置决定 LEFT/RIGHT JOIN 未匹配的右表字段补什么：\n"
        "· join_use_nulls=0（常见默认）：未匹配字段补【类型默认值】而非 NULL ——\n"
        "   数值类型(UInt/Int/Float/Decimal)补 0；String/FixedString 补空字符串 ''；\n"
        "   Date/DateTime 补对应类型默认日期(如 1970-01-01)。\n"
        "· join_use_nulls=1：未匹配字段补 NULL（标准 SQL 行为，IS NULL 判断有效）。\n"
        "后果：join_use_nulls=0 时，用右表字段 `IS NULL` / `IS NOT NULL` 判断\n"
        "「是否关联成功」会静默算错 —— 数值字段 IS NULL 恒为假、IS NOT NULL 恒为真，\n"
        "业务统计（如「有小板发单的客户占比」）结果错误且不报错。\n"
        "推荐写法（优先用显式关联标记字段，不依赖业务字段默认值）：\n"
        "  右表子查询输出 `1 AS matched`，关联后用 `countIf(matched = 1)` 判断；\n"
        "  未匹配时 matched 默认补 0，可靠且不依赖业务字段取值。\n"
        "备选：若右表业务主键确认不可能为 0（如客户 id 从 1 起），可用 `!= 0` 判断；\n"
        "  但若该字段业务上允许 0（如次数/数量字段），此写法不可靠，禁止使用。\n"
        "例外：右表字段本身是 Nullable 类型时，未匹配补 NULL，IS NULL 判断仍有效。\n"
        "边界：本规则只针对 ClickHouse；SQLite/MySQL 保持标准 NULL 判断。"
    ),
    "template_sql": (
        "WITH small_order AS (\n"
        "  SELECT customer, 1 AS matched FROM small_order_users GROUP BY customer\n"
        ")\n"
        "SELECT COUNT(*) AS `注册客户数`,\n"
        "       countIf(s.matched = 1) AS `有小板发单客户数`,\n"
        "       round(countIf(s.matched = 1) * 1.0 / COUNT(*), 4) AS `小板发单占比`\n"
        "FROM customer_all u\n"
        "LEFT JOIN small_order s ON u.customer = s.customer"
    ),
    "params": {},
    "tables": [],
    "metrics": [],
    "scope": ("仅 ClickHouse 方言、且 join_use_nulls=0（或未知待确认）时适用；"
              "SQLite / MySQL 等方言不适用"),
    "exceptions": ("join_use_nulls=1 时保留标准 IS NULL/IS NOT NULL 判断；"
                   "右表字段为 Nullable 类型时 IS NULL 判断仍有效"),
    "source_case_ids": ["manual:clickhouse-join-nulls-2026-11-11"],
    "confidence": "high",
}


def main():
    # 幂等检查：已存在同主题规则则跳过
    for r in se.list_rules(layer="join_rule", limit=200):
        if _TITLE_FINGERPRINT in (r.get("title") or ""):
            print(f"ℹ️ 规则知识已存在（id={r.get('id')}，{r.get('rule_no')}），跳过（幂等）。")
            return

    rid = se.upsert_rule(RULE, operator="system", status="approved")
    back = se.get_rule(rid)
    ok = back and _TITLE_FINGERPRINT in (back.get("title") or "")
    print(f"{'✅' if ok else '❌'} 已沉淀 ClickHouse JOIN 规则知识")
    if back:
        print(f"   id={back.get('id')}  rule_no={back.get('rule_no')}  "
              f"layer={back.get('layer')}  status={back.get('status')}  "
              f"confidence={back.get('confidence')}")


if __name__ == "__main__":
    main()
