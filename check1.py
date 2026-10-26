# -*- coding: utf-8 -*-
"""
check1.py —— 第一批模块的总验证脚本
★ 作用：一条命令，检查 config / llm / db 三个模块是否都正常工作
★ 这种"自检脚本"是好习惯：改完代码先跑它，比在业务代码里撞错误快得多
"""
import config                     # 导入配置模块
import llm                        # 导入大模型模块
import db                         # 导入数据库模块


print("=" * 55)
print("① 配置模块 config.py")
print("=" * 55)
print("   SQL 方言  ：", config.SQL_DIALECT)        # 应该是 SQLite
print("   表阈值    ：", config.TABLE_THRESHOLD)     # 应该是 0.45
print("   指标阈值  ：", config.METRIC_THRESHOLD)    # 应该是 0.5
print("   最大重试  ：", config.MAX_RETRY)           # 应该是 3
print("   数据库路径：", config.DB_PATH)


print()
print("=" * 55)
print("② 大模型模块 llm.py（会真的调用 DeepSeek，稍等几秒）")
print("=" * 55)
reply = llm.chat(                                    # 调我们自己封装的 chat 函数
    "你是一个助手，回答要极简",                          # system：身份和规矩
    "用5个字解释什么是数据库",                           # user：这次问什么
)
print("   模型回复：", reply)


print()
print("=" * 55)
print("③ 数据库模块 db.py")
print("=" * 55)
db.create_demo_db()                                  # 建库建表
schema = db.fetch_schema()                           # ★ 自动抓取表结构
print("   抓取到的字段数：", len(schema))
print("   前 3 条        ：")
for row in schema[:3]:                               # 只看前 3 条，避免刷屏
    print("      ", row)


print()
print("④ 执行 SQL 测试")
cols, rows = db.execute_sql("SELECT COUNT(*) AS cnt FROM dwd_order_all")
print("   列名：", cols)
print("   结果：", rows)


print()
print("=" * 55)
print("如果以上全部正常 → 第一批模块迁移完成 ✅")
print("=" * 55)
