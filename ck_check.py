# -*- coding: utf-8 -*-
"""
ck_check.py —— ClickHouse 接入体检工具

★ 用途：把 .env 里的 CK 连接信息配好之后，先跑这个脚本，
        它会逐项检查"能不能接、能不能安全地接"，并给出明确建议。
        体检通过再启动 Agent，能省掉大量盲猜时间。

运行： .venv/bin/python ck_check.py

★ 它检查什么？
    ① 配置是否正确（.env 有没有填全）
    ② 能不能连上（网络 / 端口 / 账号密码）
    ③ 账号是不是【只读】的（企业安全底线）
    ④ 能看见哪些表（数据库授权 = AI 的知识边界）
    ⑤ 注释覆盖率（决定知识库质量，也是 text-to-SQL 准确率的命门）
    ⑥ 表结构能不能正确抓取
    ⑦ 索引能不能建起来
    ⑧ 生成的 SQL 能不能真的执行
"""
import traceback                              # 出错时打印完整堆栈，方便排查

import config                                 # 配置
import db_clickhouse as ck                    # 直接用它，因为这是 CK 专用工具


# ═══════════════════════════════════════════════════════════
# 输出小工具
# ═══════════════════════════════════════════════════════════
def title(text):
    """打印一个醒目的小标题"""
    print()
    print("═" * 66)
    print(text)
    print("═" * 66)


def ok(text):
    print(f"  ✅ {text}")


def warn(text):
    print(f"  ⚠️  {text}")


def bad(text):
    print(f"  ❌ {text}")


# ═══════════════════════════════════════════════════════════
# ① 配置检查
# ═══════════════════════════════════════════════════════════
def check_config():
    title("① 配置检查")
    print(f"  DB_TYPE      : {config.DB_TYPE}")
    print(f"  SQL_DIALECT  : {config.SQL_DIALECT}")
    print(f"  CK_HOST      : {config.CK_HOST}")
    print(f"  CK_PORT      : {config.CK_PORT}")
    print(f"  CK_USER      : {config.CK_USER}")
    print(f"  CK_PASSWORD  : {'(已设置)' if config.CK_PASSWORD else '(空)'}")
    print(f"  CK_DATABASE  : {config.CK_DATABASE}")
    print(f"  超时保护     : {config.CK_MAX_EXECUTION_SEC} 秒")
    print(f"  行数上限     : {config.CK_MAX_RESULT_ROWS}")

    problems = []                             # 收集问题，最后一起报
    if config.DB_TYPE != "clickhouse":
        problems.append("DB_TYPE 不是 clickhouse —— 请在 .env 里设置 DB_TYPE=clickhouse")
    if config.CK_USER == "default":
        problems.append("CK_USER 是 default —— 生产环境必须用专用的【只读账号】")
    if not config.CK_DATABASE:
        problems.append("CK_DATABASE 没填 —— 请指定你要接的库名")

    for p in problems:
        warn(p)

    if not problems:
        ok("配置看起来没问题")
    return len(problems) == 0


# ═══════════════════════════════════════════════════════════
# ② 连接测试
# ═══════════════════════════════════════════════════════════
def check_connection():
    title("② 连接测试")
    try:
        client = ck._client()                  # 用项目里的连接函数（带超时设置）
        ver = client.query("SELECT version()").result_rows[0][0]
        ok(f"连接成功，ClickHouse 版本 {ver}")
        client.close()
        return True
    except Exception as e:
        bad(f"连接失败：{type(e).__name__}: {str(e)[:200]}")
        print()
        print("  排查建议：")
        print("    · 端口对不对？8123 是 HTTP 接口（不是 9000 原生端口）")
        print("    · 账号密码对不对？先在 clickhouse-client 里手工连一次试试")
        print("    · 网络通不通？内网地址要确认你的机器能访问")
        print("    · 如果用了 HTTPS/负载均衡，需要额外的 SSL 配置")
        return False


# ═══════════════════════════════════════════════════════════
# ③ ★ 权限体检 —— 账号是不是只读的
# ═══════════════════════════════════════════════════════════
def check_privileges():
    """
    ★ 为什么要专门查这个？
      给 AI 用的账号必须是只读的。如果给了写权限，一旦护栏被绕过，
      模型生成的 ALTER/DROP 会真的改掉生产数据 —— 那是不可逆的事故。
      这是企业接入的最后一道防线，必须在数据库侧做。
    """
    title("③ 权限体检（账号是否只读）")
    client = ck._client()
    try:
        rows = client.query("SHOW GRANTS").result_rows
    except Exception as e:
        warn(f"查不到权限信息（{type(e).__name__}），请手工确认账号权限")
        client.close()
        return None

    grants = [str(r[0]) for r in rows]         # 每行是一条授权语句
    print("  当前账号的授权：")
    for g in grants:
        print(f"    {g}")

    # ── 分析：有没有写权限 ──
    WRITE_KEYWORDS = ["insert", "alter", "drop", "create", "truncate", "optimize"]
    #                 这些都是会改数据/结构的操作
    dangerous = [g for g in grants
                 if any(k in g.lower() for k in WRITE_KEYWORDS)]

    print()
    if dangerous:
        bad(f"发现 {len(dangerous)} 条【写权限】，这不符合只读要求！")
        for g in dangerous:
            print(f"      {g}")
        print()
        print("  建议在 ClickHouse 上执行（把 <表> 换成实际表名）：")
        print("    REVOKE ALL ON <库>.<表> FROM 你的账号;")
        print("    GRANT SELECT ON <库>.<表> TO 你的账号;")
    else:
        ok("没有发现写权限，符合只读要求")

    # ── 提示 GRANT SELECT ON *.* 这种过宽授权 ──
    if any(g.strip().lower().endswith("on *.*") or "*.*" in g for g in grants):
        warn("检测到全库授权（*.*）—— 建议改成逐表授权，只授 AI 确实需要的表")

    client.close()
    return not dangerous


# ═══════════════════════════════════════════════════════════
# ④ 表结构 + 注释覆盖率
# ═══════════════════════════════════════════════════════════
def check_schema():
    title("④ 表结构抓取 + 注释覆盖率")
    try:
        rows = ck.fetch_schema()               # 抓表结构
    except Exception as e:
        bad(f"抓表结构失败：{type(e).__name__}: {str(e)[:200]}")
        print("  提示：确认账号对 system.tables / system.columns 有读权限")
        return None

    tables = sorted({r[0] for r in rows})      # 去重后的表名
    print(f"  抓到 {len(tables)} 张表、{len(rows)} 个字段")
    print()
    print("  表清单：")
    for t in tables[:40]:                      # 最多显示 40 张，避免刷屏
        print(f"    · {t}")
    if len(tables) > 40:
        print(f"    ... 还有 {len(tables) - 40} 张")

    # ── 注释覆盖率（这是最影响准确率的指标）──
    comments = ck.fetch_comments()
    t_comment = comments.get("tables", {})
    c_comment = comments.get("columns", {})

    t_rate = len(t_comment) / len(tables) * 100 if tables else 0
    c_rate = len(c_comment) / len(rows) * 100 if rows else 0

    print()
    print(f"  表注释覆盖率  : {len(t_comment)}/{len(tables)} = {t_rate:.0f}%")
    print(f"  字段注释覆盖率: {len(c_comment)}/{len(rows)} = {c_rate:.0f}%")

    if c_rate >= 80:
        ok("字段注释很完整，知识库可以零成本冷启动")
    elif c_rate >= 30:
        warn("字段注释偏少，建议补齐关键字段（尤其是状态码这类枚举字段）")
    else:
        bad("字段注释严重缺失 —— 这会直接拉低 SQL 准确率")
        print()
        print("  为什么字段注释这么关键？")
        print("    我们踩过的真实坑：模型不知道 status 各枚举值的含义，")
        print("    把'完单量'写成了 status=7（已取消）。")
        print("    直到字段上写明 '6已完成 7已取消' 才修好。")
        print()
        print("  补救办法：")
        print("    · ClickHouse 支持给字段加注释：")
        print("        ALTER TABLE <表> COMMENT COLUMN <字段> '中文说明';")
        print("    · 或者用 admin.py 在配置库里人工维护表注释")

    return {"tables": tables, "rows": rows,
            "t_comment": t_comment, "c_comment": c_comment,
            "t_rate": t_rate, "c_rate": c_rate}


# ═══════════════════════════════════════════════════════════
# ⑤ 建索引
# ═══════════════════════════════════════════════════════════
def check_index():
    title("⑤ 建索引（RAG 知识库）")
    try:
        import rag                             # 延迟导入：它比较重（要加载模型）
        index = rag.build_index()
        ok(f"索引建成：{len(index['tables'])} 张表 + {len(index['metrics'])} 个指标")

        # ── 权限过滤对比：数据库授权 vs 代码白名单 ──
        print()
        print(f"  可检索的表: {sorted(index['allowed_tables'])}")
        return index
    except Exception as e:
        bad(f"建索引失败：{type(e).__name__}: {str(e)[:300]}")
        traceback.print_exc()
        return None


# ═══════════════════════════════════════════════════════════
# ⑥ ★ 门槛检查：SQL 能不能真的执行
# ═══════════════════════════════════════════════════════════
def check_query(index):
    title("⑥ 真实执行测试（不调大模型，直接验证 SQL 通道）")

    # 从索引里挑一张表，拼一条最简单的查询
    if not index or not index["tables"]:
        bad("没有可用的表，跳过")
        return False

    table = index["tables"][0]["name"]         # 取第一张可见表做测试
    sql = f"SELECT count() AS cnt FROM {table}"
    print(f"  测试 SQL: {sql}")

    try:
        cols, rows = ck.execute_sql(sql)
        ok(f"执行成功，列名 {cols}，结果 {rows}")

        # ── 验证 LIMIT 是否被自动注入（防大查询的关键保护）──
        print()
        final = ck._ensure_limit(f"SELECT * FROM {table}")
        ok(f"LIMIT 自动注入验证：{final[:70]}")
        return True
    except Exception as e:
        bad(f"执行失败：{type(e).__name__}: {str(e)[:200]}")
        return False


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
def main():
    print()
    print("╔" + "═" * 64 + "╗")
    print("║" + "ClickHouse 接入体检".center(58) + "║")
    print("╚" + "═" * 64 + "╝")

    results = {}                               # 收集每一项的结果

    results["配置"] = check_config()

    if not check_connection():
        title("体检结论")
        bad("连接不通，后面几项无法检查")
        print()
        print("  请先解决连接问题，再重跑本脚本。")
        return

    results["只读权限"] = check_privileges()
    schema = check_schema()
    results["表结构"] = schema is not None

    index = check_index()
    results["索引"] = index is not None

    results["执行通道"] = check_query(index)

    # ── 总结 ──
    title("体检结论")
    for k, v in results.items():
        mark = "✅" if v else ("⚠️ " if v is None else "❌")
        print(f"  {mark}  {k}")

    print()
    if all(v for v in results.values() if v is not None):
        ok("体检通过，可以启动 Agent 了")
        print()
        print("  启动命令：")
        print("    .venv/bin/python -m uvicorn server:app --reload --port 8000")
    else:
        warn("有项目未通过，请先按上面的提示修复")

    # ── 附：确定性的建议 ──
    print()
    print("─" * 66)
    print("接入真实库后建议做的两件事")
    print("─" * 66)
    print("  ① 建真实业务评测集量化效果（评测功能已移除，可后续按需自建）")
    print("     重点关注：准确率有没有因为'表变多'而下降")
    print()
    print("  ② 校准检索阈值")
    print("     表从几张变成几百张后，原来的阈值（TABLE_THRESHOLD）")
    print("     可能开始放进无关表。建议抽样核查检索结果重新定阈值。")
    print()


if __name__ == "__main__":
    main()
