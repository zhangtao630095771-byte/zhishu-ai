# -*- coding: utf-8 -*-
"""
一次性脚本：精准同步 sql_rules 的「规则5」，只改这一条、不碰其它自定义内容。

用法（在项目根目录执行）：
    .venv/bin/python sync_sql_rules_rule5.py

回滚：脚本会先把旧内容备份到 sql_rules.bak.<时间戳>.txt，
      如需回滚可用 `python admin.py set sql_rules sql_rules.bak.<时间戳>.txt`。
"""
import datetime
import config_store

# 旧规则5原文（要替换的那一行）
OLD = "5. 只输出 SQL，不要任何解释，不要用 ``` 包裹"
# 新规则5（多行）
NEW = ("5. 只输出 SQL，不要任何解释，不要用 ``` 包裹\n"
       "   ★ 第一行必须是 SQL 本身（SELECT 或 WITH），前面绝对不要写任何思路/说明/结论等开场白\n"
       "   （例如「不用 WITH 了，直接 CROSS JOIN 输出一行」这类思考过程一律禁止——\n"
       "     那是解释，不是 SQL，会直接导致解析失败、查询被拒）")


def main():
    cur = config_store.get_prompt("sql_rules")
    if "第一行必须是 SQL 本身" in cur:
        print("✅ 规则5 已是新版，无需改动")
        return
    if OLD not in cur:
        print("⚠️ 未找到旧规则5原文，可能已被改过，请人工检查当前规则5：")
        print([l for l in cur.split("\n") if l.startswith("5.")][:1])
        return
    # 先备份旧内容，安全可回滚
    bak = "sql_rules.bak.{}.txt".format(datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(bak, "w", encoding="utf-8") as f:
        f.write(cur)
    # 精准替换这一条
    updated = cur.replace(OLD, NEW, 1)
    config_store.set_prompt("sql_rules", updated)
    print("✅ 已精准更新规则5；旧内容已备份到 {}".format(bak))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ 失败：{}".format(e))
        print("   若提示 database is locked / disk I/O error，"
              "请先停掉正在运行的服务（server.py）再跑一次。")
