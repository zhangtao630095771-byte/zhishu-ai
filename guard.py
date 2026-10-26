# -*- coding: utf-8 -*-
"""
guard.py —— 安全护栏（企业里唯一不可妥协的一层）
★ 核心思想：模型生成的 SQL 一律视为"不可信输入"，执行前必须过安检
★ 这和你做后端时"用户输入一律不可信"是同一个原则
"""
import re      # 正则表达式工具箱：用来做文本匹配和清洗
#   【SQL 类比】re ≈ SQL 里的 LIKE / REGEXP，但功能强得多
import config  # ★ 读取 TABLE_PARSE_STRICT（解析降级时是否 fail-closed）


# ═══════════════════════════════════════════════════════════
# ★ dateTrunc 自动改写（防御性兜底，不依赖 sqlglot）
# ═══════════════════════════════════════════════════════════
# 【为什么需要？】
#   模型常写出 dateTrunc('MONTH', x)。部分 ClickHouse 版本（如 23.8）只认小写
#   'month' 或干脆只支持 toStartOfMonth(x)，大写 'MONTH' 直接报 code 36
#   (MONTH doesn't look like datepart name)。更严重的是：我们的库宽松能跑通，
#   但用户把页面上的 SQL 复制到别的客户端（如 DBeaver 连的更严实例）就报错，
#   "页面成功、复制出去失败" 就是因为显示的 SQL 带了不兼容写法。
#   所以在"收尾"环节统一改写成 toStartOf*，保证【显示 = 执行 = 复制到别处】三者一致且都安全。
# 【为什么用纯正则 + 括号匹配？】
#   不依赖 sqlglot（有些环境没装），且能正确处理 arg 里的嵌套括号。
_DATEPART_MAP = {
    "MONTH": "toStartOfMonth", "MONTHS": "toStartOfMonth",
    "YEAR": "toStartOfYear", "YEARS": "toStartOfYear",
    "QUARTER": "toStartOfQuarter", "QUARTERS": "toStartOfQuarter",
    "DAY": "toStartOfDay", "DAYS": "toStartOfDay",
    "WEEK": "toStartOfWeek", "WEEKS": "toStartOfWeek",
    "HOUR": "toStartOfHour", "HOURS": "toStartOfHour",
    "MINUTE": "toStartOfMinute", "MINUTES": "toStartOfMinute",
    "SECOND": "toStartOfSecond", "SECONDS": "toStartOfSecond",
}
_DATETRUNC_RE = re.compile(r"dateTrunc", re.IGNORECASE)


def _fix_datetrunc(sql):
    """把 dateTrunc('UNIT', arg) 改写成 toStartOfUnit(arg)，兼容大小写、嵌套括号"""
    out = []          # 分段拼回结果
    i = 0
    n = len(sql)
    while True:
        m = _DATETRUNC_RE.search(sql, i)
        if not m:
            out.append(sql[i:])
            break
        out.append(sql[i:m.start()])              # 保留 dateTrunc 之前的部分
        # 找紧跟的 '('
        j = m.end()
        while j < n and sql[j].isspace():
            j += 1
        if j >= n or sql[j] != "(":
            out.append(sql[m.start():j])          # 不是函数调用，原样保留
            i = j
            continue
        # 括号匹配，定位整个 dateTrunc 调用结束的 ')'
        depth = 0
        k = j
        while k < n:
            if sql[k] == "(":
                depth += 1
            elif sql[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        inner = sql[j + 1:k]                       # 去掉最外层括号后的内容
        # 在 depth==0 处找第一个逗号，分隔 unit 与 arg（unit 是字符串，不会含逗号）
        d = 0
        comma = -1
        for idx, ch in enumerate(inner):
            if ch == "(":
                d += 1
            elif ch == ")":
                d -= 1
            elif ch == "," and d == 0:
                comma = idx
                break
        if comma == -1:
            out.append(sql[m.start():k + 1])       # 形式异常，原样保留
            i = k + 1
            continue
        unit = inner[:comma].strip().strip("'").strip('"').upper()
        arg = inner[comma + 1:].strip()
        fn = _DATEPART_MAP.get(unit)
        if fn:
            out.append(f"{fn}({arg})")             # 改写成 toStartOf* 形式
        else:
            out.append(sql[m.start():k + 1])       # 不认识的 unit 不动
        i = k + 1
    return "".join(out)


# ═══════════════════════════════════════════════════════════
# 第一道：清洗 —— 把 SQL 从模型的一堆废话里抠出来
# ═══════════════════════════════════════════════════════════
def clean_sql(raw):
    """
    模型输出很随性，可能带 ``` 代码块、带分号、带解释文字
    这个函数负责统一清洗成"干净的 SQL 一行/多行文本"
    """
    text = raw.strip()                     # 去掉首尾空白和换行
    #                                        【SQL 类比】≈ TRIM()

    # ① 如果用了 ```sql ... ``` 代码块，把里面的内容抠出来
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    #   re.search(模式, 文本, 标志) = 在文本里找第一个匹配
    #   (?:sql)?     = 可选的 "sql" 字样（?: 表示不捕获这一组）
    #   \s*          = 任意空白字符
    #   (.*?)        = 非贪婪地捕获中间所有内容（这就是我们要的 SQL）
    #   re.DOTALL    = 让 . 也能匹配换行符（不然多行 SQL 抓不全）
    #   re.IGNORECASE= 忽略大小写（```SQL 也能匹配）
    if m:                                  # 找到了代码块
        text = m.group(1).strip()          # group(1) = 第 1 个捕获组的内容
    #   【SQL 类比】≈ REGEXP_SUBSTR 从文本里截出中间那段

    # ② 去掉结尾的分号（统一格式）
    text = text.rstrip(";").strip()
    #   rstrip(";") = 只去掉【右边】的分号
    #   注意不能用 strip(";")，那会把开头的分号也去掉（虽然 SQL 不会以分号开头）

    # ③ 把 dateTrunc('MONTH', x) 等改成 toStartOfMonth(x)（见上方说明，防 code 36 且跨客户端兼容）
    text = _fix_datetrunc(text)

    return text


# ═══════════════════════════════════════════════════════════
# 第二道：安全护栏 —— 只允许只读查询
# ═══════════════════════════════════════════════════════════
FORBIDDEN = [                              # 禁止出现的写操作关键词
    "insert", "update", "delete", "drop", "alter", "create",
    "truncate", "replace", "grant", "revoke", "attach", "vacuum",
]

# ★ 高危子句：它们不一定以写操作关键词开头，但同样能造成破坏/外泄。
#   例：SELECT ... INTO OUTFILE '/tmp/x' 会把查询结果写到服务器文件系统。
#   这里用多词模式匹配（单看 outfile 可能误伤列名，多词组合更精确）。
FORBIDDEN_PATTERNS = [
    (r"\binto\s+outfile\b", "INTO OUTFILE（导出到文件）"),
    (r"\binto\s+dumpfile\b", "INTO DUMPFILE（导出到文件）"),
    (r"\boptimize\s+table\b", "OPTIMIZE TABLE"),
    (r"\bsystem\s+(flush|reload|drop|shutdown|kill|restart)\b", "SYSTEM 管理命令"),
    (r"\brename\s+table\b", "RENAME TABLE"),
    (r"\bexchange\s+tables\b", "EXCHANGE TABLES"),
    # SETTINGS 由系统统一注入资源上限；模型自带 SETTINGS 可能覆盖保护参数
    (r"\bsettings\b", "SETTINGS（执行参数由系统统一控制）"),
]


def sql_guard(sql):
    """
    检查 SQL 是否安全。返回 (是否放行, 原因)
    ★ 不信任模型，只认规则
    """
    # ① 先去掉注释，防止有人用注释藏危险语句
    #    例：SELECT 1; -- 后面偷偷写 DROP TABLE
    no_comment = re.sub(r"--.*", "", sql)                                # 去掉 -- 到行尾
    no_comment = re.sub(r"/\*.*?\*/", "", no_comment, flags=re.DOTALL)   # 去掉 /* ... */
    #            re.sub(模式, 替换成, 文本) = 把匹配到的内容替换掉
    #            【SQL 类比】≈ REGEXP_REPLACE

    cleaned = no_comment.strip()           # 去掉首尾空白

    # ② 空语句直接拒绝
    if not cleaned:                        # 空字符串在 Python 里相当于 False
        return False, "SQL 为空"

    # ③ 必须以只读关键字开头
    first = re.match(r"\(?\s*(select|with)\b", cleaned, re.IGNORECASE)
    #       re.match = 只从【开头】匹配（和 search 不同：search 是全文找）
    #       \(?      = 可能有左括号（有些 SQL 写成 (SELECT ...) ）
    #       \b       = 单词边界，避免匹配到 selected 这种词
    if not first:                          # 开头不是 SELECT / WITH
        return False, f"只允许 SELECT/WITH 开头，实际是：{cleaned[:30]}"

    # ④ 全文扫描禁止关键词（忽略大小写 + 词边界）
    upper = cleaned.upper()                # 统一转大写，避免大小写绕过
    for kw in FORBIDDEN:                   # 逐个检查危险词
        if re.search(rf"\b{kw}\b", upper, re.IGNORECASE):
            #                \b关键词\b = 必须是完整单词
            #                避免把 "deleted_at" 这种字段名误判成 DELETE
            return False, f"检测到禁止的写操作：{kw}"

    # ⑤ 高危子句（导出/系统管理/覆盖资源参数）
    for pattern, label in FORBIDDEN_PATTERNS:
        if re.search(pattern, cleaned, re.IGNORECASE):
            return False, f"检测到高危 SQL 子句：{label}"

    return True, "通过"                    # 全部检查通过

# ★ 补充说明：代码层的护栏是【第二道】防线
#   企业里的【第一道】是数据库账号本身 —— 给 Agent 用的账号必须是只读账号。
#   两道都要有：护栏拦"模型写错"，只读账号拦"护栏被绕过"。


# ═══════════════════════════════════════════════════════════
# 第三道：★ 表级权限校验 —— 只允许查询白名单里的表
# ═══════════════════════════════════════════════════════════
# 【为什么需要这一道？】
#   RAG 层已经做了"看不见"的过滤（第一道过滤），
#   但那是"控制模型看见什么"，属于软约束。
#   如果模型恰好从训练数据里知道一张敏感表的名字（比如 user_phones），
#   它仍有可能凭记忆写出来。
#   → 所以必须在【执行前】做一次硬校验：把 SQL 里的表名抠出来，逐个验权限。
#
# 【为什么从正则换成 SQL 解析器？】
#   正则只会"看到 FROM 后面那串字符"，它不知道 SQL 的语法结构，于是：
#     ① CTE 别名被当成真表   → WITH t AS (...) SELECT * FROM t 里的 t 被误判 → 误拦
#     ② 多语句只看第一段     → SELECT 1; DROP TABLE x 的后半段可能被漏掉   → 漏判
#     ③ 注释里的表名被当真    → -- FROM secret 会被抓出来                  → 误拦
#   换成 sqlglot 后，这些都由语法树天然解决：
#     CTE 名可以从 exp.CTE 节点精确排除，子查询别名根本不是 exp.Table 节点。
#
# 【兜底策略】
#   解析器也可能因方言差异解析失败 —— 此时回退到正则。
#   回退不会比原来更差（原来就只有正则），但会在返回信息里标明用了哪种方式，
#   方便发现"长期在走兜底"这种隐患。
# ═══════════════════════════════════════════════════════════

try:
    import sqlglot                  # 专业 SQL 解析器：把 SQL 文本变成语法树（AST）
    from sqlglot import exp         # exp = expression，语法树里各种节点的类型
    _HAS_SQLGLOT = True             # 标记解析器可用
except ImportError:                 # 没装 sqlglot 也不让程序崩
    _HAS_SQLGLOT = False            # 标记只够用正则兜底

# 【为什么要 try/except 包住 import？】
#   护栏是"最后一道防线"，不能因为缺一个可选依赖就让整个系统起不来。
#   降级到正则虽然弱一些，但仍能拦住绝大多数越权。


# ── 正则兜底用的两个模式 ──
# 匹配 FROM / JOIN 后面跟着的表名
#   \b(?:from|join)\s+  → 单词边界的 from 或 join，后面跟空白（?: 表示不捕获这一组）
#   ([a-zA-Z_][\w.]*)   → 表名：字母或下划线开头，后面可跟字母数字下划线或点（库.表）
_TABLE_PATTERN = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)

# 匹配 CTE 定义里的名字（用来在兜底时排除别名）
#   (?:\bwith\b|,)\s+  → "with" 开头，或者逗号分隔的后续 CTE
#   ([a-zA-Z_]\w*)\s+as\s*\(  → 名字后面跟 AS (
_CTE_PATTERN = re.compile(r"(?:\bwith\b|,)\s+([a-zA-Z_]\w*)\s+as\s*\(", re.IGNORECASE)


def _dialects_to_try(dialect):
    """
    生成"依次尝试"的方言列表

    ★ 为什么要有这个？
      同一段 SQL 在不同方言下可能有不同的解析结果或直接解析失败。
      比如 ClickHouse 的 countIf()、MySQL 的反引号，用 SQLite 方言去解析就可能报错。
      先试配置里的方言（最匹配），失败再试几个常见方言 —— 只要能解析出语法树，
      提取表名的结果就是可信的。
    """
    names = []                              # 待尝试的方言名列表
    if dialect:                             # 有指定方言就先试它
        names.append(str(dialect).lower())
    for d in ("sqlite", "clickhouse", "mysql", "postgres", "duckdb", "hive"):
        #       这几个覆盖了绝大多数企业数仓会用到的语法
        if d not in names:                  # 避免重复
            names.append(d)
    return names


def _default_dialect():
    """
    从 config 里拿当前数据源的方言

    ★ 这里用"延迟导入"（函数内部才 import config），而不是在文件顶部 import。
      因为 guard 是被 agent / server 间接依赖的底层模块，
      放函数里导入可以避免模块加载顺序引起的循环依赖问题。
    """
    try:
        import config
        return getattr(config, "SQL_DIALECT", None)     # 取不到就返回 None
    except Exception:
        return None                                     # 拿不到方言也没关系，后面会依次试


def _extract_by_parser(sql, dialect):
    """
    用 sqlglot 解析 SQL，从语法树里提取真实表名

    返回：(表名集合, 实际成功的方言名)
    全部方言都解析失败时，抛出最后一个异常
    """
    last_error = None                                       # 记住最后一次的失败原因
    for name in _dialects_to_try(dialect):                  # 挨个方言尝试
        try:
            statements = sqlglot.parse(sql, read=name)
            #              parse = 解析【全部语句】，返回语句列表
            #              ★ 注意用 parse 而不是 parse_one：
            #                parse_one 只解析第一条语句，
            #                "SELECT 1; DROP TABLE x" 的后半段就看不见了 —— 这是安全隐患
        except Exception as e:
            last_error = e                                  # 记下失败原因，继续试下一个方言
            continue

        tables = set()                                      # 收集表名（集合自带去重）
        for stmt in statements:                             # 遍历每一条语句
            if stmt is None:                                # 空语句（比如结尾多个分号）跳过
                continue

            cte_names = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}
            #             find_all(类型) = 找出语法树里所有该类型的节点
            #             alias_or_name  = CTE 定义的名字
            #             ★ 这是关键一步：CTE 名不是真表，稍后要排除掉
            #               这就是正则做不到的事 —— 正则只看到 "FROM t"，无从判断 t 是别名

            for node in stmt.find_all(exp.Table):           # 找出所有"表引用"节点
                name_only = node.name                       # .name 只取表名，自动去掉库名前缀
                #             ★ 比如 demo.dwd_order_all → name 是 "dwd_order_all"
                #               所以不需要像正则那样手工 split(".")
                if not name_only:                           # 理论上不会为空，防御性判断
                    continue
                low = name_only.lower()                     # 统一小写（表名大小写不敏感）
                if low in cte_names:                        # 是 CTE 别名，不是真表
                    continue                                # ★ 排除，避免误拦
                tables.add(low)                             # 收进结果

        return tables, name                                 # 解析成功，返回结果

    raise last_error                                        # 所有方言都失败，把错误抛出去


def _extract_by_regex(sql):
    """
    正则兜底：解析器不可用或解析失败时使用

    ★ 比原来的实现多了一步"排除 CTE 别名"，
      因为 CTE 别名会导致合法 SQL 被误拦（这是原来最大的痛点）。
    """
    found = _TABLE_PATTERN.findall(sql)                     # 抓 FROM/JOIN 后面的词
    cte_names = {c.lower() for c in _CTE_PATTERN.findall(sql)}
    #            先把 CTE 名字收集起来，等下排除

    tables = set()                                          # 结果集合
    for t in found:                                         # 逐个处理抓到的词
        t = t.strip()                                       # 去掉可能的空白
        if "." in t:                                        # 带库名前缀（db.table）
            t = t.split(".")[-1]                            #  只取最后一段（表名）
        low = t.lower()
        if low and low not in cte_names:                    # 非空、且不是 CTE 别名
            tables.add(low)                                 # 收进结果
    return tables


def extract_tables(sql):
    """
    从 SQL 里提取所有被引用的真实表名（去重）

    ★ 优先用 SQL 解析器（准确），解析不了才回退正则（兜底）。
    ★ 返回集合（set）：权限判断只关心"在不在里面"，
      集合查找是 O(1)，列表是 O(n)。

    ★ 注意：这个函数只返回"提取到什么"，不表达"提取是否可信"。
      安全判定请用 extract_tables_ex() / check_tables_ex()，
      否则"解析失败 + 空集合"会被误当成"没有引用任何表"而放行。
    """
    return extract_tables_ex(sql)["tables"]


def extract_tables_ex(sql, dialect=None):
    """
    提取表名，并明确返回【可信度状态】—— 供 fail-closed 判定使用。

    返回 dict：
        tables  : set[str]                 提取到的表名（小写）
        status  : "parsed" | "degraded" | "failed"
                  parsed   = SQL 解析器成功，结果可信
                  degraded = 解析器失败/不可用，退回正则，结果只能算"尽力而为"
                  failed   = 降级后仍一张表都没识别出来，结果不可用
        method  : 人可读的方式说明（"解析器[clickhouse]" / "正则兜底(...)"）
        reason  : 失败/降级原因（parsed 时为空）

    ★ 为什么必须区分这三态？
      旧实现只返回 (表名集合, 说明字符串)，调用方看到空集合时无法判断
      是"SQL 真的没引用表"（如 SELECT 1），还是"解析失败没认出来"。
      后者一旦按空集合放行，就是权限校验 fail-open（已实测可绕过）。
    """
    if _HAS_SQLGLOT:                                        # 解析器可用
        try:
            tables, used = _extract_by_parser(sql, dialect or _default_dialect())
            return {"tables": tables, "status": "parsed",
                    "method": f"解析器[{used}]", "reason": ""}
        except Exception as e:                              # 解析失败（语法错误 / 方言不认识）
            reason = str(e).split("\n")[0][:120]            # 只取第一行、截断，方便看
            tables = _extract_by_regex(sql)
            # ★ 正则抓到表 ≠ 结果可信：正则不认反引号/双引号/表函数，
            #   可能只抓到一部分。标 degraded，由调用方按策略决定是否放行。
            return {"tables": tables,
                    "status": "degraded" if tables else "failed",
                    "method": f"正则兜底({reason})", "reason": reason}

    tables = _extract_by_regex(sql)
    return {"tables": tables,
            "status": "degraded" if tables else "failed",
            "method": "正则兜底(sqlglot 未安装)",
            "reason": "sqlglot 未安装"}


def extract_tables_detail(sql, dialect=None):
    """
    兼容旧调用：返回 (表名集合, 方式说明)。
    ★ 新代码请用 extract_tables_ex()，它能区分"解析成功 / 降级 / 失败"。
    """
    r = extract_tables_ex(sql, dialect)
    return r["tables"], r["method"]


def check_tables_ex(sql, allowed_tables, strict=None):
    """
    表权限校验（带可信度状态，fail-closed）。

    返回 dict：
        ok      : bool
        illegal : list[str]     越权表名
        status  : parsed / degraded / failed
        method  : 提取方式
        reason  : 降级/失败原因
        message : 可直接展示给用户的错误说明（ok=True 时为空）

    ★ 策略：
      1) 解析成功（parsed）→ 按白名单严格比对；
      2) 解析降级（degraded）→ 生产环境默认拒绝（strict=True）：
         正则可能漏掉反引号/双引号/表函数里的表，漏掉就等于放行未授权表；
      3) 解析失败（failed）→ 一律拒绝，绝不把"没认出来"当成"没有表"。

    strict 默认取 config.TABLE_PARSE_STRICT（生产建议 True）。
    """
    if strict is None:
        strict = getattr(config, "TABLE_PARSE_STRICT", True)

    allowed_set = {t.lower() for t in allowed_tables}
    r = extract_tables_ex(sql)
    used = r["tables"]

    if r["status"] == "failed":
        msg = ("无法可靠解析 SQL 引用的表（解析失败，正则也未识别到表），"
               "已按安全策略拒绝执行")
        return {"ok": False, "illegal": [], "status": r["status"],
                "method": r["method"], "reason": r["reason"], "message": msg}

    if r["status"] == "degraded" and strict:
        msg = (f"SQL 解析器不可用，正则兜底结果不可信（{r['reason'][:80]}），"
               "已按安全策略拒绝执行")
        return {"ok": False, "illegal": [], "status": r["status"],
                "method": r["method"], "reason": r["reason"], "message": msg}

    illegal = sorted(used - allowed_set)
    if illegal:
        return {"ok": False, "illegal": illegal, "status": r["status"],
                "method": r["method"], "reason": r["reason"],
                "message": f"SQL 引用了未授权的表：{illegal}"}

    return {"ok": True, "illegal": [], "status": r["status"],
            "method": r["method"], "reason": r["reason"], "message": ""}


def check_tables(sql, allowed_tables):
    """
    兼容旧调用：返回 (是否放行, 越权表名列表)。
    ★ 新代码请用 check_tables_ex()，以便拿到 fail-closed 的具体原因。
    """
    r = check_tables_ex(sql, allowed_tables)
    if r["ok"]:
        return True, []
    return False, (r["illegal"] or [r["message"]])


def format_sql(sql):
    """
    把 SQL 排版成多行，方便人读

    ★ 为什么需要这个？
      模型生成的 SQL 是一整行，几百个字符横着排，得左右拉着看，
      WHERE 里到底过滤了什么很容易看漏。排版后每个子句一行，一眼看清。

    ★★ 只用于【展示】，执行永远用原始 SQL。
      虽然 sqlglot 的 pretty 输出语义等价，但没必要冒任何风险 ——
      "展示归展示、执行归执行" 是最省心的边界。
      （sqlglot 真出问题时，错的只是界面显示，不会让查询结果出错）

    ★ 解析失败就原样返回：排版只是锦上添花，
      绝不能因为它影响主流程，更不能让用户看不到 SQL。
    """
    if not _HAS_SQLGLOT or not (sql or "").strip():
        return sql                                     # 没装解析器 / 空字符串 → 原样返回

    for name in _dialects_to_try(_default_dialect()):
        try:
            out = sqlglot.transpile(sql, read=name, write=name, pretty=True)
            #   read == write：只做"排版"，不翻译成别的方言（避免语义被改写）
            if out and out[0].strip():
                # ★ 关键：sqlglot 重排可能把 toStartOfMonth(x) 又渲染成 dateTrunc('month', x)，
                #   而展示/复制用的正是 format_sql 结果（见 server.py: sql_formatted），
                #   会导致「执行成功、复制出去到严格实例报 code 36」。所以重排后再过一遍改写，
                #   保证【展示 SQL = 执行 SQL】二者一致且都安全。
                return _fix_datetrunc(out[0])
        except Exception:
            continue                                   # 这个方言解析不了，换下一个

    return sql                                         # 全都失败 → 原样返回（绝不报错）


def qualify_tables(sql, database):
    """
    给 SQL 里的表名补上库名前缀

    例：dwd_tu_order_all → ctc_data_warehouse.dwd_tu_order_all

    ★ 为什么要做这一步？
      公司规范要求 SQL 里的表名写全（库.表），一眼能看出数据来自哪个库。
      虽然连接时已经指定了默认库（不写前缀也能跑），
      但"写全"能让 SQL 脱离当前连接依然可读、可复制到其它工具里直接跑。

    ★ 为什么用解析器改，而不是字符串替换？
      字符串替换会误伤同类文本：CTE 名、表别名、字符串常量里的同名内容都会被改坏。
      在语法树上改，才能准确区分"真实表引用"和"CTE 引用"。

    ★ 四种情况必须跳过（否则会改错）：
      ① 已经有库名前缀的（含 system.xxx）—— 再补一层变成 a.b.c，表就找不到了
      ② CTE 名（WITH t AS (...) 里的 t 是临时结果集，不是真实表）
      ③ 表函数（numbers(10) 之类，压根没有表名）
      ④ 解析不了的 SQL —— 原样返回

    ★ 没做任何改动就返回【原 SQL】，不返回重新生成的：
      解析器重新生成 SQL 时可能顺手改掉格式（加引号、改缩进），
      没必要的改动就是没必要引入的风险。"能不动就不动"。
    """
    if not _HAS_SQLGLOT or not database:
        return sql                                     # 没解析器 / 没库名 → 原样返回
    if not (sql or "").strip():
        return sql

    for name in _dialects_to_try(_default_dialect()):
        try:
            tree = sqlglot.parse_one(sql, read=name)   # 单语句解析
        except Exception:
            continue                                   # 这个方言解析不了，换下一个
        if tree is None:
            continue

        cte_names = {c.alias_or_name for c in tree.find_all(exp.CTE) if c.alias_or_name}

        changed = False
        for node in tree.find_all(exp.Table):
            if not node.name:                          # ③ 表函数：没有表名
                continue
            if node.db:                                # ① 已有库名前缀 / system.xxx
                continue
            if node.name in cte_names:                 # ② CTE 引用，不是真实表
                continue
            node.set("db", exp.to_identifier(database))
            changed = True

        if not changed:
            return sql                                 # 没改动 → 原样返回，避免被解析器重排格式

        try:
            # ★ 关键：sqlglot 用 ClickHouse 方言【重新生成】SQL 时，会把 toStartOfMonth(x)
            #   渲染成 dateTrunc('month', x)，而本库 CH 23.8 不认 dateTrunc → code 36。
            #   这里必须再过一遍 _fix_datetrunc，保证【执行用的 SQL】安全。
            #   （format_sql 只修了"展示用"的 SQL，所以曾出现"展示正确、执行报 code 36"的诡异现象。）
            return _fix_datetrunc(tree.sql(dialect=name))
        except Exception:
            return sql                                 # 生成失败就放弃，别让查询挂掉

    return sql                                         # 所有方言都解析不了 → 原样返回


def output_source_fields(sql):
    """
    解析 SQL 的 SELECT 列表，得到 {输出列名: 来源字段名}

    ★ 为什么需要这一步？（真实踩过的坑）
      模型的 SQL 常写成 `SELECT origin_city AS city, ...`：
        输出列叫 city，但【真正带注释的字段是 origin_city】。
      只看输出列名，既取不到注释，还会把这一列误判成"算出来的列"，
      给它贴上指标名 —— 截图上就出现了「city」被标成「完单率」，
      错得比"没有中文名"更糟：用户会以为这一列是完单率。

    返回：{"city": "origin_city", "finish_rate": None}
          None 表示这一列是算出来的（聚合/算术/函数），没有单一来源字段
    """
    if not _HAS_SQLGLOT or not (sql or "").strip():
        return {}

    for name in _dialects_to_try(_default_dialect()):
        try:
            tree = sqlglot.parse_one(sql, read=name)
        except Exception:
            continue                                   # 换个方言再试
        if tree is None:
            continue

        # 最外层的 SELECT 才定义"结果有哪几列"，取 DFS 遇到的第一个
        top = next(tree.find_all(exp.Select), None)
        if top is None:
            return {}

        mapping = {}
        for proj in top.expressions:                   # 遍历每个输出项
            alias = proj.alias_or_name                 # 输出列名（有 AS 就是别名，没有就是原名）
            if not alias:
                continue
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            #   ★ 只有"纯字段引用"才算有来源字段；
            #     聚合(count/sum)、算术、函数(COALESCE) 一律算"算出来的列"
            mapping[alias] = inner.name if isinstance(inner, exp.Column) else None
        return mapping

    return {}                                          # 全解析不了 → 返回空（调用方按列名兜底）


def output_aliases(sql):
    """
    按 SELECT 顺序返回每个输出列的别名/列名（与结果集的列一一按位置对齐）

    ★ 为什么要它？（规则18 之后的新需求）
      规则18 要求模型给每个输出列都加反引号中文别名（`取消率（订单量）`）。
      这个别名是模型为【这次查询】专门起的，比字段注释/指标名更贴合本次语义，
      结果表头应该优先显示它。
      ClickHouse 返回的列名本身就是别名，但经过 guard 改写 / sqlglot 美化后
      可能对不上，所以这里直接从 SQL 解析，按位置对齐最稳。

    返回：["city", "取消率（订单量）", ...]（与 SELECT 输出项顺序一致；
          个别输出项解析不出名字时对应位置为 None，保持位置对齐）
    """
    if not _HAS_SQLGLOT or not (sql or "").strip():
        return []
    for name in _dialects_to_try(_default_dialect()):
        try:
            tree = sqlglot.parse_one(sql, read=name)
        except Exception:
            continue
        if tree is None:
            continue
        top = next(tree.find_all(exp.Select), None)
        if top is None:
            return []
        return [(p.alias_or_name or None) for p in top.expressions]
    return []


def extract_columns(sql):
    """
    解析 SQL 里用到的【所有字段名】（SELECT 列 + WHERE / JOIN / GROUP BY 里的）

    ★ 和 output_source_fields 的区别：
      output_source_fields 只关心"结果有哪几列"（给结果表头用）；
      这个关心"这条 SQL 碰了哪些字段"（给优化建议用）。

    ★ 为什么需要它？
      答错的往往不是输出列，而是【WHERE 里用错的那个字段】——
      例：把「车拖车」写成 `WHERE company_id = 'ctc'`，
      company_id 只出现在条件里，不是输出列。
      只分析输出列，就看不到"错在用错了过滤字段"这个关键事实。

    返回：[字段名, ...]（已去重，保持出现顺序）
    """
    if not _HAS_SQLGLOT or not (sql or "").strip():
        return []
    for name in _dialects_to_try(_default_dialect()):
        try:
            tree = sqlglot.parse_one(sql, read=name)
        except Exception:
            continue                                   # 换个方言再试
        if tree is None:
            continue
        cols = []
        for c in tree.find_all(exp.Column):
            n = c.name
            if n and n not in cols:
                cols.append(n)
        return cols
    return []
