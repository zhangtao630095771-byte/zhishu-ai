# -*- coding: utf-8 -*-
"""
db_clickhouse.py —— ClickHouse 数据源实现（生产用）
★ 和 db_sqlite.py 提供完全相同的三个函数，所以上层代码一行不用改
★ 只需在 .env 里把 DB_TYPE 改成 clickhouse，整个系统就切到真实库

⚠️ 生产环境必须做到：
   ① 用【只读账号】（只有 SELECT 权限，且只授权白名单表）
   ② 服务端设置查询超时（max_execution_time），防止大查询拖垮集群
   ③ 应用层强制加 LIMIT，防止 SELECT * 拉全表
"""
import re                        # 正则（判断 SQL 里是否已有 LIMIT）
import threading                 # ★ 线程本地连接（每线程一条长连接，互不共享）
import time                      # ★ 连接空闲计时

import clickhouse_connect     # ClickHouse 官方 Python 客户端
import config                 # 配置模块


# ═══════════════════════════════════════════════════════════
# 内部工具：连接管理
# ═══════════════════════════════════════════════════════════
#
# ★★ 为什么不再"每次新建连接"？（实测数据，2026-10-30）
#
#   原来的实现：每个函数 `client = _client()` 用完 `client.close()`。
#   注释当时写的是"适合低频取数场景"，但实测把这个假设推翻了：
#
#       新建连接      8.3 秒   （127.0.0.1:8123，经 SSH 隧道）
#       复用连接查询  0.05 秒  （同一条 SQL：0.08 秒）
#       ↓
#       一次取数的 execute 阶段实测 5.8 秒，其中 SQL 只占 0.08 秒 ——
#       **连接开销是查询本身的 70 倍**。
#
#   根因不是 ClickHouse 慢，而是建连接要走隧道握手 + 拉服务端设置，
#   在隧道场景下固定要几秒。取数本身几十毫秒，被连接开销完全淹没。
#
# ★ 为什么用【线程本地】而不是全局共享一条？
#   服务端是 FastAPI + asyncio.to_thread，一个请求跑在一个线程里；
#   多个取数请求会并发。clickhouse_connect 的客户端虽然多数操作线程安全，
#   但"共享一条连接 + 一个线程在 close"这种组合仍然容易踩坑。
#   每线程一条，既拿到复用收益，又不需要考虑跨线程同步。
#
# ★ 为什么必须"出错自动重连"？
#   连的是本地 SSH 隧道（ssh_tunnel.ensure_tunnel 会在端口不通时重建）。
#   隧道一重建，长连接就废了 —— 若没有重连，第一句话就 500。
#   所以：连接类错误 → 丢弃旧连接 → 重连 → 重试一次（只一次，避免雪崩）。
#
# ★ 为什么不用 ping 探活？
#   每次查询前 ping 一下省不了时间（多一次往返 ~50ms），
#   而"出错再重连"的代价只有失败那一次。少一次往返更划算。
_LOCAL = threading.local()
_STAT = {"created": 0, "reused": 0, "reconnect": 0}      # 可观测：连接生命周期


def _new_client():
    """真正建立一条新连接（唯一创建点）"""
    return clickhouse_connect.get_client(
        host=config.CK_HOST,             # 主机
        port=config.CK_PORT,             # 端口（8123 = HTTP 接口）
        username=config.CK_USER,         # ★ 必须是只读账号，不要用 default
        password=config.CK_PASSWORD,
        database=config.CK_DATABASE,
        settings={
            # ★ 服务端超时：超过这个秒数，ClickHouse 会主动杀掉查询
            #   这是防止"一句自然语言拖垮整个集群"的关键保险
            "max_execution_time": config.CK_MAX_EXECUTION_SEC,
            # ★ 限制返回行数（服务端侧再上一道保险）
            "max_result_rows": config.CK_MAX_RESULT_ROWS,
        },
    )


def _drop_local():
    """丢弃当前线程的连接（不抛异常：清理失败不该影响主流程）"""
    c = getattr(_LOCAL, "client", None)
    _LOCAL.client = None
    _LOCAL.at = 0.0
    if c is not None:
        try:
            c.close()
        except Exception:
            pass


def _shared_client():
    """取当前线程的连接；没有/过旧/开关关闭 → 新建。

    过旧 = 空闲超过 CK_CONN_MAX_AGE 秒。为什么要上限？
      隧道可能被中间设备掐掉空闲连接，TCP 层已经死了但对象还活着。
      有上限可以让"长时间不用后的第一条查询"走新连接，少一次失败。
    """
    if not getattr(config, "CK_CONN_REUSE", True):
        _STAT["created"] += 1
        return _new_client()

    c = getattr(_LOCAL, "client", None)
    at = getattr(_LOCAL, "at", 0.0)
    max_age = getattr(config, "CK_CONN_MAX_AGE", 600)
    if c is not None and (time.time() - at) < max_age:
        _STAT["reused"] += 1
        return c
    if c is not None:
        _drop_local()
    _LOCAL.client = _new_client()
    _LOCAL.at = time.time()
    _STAT["created"] += 1
    return _LOCAL.client


def _client():
    """兼容旧调用名（等价于 _shared_client）。新代码请直接用 _shared_client。"""
    return _shared_client()


# 连接类错误的关键词：只对这些错误重连重试。
#   ★ 绝不把"服务端报错"（语法错/权限不足/超时被 kill）也算进来 ——
#     那些重试没有意义，只会把一次失败变成两次等待。
_CONN_ERROR_HINTS = ("connection", "connect", "reset", "refused", "broken pipe",
                     "remote end closed", "network", "unreachable", "getaddrinfo",
                     "timed out", "timeout")


def _is_conn_error(e):
    """是否属于"连接断了"这类可重连的错误"""
    if isinstance(e, (ConnectionError, TimeoutError)):
        return True
    name = type(e).__name__.lower()
    msg = str(e).lower()
    if "timeoutexceeded" in name or "timeout exceeded" in msg:
        return False          # 服务端执行超时：重试只会再等一遍
    return any(k in name or k in msg for k in _CONN_ERROR_HINTS)


def _run(sql, parameters=None):
    """★ 所有 ClickHouse 查询的统一入口：复用连接 + 断线重连（一次）。

    返回 clickhouse_connect 的 QueryResult（调用方自己取 .result_rows / .column_names）。
    """
    client = _shared_client()
    try:
        return client.query(sql, parameters=parameters)
    except Exception as e:
        if not _is_conn_error(e):
            raise
        # 连接已废（典型场景：SSH 隧道重建）→ 丢掉重连，再试一次
        _STAT["reconnect"] += 1
        _drop_local()
        return _shared_client().query(sql, parameters=parameters)


def close_all():
    """关闭当前线程的连接（测试/重启/切换数据源时用）"""
    _drop_local()


def conn_stats():
    """连接统计（排障用：能看出复用是否生效）"""
    return dict(_STAT)


# ═══════════════════════════════════════════════════════════
# ① 建演示库 —— 对 ClickHouse 无意义（真实库早就在了）
# ═══════════════════════════════════════════════════════════
def create_demo_db():
    """
    ClickHouse 连的是公司真实数据仓库，不需要（也不应该）建表
    ★ 这里做成空操作，保证上层调用 create_demo_db() 时不会报错
    """
    print(f"[ClickHouse] 跳过建表，使用真实库：{config.CK_DATABASE}")


# ═══════════════════════════════════════════════════════════
# ② ★ 抓表结构 —— 唯一需要按数据库定制的地方
# ═══════════════════════════════════════════════════════════
def _query_columns(db, batch):
    """查一批表的字段，返回 [(表名, 字段名, 类型), ...]"""
    quoted = ",".join(f"'{t}'" for t in batch)  # 表名来自 system.tables，不是用户输入
    sql = ("SELECT table, name, type FROM system.columns "
           f"WHERE database = '{db}' AND table IN ({quoted}) "
           "ORDER BY table, position")
    return _run(sql).result_rows


def fetch_schema():
    """
    从 ClickHouse 抓取全部表结构

    返回：[(表名, 字段名, 类型), ...]   ← 和 SQLite 版本格式完全一致

    ★ 为什么要"分批查"，而不是"逐表查"或"一次全查"？
      最早是逐表查（741 张表 = 741 次往返），在 SSH 隧道下实测要 40 秒以上 ——
      每次重建索引都等这么久，根本没心思调知识库。
      但也不能"一次全查完"：连接上设了 max_result_rows = 10000，
      而整库字段有 18000+ 行，一次查会被 ClickHouse 直接杀掉。
      ↓
      按【行预算】分批：攒够 8000 行就发一批（留 2000 余量），
      741 张表大约分成 40 批 → 往返次数降到 1/20，又快又不超限。
    """
    db = config.CK_DATABASE                     # 库名（来自配置，是我们自己填的，可信）

    # ── 第一步：列出这个库里的所有表 ──
    #   system.tables 是 ClickHouse 的系统表，相当于 SQLite 的 sqlite_master
    #   ★ 这里直接拼字符串而不是用参数绑定，因为 db 来自我们自己的配置（不是用户输入），
    #     不存在注入风险。（用户输入绝不允许这样拼！）
    tables = [r[0] for r in _run(
        f"SELECT name FROM system.tables WHERE database = '{db}'"
    ).result_rows]

    # ── 第二步：拿到每张表的字段数，用来算分批预算 ──
    #   （只返回"每表一行"的聚合结果，几百行，很轻）
    counts = {r[0]: r[1] for r in _run(
        "SELECT table, count() FROM system.columns "
        f"WHERE database = '{db}' GROUP BY table"
    ).result_rows}

    # ── 第三步：按行预算分批抓字段 ──
    ROW_BUDGET = 8000                           # 留 2000 余量，别顶到 max_result_rows
    rows, batch, used = [], [], 0
    for t in tables:
        n = counts.get(t, 0)
        if batch and used + n > ROW_BUDGET:     # 这批塞不下了 → 先发一批
            rows.extend(_query_columns(db, batch))
            batch, used = [], 0
        batch.append(t)
        used += n
    if batch:                                   # 最后一批
        rows.extend(_query_columns(db, batch))

    print(f"[ClickHouse] 从 {db} 抓取到 {len(tables)} 张表、{len(rows)} 个字段")
    return rows


def list_tables():
    """
    只列出当前库的全部表名（一次查询，不去逐表拉字段）

    返回：[表名, ...]
    """
    db = config.CK_DATABASE
    rows = _run(
        f"SELECT name FROM system.tables WHERE database = '{db}'"
    ).result_rows
    return [r[0] for r in rows]


def list_columns(table_name):
    """
    只列出【一张表】的字段名和类型（给 RAG 知识库页面的字段编辑用）

    返回：[(字段名, 类型), ...]，按字段在表里的自然顺序（position）

    ★ 用 system.columns 而不是 fetch_schema：
      system.columns 带 position 列，能保证顺序和建表时一致；
      而且只捞一张表（几十行），比拉全库 18025 行快得多。
    """
    db = config.CK_DATABASE
    rows = _run(
        "SELECT name, type FROM system.columns "
        f"WHERE database = '{db}' AND table = '{table_name}' "
        "ORDER BY position"
    ).result_rows
    return [(r[0], r[1]) for r in rows]


def list_column_stats():
    """
    按表统计字段数量 + 有多少字段带数据库注释

    返回：{表名: {"total": n, "db_commented": m}}

    ★ 为什么需要这个？
      "RAG 知识库"页面要显示"字段注释覆盖了多少"。
      如果为此去拉全库字段明细（本库 18025 行），
      在 SSH 隧道下实测要 40 秒以上 —— 页面根本没法用。
      而这条 GROUP BY 聚合查询只返回几百行（每表一行），快到可以忽略。
      —— 能算的要让数据库算，别把明细捞到 Python 里再统计。
    """
    db = config.CK_DATABASE
    rows = _run(
        "SELECT table, count() AS total, countIf(comment != '') AS commented "
        f"FROM system.columns WHERE database = '{db}' GROUP BY table"
    ).result_rows
    return {r[0]: {"total": r[1], "db_commented": r[2]} for r in rows}


# ═══════════════════════════════════════════════════════════
# ②-b ★ 抓注释 —— 真实数仓最值钱的一步
# ═══════════════════════════════════════════════════════════
# 【为什么这一步对真实环境至关重要？】
#   在演示库上，我们只有 4 张表，注释可以手写进 config_store。
#   但真实数仓有几百张表、几千个字段 —— 手写注释是不可能的。
#
#   ClickHouse 的 system 表里【本来就存着注释】：
#     system.tables.comment   ← 建表时 COMMENT '订单事实表' 写的
#     system.columns.comment  ← 建表时 COMMENT '订单金额' 写的
#   直接把它们抓下来，知识库就"冷启动零成本"了。
#
# 【还记得我们踩过的那个坑吗？】
#   第 8 步时，模型把"完单量"写成 status=7，根因就是它不知道各状态码的含义。
#   后来我们在 demo 库的 status 字段上写了注释
#   "状态：1待接单 2已接单 3已支付 6已完成 7已取消"，准确率立刻上去了。
#   → 字段注释是 text-to-SQL 准确率最直接的杠杆，而真实库里通常已经有了！
# ═══════════════════════════════════════════════════════════
def fetch_comments(table_names=None):
    """
    从 ClickHouse 自动抓取表注释和字段注释

    参数 table_names：
        传了列表 → 只抓这些表的注释（★ 推荐）
        None     → 抓整库（数据量大时可能超限，慎用）

    返回：{"tables": {表名: 注释}, "columns": {"表名.字段名": 注释}}

    ★ 注意：这是"自动发现"的注释，优先级【低于】config_store 里人工维护的注释。
      逻辑在 rag.py：人工注释 > 数据库注释 > 表名兜底

    ★★ 为什么必须支持按表收窄？（这里踩过一个很隐蔽的坑）
      连接上设了 max_result_rows = 10000（防止大结果集拖垮集群）。
      而"整库字段注释"一次要返回 16242 行 —— 直接超限，被 ClickHouse
      以 code 396 杀掉。更糟的是这个异常被下面的 except 吞掉，
      只打印一句警告，于是【整库 16242 条字段注释被静默丢弃】，
      模型从来没看到过它们（比如 dwd_tu_order_all.id 的注释"订单id"）。
      而调用方（建索引 / 知识库页面）其实只关心【可见的那几张表】，
      收窄范围后只有几百行，既不超限，又快得多。
      —— 教训：别为了"一次查完"去拉远超需要的行数。
    """
    db = config.CK_DATABASE                     # 库名（来自我们自己的配置，可信）

    tables, columns = {}, {}                    # 两个空字典收集结果

    # 按表收窄时的过滤条件
    #   ★ 这里直接拼字符串：表名来自 system.tables（我们自己的库），不是用户输入
    if table_names:
        quoted = ",".join(f"'{t}'" for t in table_names)
        col_filter = f" AND table IN ({quoted})"
        tbl_filter = f" AND name IN ({quoted})"
    else:
        col_filter = tbl_filter = ""

    try:
        # ── 表注释 ──
        #   ★ 在 SQL 里就过滤掉空注释：本库 741 张表里只有 346 张有注释，
        #     不过滤的话白捞几百行空字符串回来，还得在 Python 里再筛一遍。
        t_sql = (f"SELECT name, comment FROM system.tables "
                 f"WHERE database = '{db}' AND comment != ''{tbl_filter}")
        for name, comment in _run(t_sql).result_rows:
            tables[name] = comment.strip()

        # ── 字段注释 ──
        #   ★ 一定要带 comment != '' 和按表过滤：
        #     整库字段注释有 16000+ 行，超过连接的 max_result_rows(10000) 会被杀掉
        c_sql = (
            "SELECT table, name, comment FROM system.columns "
            f"WHERE database = '{db}' AND comment != ''{col_filter}"
        )
        for tbl, col, comment in _run(c_sql).result_rows:
            columns[f"{tbl}.{col}"] = comment.strip()
            #   ★ 用 "表名.字段名" 做键，避免不同表里同名字段互相覆盖
            #     （比如 dwd_order_all.city 和 dim_driver.city 是两个不同的注释）

    except Exception as e:
        # ── 降级 ──
        # 老版本 ClickHouse 的 system.tables / system.columns 可能没有 comment 字段，
        # 或结果集超过 max_result_rows。这时不能崩 —— 注释只是"锦上添花"，
        # 没有它系统照样跑，只是知识库质量差一点（需要人工补注释）。
        # ⚠️ 但一定要打印【完整错误】：这个 except 曾经吞掉过一个真 bug
        #    （code 396 结果集超限），导致整库 16242 条字段注释被静默丢弃，
        #    模型从来没看到过它们，而且没人发现。降级 ≠ 假装没事。
        print(f"⚠️ [ClickHouse] 抓取注释失败（{type(e).__name__}）：{e}")
        print("   → 不影响运行，但知识库质量会下降，请排查上面这条错误")

    print(f"[ClickHouse] 自动发现注释：{len(tables)} 条表注释、{len(columns)} 条字段注释")
    return {"tables": tables, "columns": columns}


def list_dict_types():
    """
    列出【数据字典源表】里有哪些字典类型（供"从数据库导入"页面选择）

    返回：[{"dict_type": "app_entity_source", "count": 12, "sample": "车拖车 / 慧拉车 / ..."}, ...]

    ★ 为什么不把取值也一起返回？
      99 个字典类型的全部取值加起来有近千条，一次全拉回来没必要。
      先列类型（每类一行，很少），用户选中哪一个再去取它的取值 —— 按需加载。
    """
    rows = _run(
        "SELECT dict_type, count() AS n, "
        "arrayStringConcat(arraySlice(groupArray(dict_label), 1, 4), ' / ') AS sample "
        f"FROM {config.DICT_TABLE} "
        "WHERE dict_type != '' GROUP BY dict_type ORDER BY n DESC"
    ).result_rows
    return [{"dict_type": r[0], "count": r[1], "sample": r[2]} for r in rows]


def fetch_dict_items(dict_type):
    """
    取某个字典类型的全部取值（编码 → 中文）

    返回：[{"value": "101", "label": "车拖车"}, ...]

    ★ 用参数绑定而不是拼字符串：dict_type 来自页面输入，虽然是从列表里选的，
      但接口层面不能假设调用方一定守规矩 —— 用户输入永远按不可信处理。
    """
    # 表名来自 config（可信），dict_type 来自页面（不可信）→ 用参数绑定
    rows = _run(
        f"SELECT dict_value, dict_label FROM {config.DICT_TABLE} "
        "WHERE dict_type = %(dt)s ORDER BY dict_sort, dict_value",
        parameters={"dt": dict_type},
    ).result_rows
    return [{"value": str(r[0]), "label": str(r[1])} for r in rows]


# ── 标识符白名单：表和字段名只允许 [字母/数字/下划线]，杜绝 SQL 注入 ──
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def import_table_as_dict(table_name, value_col, label_col=None, limit=1000):
    """
    ★ 从任意业务表导入一列去重取值（可选带中文含义列），作为数据字典

    例：ods_tu_record_type_all 的 id 列（187=缴纳代驾审核激活费）——
        这种"枚举表"本身存的就是编码字典，直接取出来即可，不用手抄。

    ★ 安全：table_name / value_col / label_col 都先校验白名单 + 反引号包裹，
      绝不接受任意字符串进 SQL（用户输入按不可信处理）。
    """
    if not _IDENT_RE.match(table_name or ""):
        raise ValueError(f"非法表名：{table_name}")
    cols = {c for c, _ in list_columns(table_name)}   # 真实字段名（白名单校验基础）
    if value_col not in cols:
        raise ValueError(f"表 {table_name} 里没有字段：{value_col}")
    if label_col:
        if not _IDENT_RE.match(label_col):
            raise ValueError(f"非法字段名：{label_col}")
        if label_col not in cols:
            raise ValueError(f"表 {table_name} 里没有字段：{label_col}")

    limit = max(1, min(int(limit or 1000), 5000))     # 夹在 1~5000
    vc = f"`{value_col}`"
    if label_col:
        sel = f"`{value_col}`, `{label_col}`"
        where = f"WHERE `{value_col}` IS NOT NULL AND `{label_col}` IS NOT NULL"
        val_idx, lbl_idx = 0, 1
    else:
        sel = f"`{value_col}`"
        where = f"WHERE `{value_col}` IS NOT NULL"
        val_idx, lbl_idx = 0, 0

    sql = (f"SELECT DISTINCT {sel} FROM `{table_name}` {where} "
           f"ORDER BY `{value_col}` LIMIT {limit}")
    rows = _run(sql).result_rows

    items = []
    seen = set()
    for r in rows:
        v = str(r[val_idx])
        if v in seen:
            continue
        seen.add(v)
        lbl = str(r[lbl_idx]) if label_col else v
        items.append({"value": v, "label": lbl})
    return items


# ═══════════════════════════════════════════════════════════
# ③ 执行 SQL
# ═══════════════════════════════════════════════════════════
def _ensure_limit(sql):
    """
    ★ 强制加 LIMIT —— 防止 `SELECT * FROM 十亿行大表` 把集群拖垮

    【为什么要做在这里，而不是只靠服务端 max_result_rows？】
      服务端的 max_result_rows 是"结果太大就报错"，
      而这里加 LIMIT 是"根本不请求那么多数据"。
      两者效果不同：前者会失败，后者能正常返回前 N 行。
      双保险，体验更好。

    ★★ 这里踩过一个坑（真实报错 code 62 Syntax error at 'LIMIT'）：
      最早的判断是 `" limit " in sql.lower()` —— 要求 limit 前后都得有【空格】。
      但模型输出的 SQL 经常是【多行】的，LIMIT 在行首时前面是换行符而不是空格，
      判断就失灵了 → 我们又在后面追加一个 LIMIT，变成：
          ... ORDER BY x
          LIMIT 100 LIMIT 1000        ← ClickHouse 直接语法错误
      ↓
      修法三点：
        ① 改用正则的【单词边界】\\blimit\\b —— 换行、制表符、大小写都不怕
        ② 先去掉末尾分号（`SELECT ...;` 后面再拼 LIMIT 会变成两条语句）
        ③ 追加时【换行】而不是空格 —— 万一 SQL 末尾是 `-- 注释`，
           空格拼接会把 LIMIT 拼进注释里（限制悄悄失效 = 静默降级）
    """
    s = (sql or "").strip().rstrip(";").rstrip()          # 去首尾空白 + 去末尾分号
    # ★ 系统统一追加的执行参数（不让模型写 SETTINGS → 避免和 LIMIT 位置冲突 code 62）
    syst = (f"SETTINGS join_algorithm='partial_merge', "
            f"max_memory_usage={config.MAX_QUERY_MEMORY}, "
            f"max_execution_time={config.MAX_QUERY_TIMEOUT}")
    has_limit = bool(re.search(r"\blimit\b", s, flags=re.IGNORECASE))
    has_settings = bool(re.search(r"\bsettings\b", s, flags=re.IGNORECASE))
    if has_limit and has_settings:
        return s                                          # 模型都写了：尊重（极少发生）
    if has_limit and not has_settings:
        return f"{s}\n{syst}"                             # 有 LIMIT 无 SETTINGS：补 SETTINGS
    if (not has_limit) and has_settings:
        # 有 SETTINGS 无 LIMIT：把 LIMIT 插到 SETTINGS【前面】（语法要求）
        m = re.search(r"\bsettings\b", s, flags=re.IGNORECASE)
        head = s[: m.start()].rstrip()
        tail = s[m.start():]
        return f"{head}\nLIMIT {config.MAX_RESULT_ROWS}\n{tail}"
    # 都没有：LIMIT 在前、SETTINGS 在后（★ 合法顺序）
    return f"{s}\nLIMIT {config.MAX_RESULT_ROWS}\n{syst}"


def execute_sql(sql):
    """
    执行 SQL，返回 (列名列表, 结果行列表)

    ⚠️ 危险函数：调用前必须先过 guard.py 的两道校验
       ① sql_guard    —— 只读校验（不许 DELETE/DROP）
       ② check_tables —— 表级权限校验（只许查白名单表）
    """
    final_sql = _ensure_limit(sql)              # ★ 强制加行数上限
    result = _run(final_sql)                    # 执行（复用长连接，断线自动重连一次）
    columns = result.column_names               # 列名（CK 的字段名）
    rows = result.result_rows                   # 结果行
    return columns, rows
