# -*- coding: utf-8 -*-
"""
db_sqlite.py —— SQLite 数据源实现（开发 / 演示用）
★ 定位：这是【数据源实现】之一，另一个是 db_clickhouse.py
★ 两者对外提供完全相同的四个函数：
      create_demo_db() / fetch_schema() / fetch_comments() / execute_sql()
  → 所以上层（db.py / rag.py / agent.py）完全不关心底下是哪种库
★ 这就是【适配器模式】：同一套接口，多种实现，随时可替换
"""
import sqlite3                # 引入数据库驱动（Python 自带，无需 pip 安装）
import re                     # 正则：SQLite 本身不存注释，我们从建表语句里解析出来
import config                 # 引入配置模块


# 从 SQLite 建表语句里抠出字段注释
#   行样例：  city        TEXT,         -- 城市
#   模式分解：
#     ^\s*([A-Za-z_]\w*)    → 行首的字段名
#     \s+                   → 至少一个空白
#     [^-]*?                → 类型部分（不含 -- ，非贪婪）
#     --\s*(.+?)\s*$        → 注释内容
_COL_COMMENT_PATTERN = re.compile(r"^\s*([A-Za-z_]\w*)\s+[^-]*?--\s*(.+?)\s*$")

# 建表语句里这些是约束关键字，不是字段名，解析时要跳过
_NOT_A_COLUMN = {"primary", "foreign", "unique", "check", "constraint", "key"}


def create_demo_db():
    """
    建演示用的库和表
    ★ 真实项目里不需要这个函数 —— 你们公司已经有库了
    ★ IF NOT EXISTS = 表已存在就不重复建（重复运行不会报错、不会丢数据）
    """
    conn = sqlite3.connect(config.DB_PATH)      # 连上数据库文件（文件不存在就自动新建）
    cur = conn.cursor()                         # 拿到执行 SQL 的光标（≈ 客户端里的输入框）

    # ── 订单事实表 ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dwd_order_all (
            order_id    INTEGER,      -- 订单编号
            user_id     INTEGER,      -- 用户ID
            driver_id   INTEGER,      -- 司机ID
            city        TEXT,         -- 城市
            status      INTEGER,      -- 状态：1待接单 2已接单 3已支付 6已完成 7已取消
            amount      REAL,         -- 订单金额
            create_time TEXT,         -- 下单时间
            finish_time TEXT          -- 完单时间
        )
    """)
    #  ⚠️ 注意：这里的 -- 是 SQL 注释（写在字符串内部），不是 Python 注释
    #     三引号里的内容全是字符串，会被原样发给数据库
    #     千万不要在 """ 后面写 Python 的 # 注释，那会被当成 SQL 的一部分而报错

    # ── 用户维表 ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dim_user (
            user_id    INTEGER,       -- 用户ID
            user_name  TEXT,          -- 用户姓名
            reg_city   TEXT,          -- 注册城市
            reg_time   TEXT,          -- 注册时间
            user_level INTEGER        -- 等级：1普通 2银卡 3金卡
        )
    """)

    # ── 司机维表 ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dim_driver (
            driver_id    INTEGER,     -- 司机ID
            driver_name  TEXT,        -- 司机姓名
            city         TEXT,        -- 所属城市
            reg_time     TEXT,        -- 注册时间
            driver_level INTEGER      -- 司机等级
        )
    """)

    # ── 退款事实表 ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dwd_refund (
            refund_id   INTEGER,      -- 退款单号
            order_id    INTEGER,      -- 关联订单ID
            refund_amt  REAL,         -- 退款金额
            refund_time TEXT          -- 退款时间
        )
    """)

    # ── ★ 敏感信息表（用来演示权限控制）──
    #   真实企业里这类表包括：用户手机号、身份证、银行卡、薪酬、财务明细…
    #   它们【绝不能】因为一句自然语言就被 AI 查出来
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dim_user_contact (
            user_id  INTEGER,         -- 用户ID
            phone    TEXT,            -- 手机号（敏感）
            id_card  TEXT             -- 身份证号（敏感）
        )
    """)

    conn.commit()                               # 提交：把改动真正写进文件
    conn.close()                                # 关闭连接，释放资源

    seed_demo_data()                            # ★ 顺便插入演示数据（没有区分度就没法评测）


def seed_demo_data():
    """
    ★ 给演示库插入"有区分度"的数据
    ★ 为什么必须插数据？
      空表时任何 SQL 都返回 0 或 NULL，评测就完全失效了 ——
      生成的 SQL 对不对都看不出区别。
    ★ 这个函数是幂等的：已有数据就跳过，可以反复调用
    """
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()

    # ── ★ 敏感表数据（独立检查，保证老库也能补上）──
    #   这些数据在库里有，但通过"表白名单"禁止 AI 访问
    #   → 用来验证：权限控制是不是真的生效了
    cur.execute("SELECT COUNT(*) FROM dim_user_contact")
    if cur.fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO dim_user_contact (user_id, phone, id_card) VALUES (?,?,?)",
            [
                (101, "13800000001", "110101199001011234"),
                (102, "13800000002", "310101199002022345"),
                (103, "13800000003", "440101199003033456"),
            ],
        )
        conn.commit()

    # 幂等检查：已经有订单数据就直接返回
    cur.execute("SELECT COUNT(*) FROM dwd_order_all")
    if cur.fetchone()[0] > 0:
        conn.close()
        return                                  # 已有数据，什么都不做

    # ── 订单数据：10 笔，跨越 6/7/8 月，含各种状态 ──
    cur.executemany(
        "INSERT INTO dwd_order_all "
        "(order_id, user_id, driver_id, city, status, amount, create_time, finish_time) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            (1,  101, 201, "北京", 6, 300.0, "2026-06-15", "2026-06-15"),
            (2,  102, 202, "上海", 6, 450.0, "2026-07-05", "2026-07-05"),
            (3,  103, 203, "北京", 7, 200.0, "2026-07-10", None),
            (4,  101, 201, "广州", 6, 180.0, "2026-07-15", "2026-07-15"),
            (5,  104, 204, "北京", 6, 520.0, "2026-07-20", "2026-07-20"),
            (6,  102, 202, "上海", 3, 260.0, "2026-07-22", None),
            (7,  105, 205, "深圳", 7, 310.0, "2026-08-01", None),
            (8,  103, 203, "北京", 6, 400.0, "2026-07-28", "2026-07-28"),
            (9,  106, 206, "上海", 2, 150.0, "2026-07-30", None),
            (10, 101, 201, "深圳", 6, 620.0, "2026-06-20", "2026-06-20"),
        ],
    )
    #   executemany = 一次插入多行（比循环 execute 快）
    #   (?,?,...) = 占位符，执行时被元组里的值替换
    #   None = Python 的 NULL
    #   【SQL 类比】≈ 预编译语句的 ? 占位符，能防 SQL 注入

    # ── 用户数据：5 个 ──
    cur.executemany(
        "INSERT INTO dim_user (user_id, user_name, reg_city, reg_time, user_level) "
        "VALUES (?,?,?,?,?)",
        [
            (101, "张三", "北京", "2026-05-01", 3),
            (102, "李四", "上海", "2026-06-10", 2),
            (103, "王五", "北京", "2026-07-03", 1),
            (104, "赵六", "广州", "2026-07-18", 1),
            (105, "孙七", "深圳", "2026-08-02", 1),
        ],
    )

    # ── 司机数据：4 个 ──
    cur.executemany(
        "INSERT INTO dim_driver (driver_id, driver_name, city, reg_time, driver_level) "
        "VALUES (?,?,?,?,?)",
        [
            (201, "王师傅", "北京", "2026-01-10", 3),
            (202, "李师傅", "上海", "2026-03-15", 2),
            (203, "张师傅", "北京", "2026-07-01", 1),
            (204, "刘师傅", "广州", "2026-07-12", 1),
        ],
    )

    # ── 退款数据：3 笔 ──
    cur.executemany(
        "INSERT INTO dwd_refund (refund_id, order_id, refund_amt, refund_time) "
        "VALUES (?,?,?,?)",
        [
            (1, 3, 200.0, "2026-07-12"),
            (2, 7, 310.0, "2026-08-03"),
            (3, 6, 260.0, "2026-07-25"),
        ],
    )

    conn.commit()                               # 提交，数据真正写入
    conn.close()


def fetch_schema():
    """
    ★ 自动抓取表结构 —— 整个 Agent 的数据入口

    返回：[(表名, 字段名, 类型), ...]
    这个列表会流向 RAG：被组装成知识库、做成向量、供检索使用
    """
    conn = sqlite3.connect(config.DB_PATH)      # 连库
    cur = conn.cursor()                         # 拿光标

    # 第一步：查出有哪些表
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    #   sqlite_master 是 SQLite 的"系统表"，存着所有表的信息    #   列表推导式：把结果里每行的第 0 列（表名）取出来组成新列表
    #   【SQL 类比】≈ SELECT name FROM sqlite_master WHERE type='table'

    # 第二步：逐张表查出它的字段
    rows = []                                   # 装结果的空列表
    for t in tables:                            # 遍历每张表
        for c in cur.execute(f"PRAGMA table_info({t})").fetchall():   # 查字段
            rows.append((t, c[1], c[2]))        # c[1]=字段名, c[2]=类型
    #   PRAGMA 是 SQLite 特有的"元命令"，用来查数据库自身的信息
    #   f"..." 是 f-string，{t} 会被替换成当前表名

    conn.close()                                # 关连接
    return rows                                 # 返回统一格式


def list_tables():
    """只列出全部表名（给"表与权限"页面用），返回 [表名, ...]"""
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    conn.close()
    return tables

    # ★★★ 换数据库时只改这个函数，其它代码一行不动：
    #
    #   MySQL:
    #       SELECT table_name, column_name, data_type
    #       FROM information_schema.columns
    #       WHERE table_schema = '你的库名'
    #
    #   ClickHouse:
    #       SELECT table, name, type
    #       FROM system.columns
    #       WHERE database = '你的库名'
    #
    #   只要整理成 (表名, 字段名, 类型) 这个统一格式，
    #   后面的 RAG、Prompt、Agent 全都不用改。


def list_columns(table_name):
    """
    只列出【一张表】的字段名和类型（给 RAG 知识库页面的字段编辑用）

    返回：[(字段名, 类型), ...]，按字段在表里的自然顺序
    ★ SQLite 用 PRAGMA table_info；等价于 ClickHouse 的 system.columns
    """
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    #   PRAGMA table_info 返回 (序号, 字段名, 类型, 是否非空, 默认值, 是否主键)
    rows = cur.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    conn.close()
    return [(r[1], r[2]) for r in rows]


def list_column_stats():
    """
    按表统计字段数量 + 有多少字段带数据库注释

    返回：{表名: {"total": n, "db_commented": m}}
    ★ SQLite 没有 system.columns 那种带注释的元数据表，
      所以 db_commented 恒为 0。人工注释存在 config_store，不在这里。
    """
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    stats = {}
    for t in tables:
        n = len(cur.execute(f'PRAGMA table_info("{t}")').fetchall())
        stats[t] = {"total": n, "db_commented": 0}
    conn.close()
    return stats


def list_dict_types():
    """
    列出数据字典源表里有哪些字典类型

    ★ SQLite 演示库没有字典表（真实数仓才有），这里返回空列表。
      上层代码不用判断"底下是什么库"，拿到空列表走"没有可导入字典"分支即可。
    """
    return []


def fetch_dict_items(dict_type):
    """取某个字典类型的全部取值。演示库没有字典表 → 返回空列表"""
    return []


def import_table_as_dict(table_name, value_col, label_col=None, limit=1000):
    """
    ★ 从任意业务表导入一列去重取值。演示库没有这类枚举表 → 返回空列表。

    和 ClickHouse 实现保持同一签名，上层代码不用关心底下是什么库。
    """
    return []


def fetch_comments(table_names=None):
    """
    抓取表注释和字段注释

    参数 table_names：只抓这些表的注释（None = 全部）
    ★ 和 ClickHouse 实现保持同一签名，上层代码不用关心底下是哪个库

    返回：{"tables": {表名: 注释}, "columns": {"表名.字段名": 注释}}

    ★ 为什么要实现这个函数？
      因为真实数仓（ClickHouse）里【本来就存着注释】，
      我们希望上层代码用同一套方法拿注释，不关心底下是什么库。
      SQLite 本身没有注释功能，但我们建表时写的 `-- 订单编号` 会被
      SQLite 原样保存进 sqlite_master.sql，所以可以把它们解析出来，
      让演示库和真实库的行为保持一致 —— 这样"自动抓注释"这套机制
      能在本地先验证通过，接到真实 CK 时就是同一个逻辑。
    """
    conn = sqlite3.connect(config.DB_PATH)      # 连库
    cur = conn.cursor()                         # 拿光标

    # sqlite_master 是 SQLite 的系统表，sql 列存着原始建表语句
    rows = cur.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table'"
    ).fetchall()
    conn.close()                                # 关连接

    tables, columns = {}, {}                    # 两个空字典收集结果
    for name, ddl in rows:                      # 逐张表处理
        if table_names and name not in table_names:
            continue                            # ★ 只要指定表的注释
        if not ddl:                             # 极少数情况 sql 为空（如内部表）
            continue
        for line in ddl.splitlines():           # 逐行扫建表语句
            m = _COL_COMMENT_PATTERN.match(line)
            #   re.match 从行首开始匹配
            if not m:                           # 这行没有注释，跳过
                continue
            col = m.group(1)                    # group(1) = 字段名
            if col.lower() in _NOT_A_COLUMN:    # 是约束关键字，不是字段
                continue
            columns[f"{name}.{col}"] = m.group(2)   # group(2) = 注释内容
            #   ★ 键用 "表名.字段名" 避免同名字段（如两个表都有 city）互相覆盖

    return {"tables": tables, "columns": columns}


def execute_sql(sql):
    """
    执行 SQL，返回 (列名列表, 结果行列表)

    ⚠️ 危险函数：调用前必须先过安全护栏（guard.py）
    """
    conn = sqlite3.connect(config.DB_PATH)      # 连库
    cur = conn.cursor()                         # 拿光标
    cur.execute(sql)                            # 执行 SQL（危险动作！）
    columns = [d[0] for d in cur.description] if cur.description else []
    #   cur.description = 结果集的列元信息，d[0] 是列名
    #   if ... else [] = 没有返回列时给个空列表，避免报错
    rows = cur.fetchall()                       # 取回全部结果行
    conn.close()                                # 关连接
    return columns, rows                        # 返回 (列名, 数据)
