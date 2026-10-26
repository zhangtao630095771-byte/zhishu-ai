# -*- coding: utf-8 -*-
"""
db.py —— 数据源门面（Facade 模式）

★ 设计意图：上层代码（rag.py / agent.py / server.py）只 import db，
  只调用 fetch_schema() / execute_sql()，完全不关心底下连的是什么库。

        ┌──────────────┐
        │  rag.py      │  ← 上层只认识 db
        │  agent.py    │
        └──────┬───────┘
               │
        ┌──────▼───────┐
        │    db.py     │  ← 门面：根据配置分发
        └──────┬───────┘
               │
      ┌────────┴────────┐
      ▼                 ▼
┌─────────────┐  ┌──────────────────┐
│ db_sqlite.py│  │ db_clickhouse.py │  ← 两种实现，接口完全相同
│ (开发/演示)  │  │ (生产)            │
└─────────────┘  └──────────────────┘

★ 怎么切换？改 .env 里的 DB_TYPE 即可，一行代码都不用动：
      DB_TYPE=sqlite       → 用本地文件库（演示）
      DB_TYPE=clickhouse   → 连公司真实数仓

★ 这就是【适配器模式】的价值：
  换底层实现，上层零改动。你在 DESIGN.md 里看到的"换 CK 只改 2 个函数"，
  到了这一步已经升级成"改 1 行配置"。
"""
import config     # 读配置，决定用哪个实现


# ═══════════════════════════════════════════════════════════
# ★ 关键的一步：根据配置选择实现
# ═══════════════════════════════════════════════════════════
if config.DB_TYPE == "clickhouse":
    import db_clickhouse as _impl          # 生产：连真实数仓
    IMPL_NAME = "ClickHouse"
else:
    import db_sqlite as _impl              # 开发：本地 SQLite 演示库
    IMPL_NAME = "SQLite"
#   ★ 注意：这里用 if/else 在模块加载时只 import 一个实现。
#     好处：用 SQLite 时根本不会去 import clickhouse_connect，
#           没装这个包也不会报错。


# ═══════════════════════════════════════════════════════════
# 对外暴露的三个函数 —— 直接转发给真正的实现
# ═══════════════════════════════════════════════════════════
def create_demo_db():
    """建演示库（真实库不需要，ClickHouse 实现里是空操作）"""
    return _impl.create_demo_db()


def fetch_schema():
    """
    ★ 抓取表结构 —— 整个 Agent 的数据入口

    返回：[(表名, 字段名, 类型), ...]
    不管底下是 SQLite 还是 ClickHouse，格式都一样
    """
    return _impl.fetch_schema()


def list_tables():
    """
    ★ 只列出全部表名（不含字段）—— 给"表与权限"页面用

    返回：[表名, ...]
    比 fetch_schema 轻得多（一次查询，不去逐表拉字段）
    """
    return _impl.list_tables()


def list_columns(table_name):
    """
    ★ 只列出【一张表】的字段（名字 + 类型）—— 给"RAG 知识库"页面下钻编辑用

    返回：[(字段名, 类型), ...]，按字段在表里的自然顺序

    ★ 为什么不复用 fetch_schema()？
      fetch_schema 会把【全库】上万行字段一次性拉回来（本库 18025 行）。
      而页面每次点开一张表只需要几十行 —— 用这个函数点一次查一次，快得多。
    """
    return _impl.list_columns(table_name)


def list_column_stats():
    """
    ★ 按表统计字段数 + 带数据库注释的字段数 —— 给"RAG 知识库"概览用

    返回：{表名: {"total": n, "db_commented": m}}

    ★ 交给数据库做聚合，而不是拉全库字段明细回 Python 统计：
      后者在 SSH 隧道下要几十秒，聚合查询只返回几百行。
    """
    return _impl.list_column_stats()


def list_dict_types():
    """
    ★ 列出数据字典源表里有哪些字典类型（供"从数据库导入"页面选择）

    返回：[{"dict_type": "app_entity_source", "count": 12, "sample": "车拖车 / ..."}, ...]
    """
    return _impl.list_dict_types()


def fetch_dict_items(dict_type):
    """
    ★ 取某个字典类型的全部取值

    返回：[{"value": "101", "label": "车拖车"}, ...]
    """
    return _impl.fetch_dict_items(dict_type)


def import_table_as_dict(table_name, value_col, label_col=None, limit=1000):
    """
    ★ 从任意业务表导入一列去重取值，作为数据字典条目

    例：tu_record_type 的 id 列（187=缴纳代驾审核激活费）——
        公司字典源表里没有这张表的取值，它本身就是一张"枚举表"，
        直接把它的 (id, name) 取出来就是一份现成的编码字典。

    参数：
        table_name   —— 业务表名（如 ods_tu_record_type_all）
        value_col    —— 编码列（如 id）
        label_col    —— 可选：中文含义列（如 type_name）；不给则用编码本身当含义
        limit        —— 最多取多少条去重取值（默认 1000，防止大表炸库）

    返回：[{"value": "187", "label": "缴纳代驾审核激活费"}, ...]
    """
    return _impl.import_table_as_dict(table_name, value_col, label_col, limit)


def fetch_comments(table_names=None):
    """
    ★ 抓取表注释和字段注释

    参数 table_names：
        传了列表 → 只抓这些表的注释（★ 推荐）
        None     → 抓整库

    返回：{"tables": {表名: 注释}, "columns": {"表名.字段名": 注释}}

    ★ 为什么需要这个？
      真实数仓几百张表，手写注释不现实。
      而 ClickHouse 的 system.tables / system.columns 里本来就存着注释，
      直接抓下来，知识库就"冷启动零成本"了。
      （下层的 SQLite 实现是从建表语句里解析出来的，行为保持一致）

    ★ 为什么建议传 table_names？
      整库字段注释动辄上万行，会撞上连接的 max_result_rows 上限被杀掉，
      而且调用方（建索引、知识库页面）通常只关心可见的那几张表。
    """
    return _impl.fetch_comments(table_names)


def execute_sql(sql):
    """
    执行 SQL，返回 (列名列表, 结果行列表)

    ⚠️ 调用前必须先过 guard 的两道校验（只读 + 表权限）
    """
    return _impl.execute_sql(sql)
