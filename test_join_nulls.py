# -*- coding: utf-8 -*-
"""
test_join_nulls.py —— ClickHouse LEFT/RIGHT JOIN 未匹配补值检查专项测试

★ 背景：ClickHouse join_use_nulls=0 时，LEFT/RIGHT JOIN 未匹配的右表字段
  补【类型默认值】（数值→0、String→''、Date→默认日期）而非 NULL，
  于是 `s.customer IS NULL / IS NOT NULL` 判断"是否关联成功"会静默算错。

★ 本测试覆盖任务书要求的 5 类场景 + 2 个关键边界：
  ① 数值字段 IS NOT NULL / IS NULL 判断关联 → error（高置信拦截）
  ② 字符串字段未匹配补 ''（不能统一用 = 0）→ warning，suggestion 不含 "!= 0"
  ③ join_use_nulls=1 → 保留标准 NULL 判断，不误报
  ④ 业务字段可能为 0 → 非关联键降级 warning，建议以 matched 标记优先
  ⑤ 非 ClickHouse 方言（MySQL）→ 不误报
  ⑥ Nullable 字段 IS NULL 仍有效 → 不误报
  ⑦ 配置未知（-1）→ 降级 warning（不无差别 error）

运行：.venv/bin/python test_join_nulls.py   （纯内存，不连库、不调模型）
"""
import sql_validator as sv

PASSED, FAILED = [], []


def check(label, got, expected):
    ok = (got == expected)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望: {expected}")
        print(f"       实际: {got}")
    return ok


def check_true(label, cond, detail=""):
    ok = bool(cond)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok and detail:
        print(f"       实际: {detail}")
    return ok


# ── 右表字段类型（与 rag._build_tables 的 columns 格式一致）──
TABLES = [
    {"name": "ods_tu_customer_all",
     "columns": "  - customer (UInt64)  客户id\n  - ctime (Int32)  注册时间",
     "column_list": ["customer", "ctime"]},
    {"name": "small_order_users",
     "columns": ("  - customer (UInt64)  客户id\n"
                 "  - name (String)  客户名\n"
                 "  - order_count (UInt64)  发单次数（业务允许 0）\n"
                 "  - reg_date (Date)  注册日期\n"
                 "  - remark (Nullable(String))  备注"),
     "column_list": ["customer", "name", "order_count", "reg_date", "remark"]},
]


class _Ctx:
    def __init__(self, tables=TABLES):
        self.p3_table_objs = tables


def run(sql, dialect="ClickHouse", jn=0, tables=TABLES):
    """以显式方言/配置跑一次 join_nulls 检查，返回 issues。"""
    issues = []
    sv._check_join_nulls(sql, _Ctx(tables), issues, dialect=dialect, join_use_nulls=jn)
    return issues


# ═══════════════════════════════════════════════════════════
# ① 数值字段判断关联（核心场景）
# ═══════════════════════════════════════════════════════════
def test_numeric_key_is_not_null_blocked():
    print("\n【1】数值关联键 s.customer IS NOT NULL → error（join_use_nulls=0）")
    issues = run("SELECT COUNT(*) FROM ods_tu_customer_all u "
                 "LEFT JOIN small_order_users s ON u.customer = s.customer "
                 "WHERE s.customer IS NOT NULL")
    errs = [i for i in issues if i["rule"] == "join_nulls" and i["severity"] == "error"]
    check_true("拦截 IS NOT NULL 判断关联", bool(errs), issues)


def test_numeric_key_is_null_blocked():
    print("\n【2】数值关联键 s.customer IS NULL → error（同样失效）")
    issues = run("SELECT COUNT(*) FROM ods_tu_customer_all u "
                 "LEFT JOIN small_order_users s ON u.customer = s.customer "
                 "WHERE s.customer IS NULL")
    errs = [i for i in issues if i["rule"] == "join_nulls" and i["severity"] == "error"]
    check_true("拦截 IS NULL 判断关联", bool(errs), issues)


def test_matched_flag_no_issue():
    print("\n【3】★ 推荐写法（显式 matched 标记）→ 不误报")
    issues = run("SELECT COUNT(*) AS `注册客户数`, countIf(s.matched = 1) AS `有小板发单数` "
                 "FROM ods_tu_customer_all u "
                 "LEFT JOIN (SELECT customer, 1 AS matched FROM small_order_users "
                 "GROUP BY customer) s ON u.customer = s.customer")
    check("matched 标记写法无任何 join_nulls 告警",
          [i for i in issues if i["rule"] == "join_nulls"], [])


# ═══════════════════════════════════════════════════════════
# ② 字符串字段默认值（不能统一用 = 0）
# ═══════════════════════════════════════════════════════════
def test_string_field_fill_empty():
    print("\n【4】String 字段未匹配补 ''（不能统一用 = 0）")
    issues = run("SELECT COUNT(*) FROM ods_tu_customer_all u "
                 "LEFT JOIN small_order_users s ON u.customer = s.customer "
                 "WHERE s.name IS NULL")
    warns = [i for i in issues if i["rule"] == "join_nulls"]
    check_true("字符串字段 IS NULL 产生告警（补 '' 而非 NULL）", bool(warns), issues)
    # ★ 关键：字符串补空串，绝不能建议 "!= 0"
    check("suggestion 不含对字符串的 != 0 建议",
          any("!= 0" in i.get("suggestion", "") for i in warns), False)
    check("告警正确说明补『空字符串』而非 0",
          any("空字符串" in i.get("message", "") for i in warns), True)


# ═══════════════════════════════════════════════════════════
# ③ join_use_nulls = 1（保留标准 NULL 判断）
# ═══════════════════════════════════════════════════════════
def test_join_use_nulls_1_keep_null():
    print("\n【5】join_use_nulls=1 → 保留 IS NULL/IS NOT NULL，不误报")
    issues = run("SELECT COUNT(*) FROM ods_tu_customer_all u "
                 "LEFT JOIN small_order_users s ON u.customer = s.customer "
                 "WHERE s.customer IS NOT NULL", jn=1)
    check("jn=1 时无任何告警", issues, [])


# ═══════════════════════════════════════════════════════════
# ④ 业务字段可能为 0（不得用默认值作关联成功标志）
# ═══════════════════════════════════════════════════════════
def test_field_can_be_zero():
    print("\n【6】业务字段允许 0（发单次数 order_count）→ 非关联键降级 warning")
    issues = run("SELECT COUNT(*) FROM ods_tu_customer_all u "
                 "LEFT JOIN small_order_users s ON u.customer = s.customer "
                 "WHERE s.order_count IS NOT NULL")
    warns = [i for i in issues if i["rule"] == "join_nulls"]
    check_true("非关联键字段只给 warning（不无差别 error）", bool(warns), issues)
    check("非关联键字段不产生 error",
          any(i["severity"] == "error" for i in issues), False)
    # ★ 建议以 matched 标记优先，且 != 0 建议必须带"不可能为 0"的条件限定
    check_true("suggestion 优先推荐 matched 标记",
               all(i.get("suggestion", "").startswith("改用显式关联标记字段") for i in warns),
               warns)
    check_true("!= 0 建议带『不可能为 0』条件限定（避免误导）",
               any("不可能为 0" in i.get("suggestion", "") for i in warns), warns)


# ═══════════════════════════════════════════════════════════
# ⑤ 非 ClickHouse 方言（MySQL 不误报）
# ═══════════════════════════════════════════════════════════
def test_non_clickhouse_dialect_no_issue():
    print("\n【7】MySQL 方言 → 保持标准 NULL 判断，不误报")
    issues = run("SELECT COUNT(*) FROM ods_tu_customer_all u "
                 "LEFT JOIN small_order_users s ON u.customer = s.customer "
                 "WHERE s.customer IS NULL", dialect="mysql")
    check("MySQL 方言无任何告警", issues, [])
    issues2 = run("SELECT COUNT(*) FROM ods_tu_customer_all u "
                  "LEFT JOIN small_order_users s ON u.customer = s.customer "
                  "WHERE s.customer IS NULL", dialect="sqlite")
    check("SQLite 方言无任何告警", issues2, [])


# ═══════════════════════════════════════════════════════════
# ⑥ Nullable 字段 / ⑦ 配置未知（两个关键边界）
# ═══════════════════════════════════════════════════════════
def test_nullable_field_no_issue():
    print("\n【8】右表 Nullable 字段 IS NULL 仍有效 → 不误报")
    issues = run("SELECT COUNT(*) FROM ods_tu_customer_all u "
                 "LEFT JOIN small_order_users s ON u.customer = s.customer "
                 "WHERE s.remark IS NULL")
    check("Nullable 字段无告警", [i for i in issues if i["rule"] == "join_nulls"], [])


def test_config_unknown_warning_only():
    print("\n【9】join_use_nulls 未知（-1）→ 只给 warning，不无差别 error")
    issues = run("SELECT COUNT(*) FROM ods_tu_customer_all u "
                 "LEFT JOIN small_order_users s ON u.customer = s.customer "
                 "WHERE s.customer IS NOT NULL", jn=-1)
    warns = [i for i in issues if i["rule"] == "join_nulls"]
    check_true("配置未知时产生提示", bool(warns), issues)
    check("配置未知时不产生 error（不假设补 0）",
          any(i["severity"] == "error" for i in issues), False)


# ═══════════════════════════════════════════════════════════
# 辅助函数边界
# ═══════════════════════════════════════════════════════════
def test_default_fill_kind():
    print("\n【10】字段类型 → 默认补值类别判定")
    check("Nullable(String) → nullable", sv._default_fill_kind("Nullable(String)"), "nullable")
    check("UInt64 → numeric", sv._default_fill_kind("UInt64"), "numeric")
    check("Int32 → numeric", sv._default_fill_kind("Int32"), "numeric")
    check("Float64 → numeric", sv._default_fill_kind("Float64"), "numeric")
    check("Decimal(18,2) → numeric", sv._default_fill_kind("Decimal(18,2)"), "numeric")
    check("String → string", sv._default_fill_kind("String"), "string")
    check("Date → date", sv._default_fill_kind("Date"), "date")
    check("DateTime → date", sv._default_fill_kind("DateTime"), "date")
    check("空类型 → unknown", sv._default_fill_kind(""), "unknown")
    check("未知类型 → unknown", sv._default_fill_kind("Array(UInt64)"), "unknown")


def test_right_join_blocked():
    print("\n【11】RIGHT JOIN 同样受默认补值机制约束")
    issues = run("SELECT COUNT(*) FROM small_order_users s "
                 "RIGHT JOIN ods_tu_customer_all u ON u.customer = s.customer "
                 "WHERE s.customer IS NOT NULL")
    errs = [i for i in issues if i["rule"] == "join_nulls" and i["severity"] == "error"]
    check_true("RIGHT JOIN 未匹配判断同样被拦截", bool(errs), issues)


if __name__ == "__main__":
    print()
    print("╔" + "═" * 62 + "╗")
    print("║" + "ClickHouse LEFT JOIN 未匹配补值 · 专项测试".center(40) + "║")
    print("╚" + "═" * 62 + "╝")
    test_numeric_key_is_not_null_blocked()
    test_numeric_key_is_null_blocked()
    test_matched_flag_no_issue()
    test_string_field_fill_empty()
    test_join_use_nulls_1_keep_null()
    test_field_can_be_zero()
    test_non_clickhouse_dialect_no_issue()
    test_nullable_field_no_issue()
    test_config_unknown_warning_only()
    test_default_fill_kind()
    test_right_join_blocked()
    print()
    print("=" * 64)
    total = len(PASSED) + len(FAILED)
    if not FAILED:
        print(f"🎉 全部通过（{len(PASSED)}/{total}）")
    else:
        print(f"⚠️ 通过 {len(PASSED)}/{total}，失败项：")
        for f in FAILED:
            print(f"   ❌ {f}")
    print("=" * 64)
    raise SystemExit(1 if FAILED else 0)
