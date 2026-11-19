# -*- coding: utf-8 -*-
"""
test_guard.py —— 安全护栏的专项测试
★ 为什么护栏必须有专项测试？
  因为它是"安全"功能，不是"业务"功能：
    业务错了 → 用户会用出来 → 你会收到反馈
    安全错了 → 没有任何症状 → 直到出事那天才发现
  评测集（evaluate.py）测的是"答得对不对"，
  这个脚本测的是"拦得准不准" —— 两件事，必须分开测。

运行： .venv/bin/python test_guard.py
"""
import re                                   # 只在"复刻旧版正则"时用到
import guard                                # 被测对象


# ═══════════════════════════════════════════════════════════
# 工具：复刻"改造前"的纯正则实现，用来做新旧对比
# ═══════════════════════════════════════════════════════════
_OLD_TABLE_PATTERN = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)


def old_extract_tables(sql):
    """改造前的版本：纯正则，不排除 CTE 别名（保留下来做对照实验）"""
    found = _OLD_TABLE_PATTERN.findall(sql)          # 抓 FROM/JOIN 后面的词
    tables = set()                                   # 结果集合
    for t in found:
        t = t.strip()
        if "." in t:
            t = t.split(".")[-1]                     # 取表名部分
        if t:
            tables.add(t.lower())
    return tables


# ═══════════════════════════════════════════════════════════
# 第一部分：表名提取的精确性
# ═══════════════════════════════════════════════════════════
# 每条 = (说明, SQL, 期望提取到的表名集合)
# ★ 期望值是"人肉判断"出来的标准答案 —— 测试的价值全在这个期望值上
# ═══════════════════════════════════════════════════════════
EXTRACT_CASES = [
    ("最简单的单表",
     "SELECT COUNT(*) FROM dwd_order_all",
     {"dwd_order_all"}),

    ("带库名前缀",
     "SELECT * FROM demo.dwd_order_all",
     {"dwd_order_all"}),

    ("两表 JOIN",
     "SELECT * FROM dwd_order_all o JOIN dim_user u ON o.user_id = u.user_id",
     {"dwd_order_all", "dim_user"}),

    ("逗号连接（隐式 JOIN）",
     "SELECT * FROM dwd_order_all, dim_user WHERE 1=1",
     {"dwd_order_all", "dim_user"}),

    ("UNION 两个表",
     "SELECT 1 FROM dwd_order_all UNION ALL SELECT 2 FROM dwd_refund",
     {"dwd_order_all", "dwd_refund"}),

    ("★ CTE 别名不该被当成表",
     "WITH t AS (SELECT 1) SELECT * FROM t",
     set()),                                     # 一个真表都没有（t 只是别名）

    ("★ CTE + 真表混用",
     "WITH t AS (SELECT * FROM dwd_order_all) SELECT * FROM t JOIN dim_user u ON 1=1",
     {"dwd_order_all", "dim_user"}),             # t 要排除，另两个要保留

    ("★ CTE 链（多个别名）",
     "WITH a AS (SELECT 1), b AS (SELECT * FROM dim_user) SELECT * FROM a JOIN b ON 1=1",
     {"dim_user"}),                              # a、b 都是别名

    ("★ 子查询别名不该被当成表",
     "SELECT * FROM (SELECT user_id FROM dim_user) AS x",
     {"dim_user"}),                              # x 是子查询别名

    ("注释里的表名不该被当成表",
     "SELECT * FROM dwd_order_all -- FROM secret_table",
     {"dwd_order_all"}),

    ("多语句：后半段也要扫到",
     "SELECT 1; DROP TABLE dwd_refund",
     {"dwd_refund"}),                            # 只看第一句就会漏掉这张表

    ("函数名不该被当成表",
     "SELECT from_unixtime(x) FROM dwd_order_all",
     {"dwd_order_all"}),

    ("反引号包裹的表名",
     "SELECT * FROM `dwd_order_all`",
     {"dwd_order_all"}),

    ("没有表（纯计算）",
     "SELECT 1 + 1",
     set()),

    ("大小写不敏感",
     "SELECT * FROM DWD_ORDER_ALL",
     {"dwd_order_all"}),
]


def run_extract_tests():
    """跑第一部分：表名提取精确性"""
    print("=" * 78)
    print("第一部分：表名提取精确性（对比：旧正则 vs 新解析器）")
    print("=" * 78)
    print(f"{'用例':<28}{'期望':<22}{'旧正则':<22}{'新解析器':<22}{'结果'}")
    print("-" * 78)

    passed = 0
    regressions = []                                # 记录"旧版对被、新版错了"的情况（回归）

    for label, sql, expected in EXTRACT_CASES:
        old = old_extract_tables(sql)               # 旧版结果
        new = guard.extract_tables(sql)             # 新版结果
        ok = (new == expected)                      # 新版是否等于期望

        if ok:
            passed += 1

        old_ok = (old == expected)                  # 旧版是否等于期望
        if old_ok and not ok:
            regressions.append(label)               # 旧版对、新版错 —— 这种绝对不能有

        print(f"{label:<28}{fmt(expected):<22}{fmt(old):<22}{fmt(new):<22}{'✅' if ok else '❌'}")

    print("-" * 78)
    print(f"通过：{passed}/{len(EXTRACT_CASES)}")
    if regressions:
        print(f"⚠️ 发现回归（旧版正确但新版错误）：{regressions}")
    else:
        print("✅ 无回归")

    # ── 专门展示改进点 ──
    print()
    print("改进点专项展示（这几条是旧版做不到的）：")
    for label, sql, expected in EXTRACT_CASES:
        old = old_extract_tables(sql)
        new = guard.extract_tables(sql)
        if old != new:                              # 只在"结果不同"时展示
            print(f"  · {label}")
            print(f"      SQL     : {sql}")
            print(f"      期望    : {sorted(expected)}")
            print(f"      旧正则  : {sorted(old)}   {'✅' if old == expected else '❌'}")
            print(f"      新解析器: {sorted(new)}   {'✅' if new == expected else '❌'}")

    return passed == len(EXTRACT_CASES) and not regressions


def fmt(s):
    """把集合格式化成短字符串，方便打印成表格"""
    if not s:
        return "{}"
    return "{" + ",".join(sorted(s)) + "}"


# ═══════════════════════════════════════════════════════════
# 第二部分：权限校验（check_tables）
# ═══════════════════════════════════════════════════════════
# 模拟一个 AI 账号被授权的表白名单
# ═══════════════════════════════════════════════════════════
ALLOWED = ["dwd_order_all", "dim_user", "dim_driver", "dwd_refund"]
#           注意：dim_user_contact（含手机号身份证）故意不在名单里

CHECK_CASES = [
    ("正常查询",            "SELECT COUNT(*) FROM dwd_order_all",                       True),
    ("JOIN 白名单内的表",    "SELECT * FROM dwd_order_all o JOIN dim_user u ON 1=1",      True),
    ("越权查敏感表",         "SELECT phone FROM dim_user_contact WHERE user_id=1",        False),
    ("越权 + 库名前缀",      "SELECT u.phone FROM demo.dim_user_contact u",              False),
    ("越权藏在 JOIN 里",     "SELECT * FROM dwd_order_all o JOIN dim_user_contact c ON 1=1", False),
    ("★ CTE 包装的越权",     "WITH t AS (SELECT phone FROM dim_user_contact) SELECT * FROM t", False),
    ("★ CTE 正常不应误拦",   "WITH t AS (SELECT * FROM dwd_order_all) SELECT * FROM t",   True),
    ("越权藏在子查询里",      "SELECT * FROM (SELECT phone FROM dim_user_contact) x",      False),
    ("注释里藏敏感表",        "SELECT * FROM dwd_order_all -- dim_user_contact",           True),
    #   ↑ 注释里的不算引用，应该放行 —— 这恰恰证明了"不误拦"
]


def run_check_tests():
    """跑第二部分：权限校验"""
    print()
    print("=" * 78)
    print("第二部分：表级权限校验（check_tables）")
    print(f"白名单：{ALLOWED}")
    print(f"敏感表：dim_user_contact（不在白名单）")
    print("=" * 78)

    passed = 0
    for label, sql, expect_pass in CHECK_CASES:
        ok, illegal = guard.check_tables(sql, ALLOWED)
        correct = (ok == expect_pass)               # 判断结果是否符合预期

        if correct:
            passed += 1

        verdict = "✅ 放行" if ok else f"⛔ 拦截 {illegal}"
        mark = "✅" if correct else "❌"
        print(f"{mark} {label:<22} 期望={'放行' if expect_pass else '拦截':<4} 实际={verdict}")
        print(f"    SQL: {sql}")

    print("-" * 78)
    print(f"通过：{passed}/{len(CHECK_CASES)}")
    return passed == len(CHECK_CASES)


# ═══════════════════════════════════════════════════════════
# 第三部分：护栏本身（sql_guard + clean_sql）
# ═══════════════════════════════════════════════════════════
GUARD_CASES = [
    ("正常 SELECT",        "SELECT COUNT(*) FROM dwd_order_all",           True),
    ("WITH 开头",          "WITH t AS (SELECT 1) SELECT * FROM t",         True),
    ("DELETE",             "DELETE FROM dwd_order_all",                    False),
    ("UPDATE",             "UPDATE dwd_order_all SET amount=0",            False),
    ("DROP",               "DROP TABLE dwd_order_all",                     False),
    ("多语句注入",          "SELECT 1; DROP TABLE dwd_order_all",           False),
    ("注释藏写操作",        "/* hi */ INSERT INTO dwd_order_all VALUES(1)", False),
    ("字段名含 delete 字样", "SELECT deleted_at FROM dwd_order_all",         True),
    #   ↑ 旧版的正则用了 \b 词边界，所以 deleted_at 不会被误判成 DELETE
]

CLEAN_CASES = [
    ("```sql 包裹",  "```sql\nSELECT 1\n```",              "SELECT 1"),
    ("``` 无语言标记", "```\nSELECT 1\n```",                "SELECT 1"),
    ("结尾分号",      "SELECT 1;",                          "SELECT 1"),
    ("前后空白",      "  \n SELECT 1 \n ",                  "SELECT 1"),
    ("带解释文字",    "SELECT 1\n\n这条 SQL 统计了订单量",     "SELECT 1\n\n这条 SQL 统计了订单量"),
    #   ↑ 这种情况清洗不掉（模型没按规定输出），但 SQL 本身还能跑
    #     真正的把关在后面 —— sql_guard 会检查语法结构
]


def run_guard_tests():
    """跑第三部分：sql_guard 和 clean_sql"""
    print()
    print("=" * 78)
    print("第三部分：只读护栏（sql_guard）与清洗（clean_sql）")
    print("=" * 78)

    passed = 0
    for label, sql, expect_ok in GUARD_CASES:
        ok, why = guard.sql_guard(sql)
        correct = (ok == expect_ok)
        if correct:
            passed += 1
        print(f"{'✅' if correct else '❌'} [护栏] {label:<22} 期望={'放行' if expect_ok else '拦截':<4}"
              f" 实际={'✅ 通过' if ok else '⛔ ' + why}")

    print()
    for label, raw, expected in CLEAN_CASES:
        got = guard.clean_sql(raw)
        correct = (got == expected)
        if correct:
            passed += 1
        print(f"{'✅' if correct else '❌'} [清洗] {label:<22} -> {got!r}")

    total = len(GUARD_CASES) + len(CLEAN_CASES)
    print("-" * 78)
    print(f"通过：{passed}/{total}")
    return passed == total


# ═══════════════════════════════════════════════════════════
# 第四部分：LIMIT 自动注入（★ 这里踩过 code 62 的坑）
# ═══════════════════════════════════════════════════════════
#   (说明, 输入的 SQL, 期望结果里 LIMIT 出现几次)
LIMIT_CASES = [
    ("单行已有 LIMIT",         "SELECT a FROM t LIMIT 100",       1),
    ("多行 LIMIT 在行首",      "SELECT a\nFROM t\nLIMIT 100",     1),
    ("limit 小写且在行首",     "SELECT a\nfrom t\nlimit 50",      1),
    ("没有 LIMIT → 自动加",    "SELECT a FROM t",                 1),
    ("末尾有换行 → 自动加",    "SELECT a FROM t\n",               1),
    ("末尾有分号 → 自动加",    "SELECT a FROM t;",                1),
    ("末尾是行注释 → 自动加",  "SELECT a FROM t\n-- 补充说明",      1),
]


def run_limit_tests():
    """
    跑第四部分：db_clickhouse._ensure_limit

    ★ 为什么这条必须有专项测试？
      真实报错：`code 62 Syntax error at 'LIMIT'`。
      根因是判断写成了 `" limit " in sql.lower()` —— 要求 limit 前后都有【空格】。
      而模型输出的 SQL 经常是【多行】的，LIMIT 在行首时前面是换行符，
      判断就失灵了 → 代码又追加一个 LIMIT → `... LIMIT 100 LIMIT 1000`
      → 整条查询直接失败。

      "判断条件写得太窄"这种 bug 不会自己暴露，
      必须用【多行 / 注释 / 分号 / 小写】这些真实形态把它钉住。
    """
    import db_clickhouse                                   # _ensure_limit 在这里（纯函数，不连库）
    print()
    print("=" * 78)
    print("第四部分：LIMIT 自动注入（防止重复追加导致语法错误）")
    print("=" * 78)

    passed = 0
    for label, sql, expect_n in LIMIT_CASES:
        out = db_clickhouse._ensure_limit(sql)
        n = out.lower().count("limit")
        correct = (n == expect_n)
        if correct:
            passed += 1
        print(f"{'✅' if correct else '❌'} [LIMIT] {label:<22} LIMIT×{n}  {out!r}")

    # 额外一条：末尾是行注释时，LIMIT 必须【独占一行、且落在注释之后】。
    # 用空格拼接会被拼进注释里 → 限制悄悄失效（静默降级，最难发现的那种错）。
    # ★ 注意：_ensure_limit 现在会在 LIMIT 之后再补一行 SETTINGS，
    #   所以 LIMIT 不再是"最后一行"——断言应查"LIMIT 是独立行且在注释行之后"，
    #   而不是"最后一行以 LIMIT 开头"（那是加 SETTINGS 之前的过时写法）。
    out = db_clickhouse._ensure_limit("SELECT a FROM t\n-- 说明")
    lines = [l.strip() for l in out.split("\n")]
    comment_idx = next((i for i, l in enumerate(lines) if l.startswith("--")), -1)
    limit_idx = next((i for i, l in enumerate(lines) if l.upper().startswith("LIMIT")), -1)
    limit_line = lines[limit_idx] if limit_idx >= 0 else None
    ok = (limit_idx >= 0) and (comment_idx >= 0) and (limit_idx > comment_idx)
    if ok:
        passed += 1
    print(f"{'✅' if ok else '❌'} [LIMIT] 末尾注释不被吞        "
          f"注释行#{comment_idx} → LIMIT行#{limit_idx} {limit_line!r}")

    total = len(LIMIT_CASES) + 1
    print("-" * 78)
    print(f"通过：{passed}/{total}")
    return passed == total


# ═══════════════════════════════════════════════════════════
# 第五部分：库名前缀补齐（qualify_tables）
# ═══════════════════════════════════════════════════════════
_QUALIFY_DB = "ctc_data_warehouse"

#   (说明, 输入的 SQL, 期望结果里出现几次 "库名." )
QUALIFY_CASES = [
    ("单表 → 加前缀",        "SELECT count() FROM dwd_tu_order_all",                                        1),
    ("已有库名 → 不重复加",   "SELECT count() FROM ctc_data_warehouse.dwd_tu_order_all",                     1),
    ("system 表 → 不动",     "SELECT name FROM system.tables",                                              0),
    ("JOIN 两表 → 都加",     "SELECT a.id FROM dwd_tu_order_all a "
                             "JOIN ods_tu_customer_all b ON a.customer = b.id",                             2),
    ("CTE → 只给真表加",     "WITH t AS (SELECT * FROM dwd_tu_order_all) SELECT count() FROM t",            1),
    ("子查询 → 给真表加",     "SELECT * FROM (SELECT id FROM dwd_tu_order_all) x",                           1),
    ("表函数 → 不动",        "SELECT * FROM numbers(10)",                                                   0),
    ("解析不了 → 原样返回",   "这不是一段 SQL",                                                               0),
    ("注释 → 原样返回",      "-- 无法回答：无权限访问相关数据表",                                                0),
]


def run_qualify_tests():
    """
    跑第五部分：guard.qualify_tables

    ★ 为什么这条必须有专项测试？
      "给表名补库名前缀"看着简单，但字符串替换会踩一堆坑：
        把 CTE 名也加上前缀（SQL 直接废掉）
        给已有前缀的表再加一层（a.b.c，表就找不到了）
        把 system.tables 改成业务库下的 tables（元数据查询直接错）
      这些都必须在测试里钉死，否则某天改动一下就把查询改坏。
    """
    print()
    print("=" * 78)
    print(f"第五部分：库名前缀补齐（库名 = {_QUALIFY_DB}）")
    print("=" * 78)

    passed = 0
    for label, sql, expect_n in QUALIFY_CASES:
        out = guard.qualify_tables(sql, _QUALIFY_DB)
        n = out.count(_QUALIFY_DB + ".")
        correct = (n == expect_n)
        if correct:
            passed += 1
        print(f"{'✅' if correct else '❌'} [前缀] {label:<20} 前缀×{n}")
        if not correct:
            print(f"        期望 {expect_n} 次，实际输出：{out}")

    # 额外一：CTE 名绝不能被加前缀（加了 SQL 直接失效）
    out = guard.qualify_tables(
        "WITH t AS (SELECT * FROM dwd_tu_order_all) SELECT count() FROM t", _QUALIFY_DB)
    ok = f"{_QUALIFY_DB}.t" not in out
    passed += 1 if ok else 0
    print(f"{'✅' if ok else '❌'} [前缀] CTE 名没被加前缀")

    # 额外二：没配库名时原样返回（SQLite 场景）
    out = guard.qualify_tables("SELECT count() FROM dwd_tu_order_all", "")
    ok = out == "SELECT count() FROM dwd_tu_order_all"
    passed += 1 if ok else 0
    print(f"{'✅' if ok else '❌'} [前缀] 库名为空 → 原样返回")

    # 额外三：★ 加前缀后，权限校验必须照样能识别出未授权的表
    #   （前缀必须不影响"表名提取"这一步，否则权限防线会失效）
    ok_bad, illegal = guard.check_tables(
        f"SELECT * FROM {_QUALIFY_DB}.dim_user_contact", ["dwd_tu_order_all"])
    ok = (not ok_bad) and illegal == ["dim_user_contact"]
    passed += 1 if ok else 0
    print(f"{'✅' if ok else '❌'} [前缀] 带前缀的越权表仍被拦下   越权表={illegal}")

    total = len(QUALIFY_CASES) + 3
    print("-" * 78)
    print(f"通过：{passed}/{total}")
    return passed == total


# ═══════════════════════════════════════════════════════════
# 第六部分：输出列 → 来源字段（给结果列找中文名用）
# ═══════════════════════════════════════════════════════════
SOURCE_FIELD_CASES = [
    #   (说明, SQL, 期望的 {输出列: 来源字段})
    ("字段别名 → 指向真实字段", "SELECT origin_city AS city FROM t",      {"city": "origin_city"}),
    ("无别名纯字段 → 指向自己", "SELECT status FROM t",                   {"status": "status"}),
    ("聚合 → 无来源字段",      "SELECT count() AS cnt FROM t",           {"cnt": None}),
    ("算术 → 无来源字段",      "SELECT total / 2 AS half FROM t",        {"half": None}),
    ("函数包装 → 无来源字段",   "SELECT toDate(ctime) AS d FROM t",       {"d": None}),
    ("混合",                 "SELECT origin_city AS city, count() AS c FROM t",
                                                                        {"city": "origin_city", "c": None}),
]


def run_source_field_tests():
    """
    跑第六部分：guard.output_source_fields

    ★ 为什么这条必须有专项测试？（真实踩过的坑）
      模型写 `SELECT origin_city AS city`，输出列叫 city，
      但带注释的字段是 origin_city（"起点城市"）。
      早期实现只看输出列名 → 取不到注释 → 把 city 误判成"算出来的列"
      → 贴上指标名"完单率"。界面上就出现了「city / 完单率」这种**错误标签**，
      比"没有中文名"更糟 —— 用户会真的以为那列是完单率。

      所以"顺着别名找回真实字段"这一步必须测：
        别名 → 要能找回真实字段
        聚合/算术/函数 → 必须判为"没有来源字段"（否则又会被贴错指标名）
    """
    print()
    print("=" * 78)
    print("第六部分：输出列 → 来源字段（结果列中文名取值的根据）")
    print("=" * 78)

    passed = 0
    for label, sql, expected in SOURCE_FIELD_CASES:
        got = guard.output_source_fields(sql)
        correct = (got == expected)
        if correct:
            passed += 1
        print(f"{'✅' if correct else '❌'} [来源字段] {label:<22} {got}")
        if not correct:
            print(f"        期望：{expected}")

    total = len(SOURCE_FIELD_CASES)
    print("-" * 78)
    print(f"通过：{passed}/{total}")
    return passed == total


# ═══════════════════════════════════════════════════════════
# 第七部分：裸 UNION 改写（ClickHouse 方言要求写全 ALL / DISTINCT）
# ═══════════════════════════════════════════════════════════
UNION_CASES = [
    #   (说明, 输入 SQL, 期望输出)
    ("裸 UNION → UNION ALL", "SELECT 1 UNION SELECT 2", "SELECT 1 UNION ALL SELECT 2"),
    ("多个裸 UNION 全部改写", "SELECT 1 UNION SELECT 2 UNION SELECT 3",
     "SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3"),
    ("UNION ALL 原样保留", "SELECT 1 UNION ALL SELECT 2", "SELECT 1 UNION ALL SELECT 2"),
    ("UNION DISTINCT 原样保留", "SELECT 1 UNION DISTINCT SELECT 2", "SELECT 1 UNION DISTINCT SELECT 2"),
    ("小写 union 也能识别", "select 1 union select 2", "select 1 union ALL select 2"),
    ("跨行裸 UNION", "SELECT 1\nUNION\nSELECT 2", "SELECT 1\nUNION ALL\nSELECT 2"),
    ("字符串字面量 UNION 不动", "SELECT 'UNION' AS x", "SELECT 'UNION' AS x"),
    ("WHERE 里的字符串不动", "SELECT * FROM t WHERE c = 'UNION'", "SELECT * FROM t WHERE c = 'UNION'"),
    ("行注释里的 UNION 不动", "SELECT 1 -- UNION\n", "SELECT 1 -- UNION\n"),
    ("块注释里的 UNION 不动", "SELECT 1 /* UNION */", "SELECT 1 /* UNION */"),
    ("UNIONX 不是关键字", "SELECT col_unionx FROM t", "SELECT col_unionx FROM t"),
]


def run_union_tests():
    """
    跑第七部分：guard.normalize_union（裸 UNION → UNION ALL）

    ★ 为什么要有专项测试？
      ClickHouse 不接受裸 UNION，模型却常只写 UNION，导致 sqlglot 的 CH 方言
      解析失败 → 字段级权限闸门 fail-closed 拒绝（报「字段级权限拦截」）。
      normalize_union 在收尾环节确定性补上 ALL，必须保证：
        裸 UNION 被改写、UNION ALL / DISTINCT 原样保留、
        字符串 / 注释里的 UNION 不被误伤。
    """
    print()
    print("=" * 78)
    print("第七部分：裸 UNION → UNION ALL（ClickHouse 方言要求）")
    print("=" * 78)

    passed = 0
    for label, sql, expected in UNION_CASES:
        got = guard.normalize_union(sql)
        ok = (got == expected)
        if ok:
            passed += 1
        print(f"{'✅' if ok else '❌'} [UNION改写] {label:<24}")
        if not ok:
            print(f"        输入：{sql!r}")
            print(f"        期望：{expected!r}")
            print(f"        实际：{got!r}")

    total = len(UNION_CASES)
    print("-" * 78)
    print(f"通过：{passed}/{total}")
    return passed == total


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print(f"sqlglot 可用：{'✅ 是（走解析器）' if guard._HAS_SQLGLOT else '❌ 否（全走正则兜底）'}")
    print()

    r1 = run_extract_tests()        # 第一部分：表名提取
    r2 = run_check_tests()          # 第二部分：权限校验
    r3 = run_guard_tests()          # 第三部分：护栏与清洗
    r4 = run_limit_tests()          # 第四部分：LIMIT 注入
    r5 = run_qualify_tests()        # 第五部分：库名前缀
    r6 = run_source_field_tests()   # 第六部分：输出列来源字段
    r7 = run_union_tests()          # 第七部分：裸 UNION → UNION ALL

    print()
    print("=" * 78)
    if r1 and r2 and r3 and r4 and r5 and r6 and r7:
        print("🎉 全部通过")
    else:
        print("⚠️ 有失败项，见上方 ❌ 标记")
    print("=" * 78)
    # ★ 失败必须让进程返回非 0（否则上层 runner 会把失败当成功）
    raise SystemExit(0 if (r1 and r2 and r3 and r4 and r5 and r6 and r7) else 1)
