# -*- coding: utf-8 -*-
"""
admin.py —— 配置管理工具（"在线配置后台"的命令行版）
★ 这就是公司级系统里那个"配置管理页面"的雏形
★ 真实产品里它会是一个网页；命令行版是它的最小可用形态

它能管两类东西：
    ① Prompt 规则 / 角色
    ② 指标知识（RAG 的核心）

用法：
    # ── Prompt 配置 ──
    python admin.py list                        列出所有 Prompt 配置项
    python admin.py show   <key>                查看某项 Prompt 配置
    python admin.py set    <key> <文件>          从文件导入 Prompt（覆盖）
    python admin.py reset  <key>                恢复为默认值

    # ── 指标知识 ──
    python admin.py list-metrics                列出所有指标
    python admin.py show-metric <指标名>         查看某个指标的详情
    python admin.py export-metrics <文件.json>   导出全部指标为 JSON
    python admin.py import-metrics <文件.json>   从 JSON 导入指标（同名覆盖）
    python admin.py del-metric <指标名>          删除一个指标

★ 注意：改完指标后，重跑 main.py 会用新知识重建索引。
"""
import sys             # 系统模块：读取命令行参数用
import json            # JSON 模块：导入导出指标用
import config_store    # 配置存储


# ═══════════════════════════════════════════════════════════
# 帮助信息
# ═══════════════════════════════════════════════════════════
def print_usage():
    print("""
用法：
  ── Prompt 配置 ──
  python admin.py list                        列出所有 Prompt 配置项
  python admin.py show   <key>                查看某项 Prompt 配置
  python admin.py set    <key> <文件>          从文件导入 Prompt（覆盖）
  python admin.py reset  <key>                恢复为默认值

  ── 指标知识 ──
  python admin.py list-metrics                列出所有指标
  python admin.py show-metric <指标名>         查看某个指标详情
  python admin.py export-metrics <文件.json>   导出全部指标
  python admin.py import-metrics <文件.json>   从 JSON 导入指标
  python admin.py del-metric <指标名>          删除一个指标

  ── ★ 指标开放权限 ──
  python admin.py set-metric <指标名> on       开放该指标给 AI
  python admin.py set-metric <指标名> off      停用该指标（AI 看不到，问了会被拒）
  ★ 说明：停用的指标进不了知识库；用户问到它会被【直接拒绝】，
    而不是让模型自己拿字段凑一个口径 —— 那样会静默算错，最危险。

  ── ★ 表访问权限 ──
  python admin.py list-acl                    查看当前访问策略和名单
  python admin.py set-acl <表名> allow [备注]   允许 AI 访问该表
  python admin.py set-acl <表名> deny  [备注]   禁止 AI 访问该表
  python admin.py del-acl <表名>              取消登记，回到默认策略
  python admin.py check-acl "<SQL>"           测试一条 SQL 会不会被权限拦住

★ 改权限后需要重建索引才生效（重跑 main.py）
""")


# ═══════════════════════════════════════════════════════════
# Prompt 相关
# ═══════════════════════════════════════════════════════════
def cmd_list():
    """列出所有 Prompt 配置项"""
    rows = config_store.list_prompts()          # [(key, 字符数), ...]
    print("Prompt 配置项：")
    for key, length in rows:                    # 解包：把元组拆成两个变量
        print(f"  {key}   （{length} 字符）")


def cmd_show(key):
    """显示某项 Prompt 配置的完整内容"""
    print(config_store.get_prompt(key))


def cmd_set(key, path):
    """从文件读入内容，覆盖写入 Prompt 配置"""
    with open(path, "r", encoding="utf-8") as f:    # 打开文件
        content = f.read()                          # 读取全部内容
    #   with ... as f = 用完自动关闭文件，不用手动 close
    config_store.set_prompt(key, content)
    print(f"✅ 已更新配置 {key}（{len(content)} 字符）")
    print("   ★ 下次调用 Agent 时立即生效，无需重启程序")


def cmd_reset(key):
    """把配置恢复成默认值"""
    if key not in config_store.DEFAULT_PROMPTS:
        print(f"❌ 配置项没有默认值：{key}")
        return
    config_store.set_prompt(key, config_store.DEFAULT_PROMPTS[key])
    print(f"✅ 已把 {key} 恢复为默认值")


# ═══════════════════════════════════════════════════════════
# 指标知识相关
# ═══════════════════════════════════════════════════════════
def cmd_list_metrics():
    """列出所有指标（含已停用的）"""
    metrics = config_store.get_metrics(enabled_only=False)
    #         ★ 管理视图要看全部，否则看不到停用的指标就没法把它们打开
    opened = [m for m in metrics if m["enabled"]]
    print(f"指标知识库（共 {len(metrics)} 个，其中开放 {len(opened)} 个）：\n")
    for m in metrics:
        flag = "🟢 开放" if m["enabled"] else "🔴 停用"
        kw = "、".join(m["keywords"])               # 把列表拼成字符串
        tb = "、".join(m["tables"])
        print(f"  【{m['name']}】 {flag}")
        print(f"     关键词  ： {kw}")
        print(f"     依赖表  ： {tb}")
        print()


def cmd_set_metric_enabled(name, enabled):
    """开放 / 停用一个指标"""
    if config_store.set_metric_enabled(name, enabled):
        action = "已开放" if enabled else "已停用"
        print(f"✅ {action}指标：{name}")
        if not enabled:
            print("   → 该指标不再进入知识库；用户问到它会被直接拒绝")
            print("   → 需要重建索引才完全生效（重跑 main.py / 点页面「重建索引」）")
    else:
        print(f"❌ 没找到指标：{name}")


def cmd_show_metric(name):
    """显示某个指标的完整信息（含口径全文）"""
    metrics = config_store.get_metrics(enabled_only=False)
    for m in metrics:                               # 遍历找同名的
        if m["name"] == name:
            print(f"指标名   ： {m['name']}")
            print(f"开放状态 ： {'🟢 开放（AI 可以用）' if m['enabled'] else '🔴 停用（AI 看不到）'}")
            print(f"关键词   ： {m['keywords']}")
            print(f"依赖表   ： {m['tables']}")
            print(f"embedding文本： {m['doc']}")
            print(f"业务口径 ：")
            print(m["definition"])                  # 口径可能多行，单独打印
            return
    print(f"❌ 没找到指标：{name}")


def cmd_export_metrics(path):
    """把全部指标导出成 JSON 文件（给业务同学在编辑器里维护）"""
    metrics = config_store.get_metrics(enabled_only=False)
    #         ★ 导出【全部】含 enabled 字段，
    #           这样"导出→编辑→导入"能完整往返，不会把开放状态丢掉
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    #   json.dump(数据, 文件, ensure_ascii=False, indent=2)
    #     ensure_ascii=False → 中文原样输出，不转义
    #     indent=2           → 缩进 2 格，方便人阅读
    print(f"✅ 已导出 {len(metrics)} 个指标到 {path}")


def cmd_import_metrics(path):
    """从 JSON 文件导入指标（同名覆盖，不同名新增）"""
    with open(path, "r", encoding="utf-8") as f:
        metrics = json.load(f)                      # 读 JSON 并转成 Python 列表
    count = 0
    for m in metrics:
        if "name" not in m:                         # 没名字的跳过
            print(f"⚠️ 跳过一条没有 name 的记录")
            continue
        config_store.upsert_metric(m)               # 写入（有则更新，无则新增）
        count += 1
    print(f"✅ 已导入/更新 {count} 个指标")
    print("   ★ 重跑 main.py 即会重建索引、生效")


def cmd_del_metric(name):
    """删除一个指标"""
    if config_store.delete_metric(name):
        print(f"✅ 已删除指标：{name}")
    else:
        print(f"❌ 没找到指标：{name}")


# ═══════════════════════════════════════════════════════════
# ★ 表白名单（访问控制）相关
# ═══════════════════════════════════════════════════════════
def cmd_list_acl():
    """列出表访问名单"""
    import config                                   # 取默认策略用
    mode = "黑名单模式（没登记的默认可见）" if config.ACL_DEFAULT_VISIBLE \
           else "白名单模式（没登记的默认不可见）"
    print(f"当前策略：{mode}\n")

    rows = config_store.list_table_acl()
    if not rows:
        print("（名单为空）")
        return
    print(f"已登记 {len(rows)} 张表：\n")
    for name, visible, note in rows:
        mark = "✅ 允许" if visible else "❌ 禁止"
        print(f"  {mark}   {name}")
        if note:
            print(f"           备注：{note}")
    print()
    print("★ 改完权限后，必须重建索引才生效（重跑 main.py）")


def cmd_set_acl(name, action, note=""):
    """
    设置某张表的访问权限
    action = allow / deny
    """
    if action not in ("allow", "deny"):
        print(f"❌ 第二个参数只能是 allow 或 deny，你给的是：{action}")
        return
    visible = (action == "allow")                   # 是否允许
    config_store.set_table_acl(name, visible, note)
    mark = "允许访问" if visible else "禁止访问"
    print(f"✅ 已设置 {name} → {mark}")
    if note:
        print(f"   备注：{note}")
    print("   ★ 需要重建索引才生效（重跑 main.py）")


def cmd_del_acl(name):
    """
    删除某张表的登记记录 —— 让它回到"默认策略"

    ★ 和"设成 deny"的区别：
        set-acl <表> deny  → 明确【禁止】（白名单/黑名单模式下都不可见）
        del-acl <表>       → 【取消登记】，回到默认策略
                            白名单模式 → 默认不可见
                            黑名单模式 → 默认可见
    """
    if config_store.delete_table_acl(name):
        print(f"✅ 已取消 {name} 的登记，回到默认策略")
    else:
        print(f"⚠️  {name} 本来就没有登记记录")
    print("   ★ 需要重建索引才生效")


def cmd_check_acl(sql):
    """测试一条 SQL 会不会被权限校验拦住（调试用）"""
    import rag                                      # 取白名单
    import guard                                    # 用校验函数
    import db

    db.create_demo_db()
    index = rag.build_index()                       # 拿到 allowed_tables
    tables_in_sql = guard.extract_tables(sql)       # 提取 SQL 里的表名
    allowed, illegal = guard.check_tables(sql, index["allowed_tables"])

    print(f"SQL 中引用的表：{sorted(tables_in_sql)}")
    print(f"允许访问的表  ：{index['allowed_tables']}")
    if allowed:
        print("✅ 权限校验通过")
    else:
        print(f"❌ 越权拦截：{illegal}")


# ═══════════════════════════════════════════════════════════
# 入口：解析命令行参数
# ═══════════════════════════════════════════════════════════
def main():
    """sys.argv 是命令行参数列表：
         python admin.py show sql_rules
                ↑          ↑    ↑
              [0]         [1]  [2]
    """
    args = sys.argv[1:]             # 切片：跳过第 0 个（脚本名），只取后面的参数
    #                                   【SQL 类比】list[1:] ≈ 跳过第一行

    if not args:                    # 没给任何参数
        print_usage()
        return

    cmd = args[0]                   # 第一个参数是子命令
    if cmd == "list":
        cmd_list()
    elif cmd == "show" and len(args) >= 2:
        cmd_show(args[1])
    elif cmd == "set" and len(args) >= 3:
        cmd_set(args[1], args[2])
    elif cmd == "reset" and len(args) >= 2:
        cmd_reset(args[1])
    elif cmd == "list-metrics":
        cmd_list_metrics()
    elif cmd == "show-metric" and len(args) >= 2:
        cmd_show_metric(args[1])
    elif cmd == "export-metrics" and len(args) >= 2:
        cmd_export_metrics(args[1])
    elif cmd == "import-metrics" and len(args) >= 2:
        cmd_import_metrics(args[1])
    elif cmd == "del-metric" and len(args) >= 2:
        cmd_del_metric(args[1])
    elif cmd == "set-metric" and len(args) >= 3:
        #   set-metric <指标名> on|off
        on = args[2].lower() in ("on", "open", "1", "true", "yes", "开放", "启用")
        #   ★ 把常见写法都认了 —— 命令行工具要"猜得中用户想表达什么"，
        #     不该让用户记住 on/off 两个精确单词
        cmd_set_metric_enabled(args[1], on)
    elif cmd == "list-acl":
        cmd_list_acl()
    elif cmd == "set-acl" and len(args) >= 3:
        #   第 4 个参数（备注）是可选的
        note = args[3] if len(args) >= 4 else ""
        cmd_set_acl(args[1], args[2], note)
    elif cmd == "del-acl" and len(args) >= 2:
        cmd_del_acl(args[1])
    elif cmd == "check-acl" and len(args) >= 2:
        cmd_check_acl(args[1])
    else:
        print_usage()               # 参数不对，打印用法


if __name__ == "__main__":
    main()
