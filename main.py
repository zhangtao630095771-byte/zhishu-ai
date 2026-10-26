# -*- coding: utf-8 -*-
"""
main.py —— 程序入口
★ 这是整个项目唯一"可以直接运行"的文件
★ 它的职责：准备环境 → 跑测试问题 → 打印结果。业务逻辑全在 agent.py 里

运行方式：
    .venv/bin/python main.py
"""
import config      # 配置（打印行数上限等）
import db          # 数据库（建库）
import rag         # 检索（建索引）
import agent       # 编排（核心流程）


# ═══════════════════════════════════════════════════════════
# 打印一个问题的处理结果
# ═══════════════════════════════════════════════════════════
def print_result(number, result):
    """把 agent.ask 返回的字典，打印成人能看懂的样子"""
    print("=" * 62)
    print(f"问题{number}: {result['question']}")
    print("-" * 62)
    print("  召回的表  ：", result["tables"] if result["tables"] else "（无）")
    print("  召回的指标：", result["metrics"] if result["metrics"] else "（无）")
    print("  命中方式  ：", result["info"])

    if not result["ok"]:                                 # 没成功
        print("  ❌ 结果   ：", result["error"])
        print()
        return                                           # 提前结束这个函数

    print("  SQL       ：", result["sql"])                # 生成的 SQL
    print("  尝试次数  ：", result["attempts"])            # 重试了几次（1 = 一次成功）
    print("  列名      ：", result["columns"])

    rows = result["rows"]                                # 结果行
    if not rows:                                         # 空结果
        print("  数据      ： （无数据 —— 演示库是空表，属正常）")
    else:
        for r in rows[:config.MAX_ROWS_PRINT]:           # 最多打印 N 行，防止刷屏
            print("             ", r)
    print()


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
def main():
    """程序入口：准备 → 跑问题 → 打印"""
    # ── 准备阶段 ──
    if config.DB_TYPE == "clickhouse":
        import ssh_tunnel
        ssh_tunnel.ensure_tunnel()   # 连真实库时，确保 SSH 隧道已拉起（启动即连）
    db.create_demo_db()             # 建演示库（真实项目里不需要这一步）
    index = rag.build_index()       # ★ 建 RAG 索引（抓表结构 + 算向量），启动时一次

    # ── 测试问题：覆盖各种情况 ──
    questions = [
        "查询2026年7月订单量",            # ① 关键词命中，最简单
        "查询2026年7月新注册用户数",       # ② 换一张表（dim_user）
        "查询2026年7月各城市司机数量",      # ③ 再换一张表（dim_driver）
        "查询2026年7月退款总金额",         # ④ 第四张表（dwd_refund）
        "查询2026年7月客单价",            # ⑤ 需要业务口径 + 指标带出表
        "2026年7月平均每单多少钱",         # ⑥ 口语说法，靠向量语义召回
        "查询2026年7月取消率",            # ⑦ 比率类，考规则 6
        "今年公司年会预算是多少",          # ⑧ 完全无关，应被拦截
    ]

    # ── 逐个处理 ──
    for i, q in enumerate(questions, 1):     # 带序号遍历，序号从 1 开始
        result = agent.ask(index, q)         # ★ 调用核心流程
        print_result(i, result)              # 打印结果

    # ── 收尾 ──
    print("=" * 62)
    print("全部完成 ✅")
    print("=" * 62)


# ═══════════════════════════════════════════════════════════
# 这一句的意思是：
#   只有"直接运行 main.py"时才执行 main()
#   被别人 import main 时不执行（避免一导入就乱跑）
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    main()
