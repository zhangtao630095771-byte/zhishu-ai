# -*- coding: utf-8 -*-
"""
_seed_join_nulls_rule.py —— 把「ClickHouse LEFT JOIN 未匹配补值」规则追加进 SQL 生成 Prompt

★ 背景：ClickHouse join_use_nulls=0 时，LEFT/RIGHT JOIN 未匹配的右表字段
  补【类型默认值】（数值→0、String→''、Date→默认日期），而不是 NULL。
  于是 `s.customer IS NULL / IS NOT NULL` 判断关联会静默算错。

★ 本脚本只做一件事：把规则 21 追加进 prompts.sql_rules（幂等，不覆盖现有内容）。
  运行：.venv/bin/python _seed_join_nulls_rule.py
"""
import config_store as cs

# 规则 21 文案（与 config_store.DEFAULT_PROMPTS["sql_rules"] 末尾保持一致）
RULE_21 = """21. ★ ClickHouse LEFT JOIN 未匹配记录的判断（join_use_nulls=0 的默认补值机制，违反会【静默算错】）：
    ClickHouse 在 join_use_nulls=0 时，LEFT JOIN / RIGHT JOIN 未匹配的右表字段
    【补字段类型的默认值，而不是 NULL】：
      · UInt/Int/Float/Decimal 等数值类型 → 补 0
      · String / FixedString            → 补空字符串 ''
      · Date / DateTime                 → 补对应类型的默认日期（如 1970-01-01）
    ★ 因此【禁止】用右表字段 `IS NULL` / `IS NOT NULL` 判断"是否关联成功"：
      数值字段未匹配补 0，`s.customer IS NULL` 永远为假、`s.customer IS NOT NULL`
      永远为真 → 关联判断彻底失效，统计结果静默算错（尤其"有小板发单的客户占比"这类）。
    ★ 正确写法（二选一，优先用 (a)）：
      (a) 显式关联标记字段：右表子查询里输出 `1 AS matched`（如
          `SELECT customer, 1 AS matched FROM 小板发单表 GROUP BY customer`），
          关联后用 `countIf(s.matched = 1)` / `s.matched = 1` 判断；未匹配时
          matched 默认补 0，判断可靠且不依赖业务字段取值。
      (b) 若右表业务主键【确认不可能为 0】（如客户 id 从 1 起），可用
          `s.customer != 0` 判断关联成功；若该字段业务上允许 0，则不可用此法。
    ★ 例外：右表字段本身是 Nullable 类型时，未匹配补 NULL，`IS NULL` 判断仍然有效。
    ★ 边界：本规则【只针对 ClickHouse】；SQLite / MySQL 等方言保持标准 NULL 判断，
      不要改它们的 IS NULL / IS NOT NULL 逻辑。"""

# 用于幂等判定的特征串（规则 21 独有，避免误判）
_FINGERPRINT = "join_use_nulls=0 的默认补值机制"


def main():
    cs._ensure_ready()
    cur = cs.get_prompt("sql_rules") or ""

    if _FINGERPRINT in cur:
        print("ℹ️ 规则 21（ClickHouse LEFT JOIN 补值机制）已存在，跳过（幂等）。")
        return

    # ★ 只追加，绝不覆盖既有规则（现有 sql_rules 是人工维护的，覆盖 = 丢失历史）
    new_rules = (cur.rstrip() + "\n\n" + RULE_21 + "\n").lstrip("\n")
    cs.set_prompt("sql_rules", new_rules)

    # 回读确认
    back = cs.get_prompt("sql_rules") or ""
    ok = _FINGERPRINT in back
    print(f"{'✅' if ok else '❌'} 规则 21 追加{'成功' if ok else '失败'}")
    print(f"   写入前长度：{len(cur)}，写入后长度：{len(back)}")


if __name__ == "__main__":
    main()
