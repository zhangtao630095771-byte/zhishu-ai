# -*- coding: utf-8 -*-
"""
check2.py —— 第二批模块的验证脚本
★ 检查：knowledge / guard / rag / prompt 四个模块是否都正常工作
"""
import db          # 数据库模块（先把库建好）
import rag         # 检索引擎
import guard       # 安全护栏
import prompt      # Prompt 组装


# ═══════════════════════════════════════════════════════════
# 准备：确保数据库存在，然后建索引
# ═══════════════════════════════════════════════════════════
db.create_demo_db()                    # 建库建表（已存在则跳过）
index = rag.build_index()              # ★ 建索引：抓表结构 + 算向量


# ═══════════════════════════════════════════════════════════
# ① 测检索：每个问题应该召回正确的表和指标
# ═══════════════════════════════════════════════════════════
print()
print("=" * 62)
print("① 混合检索测试")
print("=" * 62)

questions = [
    "查询2026年7月订单量",            # 关键词命中：订单量 + dwd_order_all
    "2026年7月平均每单多少钱",         # 关键词失效 → 靠向量召回"客单价"
    "查询2026年7月成交总额",           # 关键词失效 → 靠向量召回"GMV"
    "查询2026年7月各城市司机数量",      # 应该召回 dim_driver
    "今年公司年会预算是多少",           # 完全无关 → 应该召回不到任何表
]

for q in questions:
    tables, metrics, info, scores = rag.retrieve(index, q)   # ★ 调检索
    print(f"\n问题：{q}")
    print("  召回的表  ：", tables if tables else "（无）")
    print("  召回的指标：", metrics if metrics else "（无）")
    print("  命中方式  ：", info)
    print("  表向量Top3：", "  ".join(f"{n}:{s:.3f}" for n, s in scores[:3]))


# ═══════════════════════════════════════════════════════════
# ② 测护栏：安全 SQL 放行，危险 SQL 拦截
# ═══════════════════════════════════════════════════════════
print()
print("=" * 62)
print("② 安全护栏测试")
print("=" * 62)

guard_cases = [
    "SELECT COUNT(*) FROM dwd_order_all",              # 应该放行
    "WITH t AS (SELECT 1) SELECT * FROM t",            # 应该放行
    "DELETE FROM dwd_order_all",                       # 应该拦截（写操作）
    "SELECT 1; DROP TABLE dwd_order_all",              # ★ 应该拦截（多语句注入）
    "/* 藏一个危险语句 */ INSERT INTO dim_user VALUES(1)",  # 应该拦截
]

for sql in guard_cases:
    allowed, why = guard.sql_guard(sql)                # ★ 调护栏
    mark = "✅ 放行" if allowed else f"❌ 拦截，{why}"
    print(f"\n  {mark}\n     {sql[:60]}")


# ═══════════════════════════════════════════════════════════
# ③ 测清洗：把 ``` 和分号剥掉
# ═══════════════════════════════════════════════════════════
print()
print("=" * 62)
print("③ SQL 清洗测试")
print("=" * 62)

dirty = "```sql\nSELECT COUNT(*) FROM dwd_order_all;\n```"
print("  清洗前：", repr(dirty))          # repr 会把换行显示成 \n，方便看清
print("  清洗后：", repr(guard.clean_sql(dirty)))


# ═══════════════════════════════════════════════════════════
# ④ 测 Prompt 组装：看最终喂给模型的完整指令
# ═══════════════════════════════════════════════════════════
print()
print("=" * 62)
print("④ Prompt 组装测试（以 客单价 为例）")
print("=" * 62)

tables, metrics, info, _ = rag.retrieve(index, "查询2026年7月客单价")   # 先检索
sys_prompt = prompt.build_system_prompt(                               # 再组装
    index["tables"], index["metrics"], tables, metrics
)
print(sys_prompt)


print()
print("=" * 62)
print("如果以上全部正常 → 第二批模块迁移完成 ✅")
print("=" * 62)
