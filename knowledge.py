# -*- coding: utf-8 -*-
"""
knowledge.py —— 业务知识的访问入口
★ 重要变化：知识【不再写在本文件里】，而是存在配置库（config_store）
    以前：指标定义写死在这个文件 → 业务改口径要改代码
    现在：指标定义存在数据库    → 用 admin.py 改完立即生效

★ 那这个文件还留着干嘛？
    它给上层提供一个【稳定的接口】：
        rag.py 只知道 knowledge.get_metrics()，
        不关心知识到底存在 SQLite、MySQL 还是向量库里。

    将来你把存储换成 MySQL 或云配置中心，
    rag.py、agent.py 一行都不用改 —— 这就是"接口隔离"的价值。
"""
import config_store     # 配置存储（真正读数据的地方）
import db               # 数据源（要列出库里的表）


def get_metrics():
    """
    返回【已开放】的指标知识库（给 RAG 用）

    返回格式：列表，每个元素是一个字典
        [{"name": "订单量", "keywords": [...], "definition": "...",
          "doc": "...", "tables": [...], "enabled": True}, ...]

    ★ 注意：这里只返回开放状态的指标。
      停用的指标不会进入向量索引 → AI 检索不到它的口径 → 也就答不出来。
      这是"看不见 = 做不到"思路在指标层的复用（和表权限同一个道理）。
    """
    return config_store.get_metrics(enabled_only=True)


def get_all_metrics():
    """
    返回【全部】指标（含已停用的）—— 给管理页面用

    ★ 管理页面必须能看到停用的指标，否则你没法把它们重新打开。
      "业务路径只看开放的，管理路径看全部"，这是权限系统的通用做法。
    """
    return config_store.get_metrics(enabled_only=False)


def get_disabled_metrics():
    """返回【已停用】的指标 —— 供 agent 做"问题级拦截"用"""
    return config_store.get_disabled_metrics()


def get_dict_entries():
    """
    返回【已开放】的数据字典（编码 → 中文含义），给 RAG 用

    返回格式：[{"name","dict_type","field_hint","keywords","items","enabled"}, ...]

    ★ 字典解决的是"编码猜不出来"的问题：
      `app_entity_source = 101` 里的 101 是什么意思？模型不可能从字段名推出来。
      给它字典，它才知道 101=车拖车、201=慧拉车。
      没给字典时，它会瞎猜（比如写成 =1），生成一条"能跑但数据全错"的 SQL。
    """
    return config_store.get_dict_entries(enabled_only=True)


def get_all_dict_entries():
    """返回【全部】字典（含已停用的）—— 给管理页面用"""
    return config_store.get_dict_entries(enabled_only=False)


# ═══════════════════════════════════════════════════════════
# ★ Semantic Layer · 实体 / 关系 / 文档 的访问入口
#   （接口隔离：rag / semantic_layer 只认 knowledge.*，不关心存储是 SQLite 还是别的）
# ═══════════════════════════════════════════════════════════
def get_entities():
    """业务实体（P2 层）"""
    return config_store.get_entities()


def get_relationships(enabled_only=True):
    """关联 / Join 规则（P2 层）"""
    return config_store.get_relationships(enabled_only=enabled_only)


def get_kb_docs(enabled_only=True):
    """普通业务文档（P4 层）"""
    return config_store.get_kb_docs(enabled_only=enabled_only)


def get_table_comments():
    """
    返回表的中文业务注释
    返回格式：字典 {表名: 注释}
    """
    return config_store.get_table_comments()


def get_column_comments():
    """
    返回【人工维护】的字段业务注释
    返回格式：字典 {"表名.字段名": 注释}

    ★ 这是 RAG 准确率最高杠杆的一环。
      数据仓库自带的字段注释常常是空的（本库实测 0/18025），
      而模型写错 SQL 最多的情形就是"用错字段含义"
      （比如不知道 status=6 是已完成、7 是已取消）。
      所以人工注释必须能覆盖数据库注释，优先级最高。
    """
    return config_store.get_column_comments()


# ═══════════════════════════════════════════════════════════
# 业务表清单
# ═══════════════════════════════════════════════════════════
# ★ 哪些表算"业务表"？
#   ClickHouse 里混着大量技术性表：分片本地表（_local）、物化视图（_mv）、
#   备份（_bak）、临时表（_tmp）、分布式表的内部列（.inner_id）……
#   它们不是业务数据，不该出现在知识库页面，更不该进 RAG。
#
# ★ 这套规则【必须只有一份】——页面、导出、一致性自检共用同一个函数。
#   否则会出现"页面显示 3 张表、导出文件里 368 张"这种对不上账的情况，
#   而导出文件是要拿去评审、存档、迁移的，一旦口径不一致就没人敢信它。
NOISE_TABLE_MARKERS = ("_local", "_mv", ".inner_id", "_bak", "_tmp")


def business_tables():
    """返回真实的业务表名列表（已剔除技术性噪声表）"""
    return [n for n in db.list_tables()
            if not any(k in n for k in NOISE_TABLE_MARKERS)]
