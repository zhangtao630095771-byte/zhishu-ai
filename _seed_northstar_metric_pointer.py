# -*- coding: utf-8 -*-
"""
修正北极星指标取数路由：指标数值统一存在 mid_order_stat_indicator_all（幂等、可重跑）

★ 背景（2026-11-18 用户补充关键信息）
  之前判断"北极星数据表 dws_metric_norths_star_*_data 未同步、不可查"是错的。
  实际：设计方案里的"聚合层"没有拆成日/周/月/年 4 张表，而是统一落到了
  ctc_data_warehouse.mid_order_stat_indicator_all 这一张宽表，用 stat_peroid 区分周期：

    stat_peroid        day / week / month / quarter / year（5 种周期）
    stat_peroid_value  周期值（day=YYYY-MM-DD；week=周一日期；month=YYYY-MM；
                       quarter=YYYY-Qn；year=YYYY）
    tab_source         数据来源表名（MySQL 原始表名）
    col_name           指标字段编码 = 北极星指标 base_col_code = 字典 col_code
    col_value          指标值（Float64）
    stat_date          数据写入/更新日期

  ★★★ 关键取数陷阱（实测验证）：
    同一个 (col_name, stat_peroid, stat_peroid_value) 有多行，stat_date 不同 = 不同批次写入，
    col_value 是"截至该 stat_date 的累计值"（周/月/季/年是 period-to-date）。
    取数必须取 stat_date 最新（最大）的那一行，禁止 SUM 多行、禁止取中间版本。

★ 本脚本做的事（走 config_store 官方入口）
  ① 更新 sql_rules【指标取数路由】块：改为"优先直取 mid_order_stat_indicator_all"
  ② 更新 kb_doc《北极星与KPI指标体系架构与取数路由规则》内容
  ③ 给 258 条有 base_col_code 且匹配 mid 表的指标 definition 追加【指标值直取】指针
  ④ 补 mid_order_stat_indicator_all 的 6 个字段注释（语义写清，含"取最新 stat_date"）

运行： .venv/bin/python _seed_northstar_metric_pointer.py
"""
import re
import sqlite3
import db
import config_store as cs

OPERATOR = "admin"
DB_PATH = "/Users/zhangtao/Downloads/autosql/zhishu-ai/agent_config.db"
MID_TABLE = "mid_order_stat_indicator_all"


def main():
    cs._ensure_ready()

    # ══ 0. 读取 CK 数据：北极星指标 + mid 表 col_name 集合 ══
    rows = db.execute_sql(
        "SELECT name, base_col_code FROM dim_meta_metric_norths_star_info_all WHERE is_deleted = 0")
    north = {r[0]: (str(r[1] or "").strip()) for r in rows[1]}
    mid_cols = set(r[0] for r in db.execute_sql(
        f"SELECT DISTINCT col_name FROM {MID_TABLE}")[1])
    print(f"北极星指标 {len(north)} 条；mid 表 col_name {len(mid_cols)} 个")

    # 哪些指标可直取（base_col_code 全部落在 mid 表）
    direct = {}
    for name, base in north.items():
        codes = [c.strip() for c in base.split(",") if c.strip()]
        if codes and all(c in mid_cols for c in codes):
            direct[name] = codes
    print(f"可直取 mid 表的指标：{len(direct)} 条")

    # ══ ① 更新 sql_rules ══
    print("═" * 66)
    print("① 更新 sql_rules【指标取数路由】")
    print("═" * 66)
    con = sqlite3.connect(DB_PATH)
    rules = con.execute("SELECT content FROM prompts WHERE key='sql_rules'").fetchone()[0]
    con.close()
    # 移除旧的【指标取数路由】块（从标记行到文末）
    m = re.search(r"\n# 指标取数路由.*$", rules, re.S)
    if m:
        rules = rules[: m.start()]
    NEW_BLOCK = """

# 指标取数路由（北极星指标体系，2026-11-18 更新）
1. 用户问指标数值（完单量、GMV、接单率等）时，优先直取指标值表 mid_order_stat_indicator_all：
   - col_name = 该指标的 base_col_code（指标若带【指标值直取】指针，指针里已写明 col_name）
   - stat_peroid 按周期取 day/week/month/quarter/year；stat_peroid_value 为周期值
     （day=YYYY-MM-DD；week=周一日期；month=YYYY-MM；quarter=YYYY-Qn；year=YYYY）
   - ★ 同一 (col_name, stat_peroid, stat_peroid_value) 有多行，必须取 stat_date 最新（最大）的一行，
     col_value 才是最新累计值；禁止 SUM 多行、禁止取中间版本。
2. 指标无直取指针（无 base_col_code 或 col_name 不在 mid 表）时，才按【MUST】口径（计算逻辑+统计范围+来源表）从实体表计算。
3. KPI 表（ctc_kpi_*）与 dws_metric_norths_star_*_data 表不存在，禁止生成引用这些表的 SQL。
4. KPI 计划/完成率/考核类问题：KPI 表未接入，用权威口径计算实际值并明确告知"计划与完成率数据未接入"。
5. 仅用户明确要明细/下钻（订单明细、司机列表）时才直接查原实体表。
6. 同比=本期 vs 上年同期，环比=本期 vs 上期。
"""
    cs.set_prompt("sql_rules", rules.rstrip() + NEW_BLOCK)
    print("  ✅ 已更新")

    # ══ ② 更新 kb_doc ══
    print("═" * 66)
    print("② 更新 kb_doc《北极星与KPI指标体系架构与取数路由规则》")
    print("═" * 66)
    con = sqlite3.connect(DB_PATH)
    kb = con.execute("SELECT id FROM kb_docs WHERE title LIKE '%北极星与KPI指标体系%'").fetchone()
    con.close()
    route_doc = {
        "id": kb[0] if kb else None,
        "title": "北极星与KPI指标体系架构与取数路由规则（必读）",
        "keywords": ["指标体系", "北极星", "KPI", "指标口径", "取数路由", "指标数据",
                     "指标宽表", "完成率", "同比", "环比", "计划", "达成", "mid_order_stat_indicator"],
        "priority": 1,
        "enabled": True,
        "content": """# 北极星与KPI指标体系（公司统一建设，2026-11-18 导入知数AI）

## 一、体系架构（四层：指标层→字典层→数仓层→聚合层）

北极星：
- 指标元数据 dim_meta_metric_norths_star_info_all（CK可查，325条）：name指标名/scope业务口径/calc_formula计算公式/level级别(0公司/1部门)/department部门/owner负责人/parent_id父子层级/base_col_code关联基础字典
- 基础指标字典 ods_meta_metric_base_dict_info_all（CK可查，706条）：col_code基础指标编码/business_def业务定义/calc_logic计算逻辑/stat_scope统计范围/tab_source来源表
- ★ 指标值表 mid_order_stat_indicator_all（CK可查，1.6亿行）：北极星指标数值统一存在这张宽表，用 stat_peroid 区分周期（day/week/month/quarter/year），没有单独的日/周/月/年表。

KPI（月度经营考核，5张表在 MySQL ctc_metric 库【未同步CK，当前不可查】）：
- 3张维度表 ctc_kpi_dim_{business,department,metric}；ctc_kpi_fact_monthly_plan 月度计划；ctc_kpi_fact_monthly_kpi 月度KPI宽表（冗余维度+计划，自有 actual_value/completion_rate/yoy/mom 等）。

## 二、指标值表 mid_order_stat_indicator_all 取数口径（核心）

字段：stat_peroid(周期类型) / stat_peroid_value(周期值) / tab_source(来源表) / col_name(指标编码) / col_value(指标值) / stat_date(写入日期)。

1. 定位指标：col_name = 指标的 base_col_code（指标 definition 的【指标值直取】指针已写明；也可用 name 在 dim 元数据表查到 base_col_code）。
2. 定位周期：stat_peroid ∈ day/week/month/quarter/year；stat_peroid_value 格式 day=YYYY-MM-DD、week=周一日期、month=YYYY-MM、quarter=YYYY-Qn、year=YYYY。
3. ★ 取最新版本：同一 (col_name, stat_peroid, stat_peroid_value) 有多行、stat_date 不同，col_value 是"截至该 stat_date 的累计值"。必须取 stat_date 最大（最新）那一行，禁止 SUM 多行、禁止取中间版本。
4. 值语义：day=当日值；week/month/quarter/year=截至最新的周期累计值（period-to-date）。

## 三、取数路由规则（核心：先指标值表，再口径计算，最后实体表）

用户问指标数值（如"上月完单量""3月GMV""接单率趋势"）时，按以下优先级：

1.【指标值表·首选】指标有【指标值直取】指针（258条已配）→ 直取 mid_order_stat_indicator_all：col_name=指针编码、stat_peroid=所需周期、取 stat_date 最新行。
2.【口径计算·兜底】指标无指针（60条新线索类指标 + 7条无 base_col_code）→ 按【MUST】口径（计算逻辑/统计范围/权威来源表）从实体表计算。
3.【KPI类】计划/完成率/达成/考核 → KPI 表未接入：用权威口径算实际值，并明确告知"计划与完成率数据未接入（ctc_kpi_* 未同步）"。
4.【明细下钻】仅用户明确要明细/列表时才直接查原实体表。
5.【同环比】同比=本期 vs 上年同期；环比=本期 vs 上期。
6. 禁止绕过口径从任意实体表 count/sum（口径漂移=结果不可信）。

## 四、指标元数据查询口径

- 查"某指标怎么定义/谁负责/口径" → dim_meta_metric_norths_star_info_all（name 匹配）
- 查"基础计算逻辑/来源表" → ods_meta_metric_base_dict_info_all（col_code = dim 表 base_col_code；tab_source 是 MySQL 原始表名，CK 对应 ods_/dwd_ 前缀 + _all 后缀）
""",
    }
    ok, msg = cs.upsert_kb_doc(route_doc, operator=OPERATOR, source="manual")
    print(f"  {'✅' if ok else '⚠️'} {msg}")

    # ══ ③ 给可直取指标 definition 追加指针 ══
    print("═" * 66)
    print("③ 给指标 definition 追加【指标值直取】指针")
    print("═" * 66)
    added, skipped = 0, 0
    all_metrics = cs.get_metrics(enabled_only=False)
    metric_map = {m["name"]: m for m in all_metrics}
    for name, codes in sorted(direct.items()):
        m = metric_map.get(name)
        if not m:
            skipped += 1
            continue
        defn = m.get("definition") or ""
        if "指标值直取" in defn:      # 幂等：已有指针则跳过
            skipped += 1
            continue
        if len(codes) == 1:
            ptr = (f"\n  ★ 指标值直取：mid_order_stat_indicator_all 表，"
                   f"col_name='{codes[0]}'，stat_peroid 按需取 day/week/month/quarter/year，"
                   f"取 stat_date 最新一行，col_value 即指标值；优先直取，勿从实体表重算")
        else:
            ptr = (f"\n  ★ 指标值直取：mid_order_stat_indicator_all 表，"
                   f"col_name 需取 {'、'.join(chr(39) + c + chr(39) for c in codes)}"
                   f"（多个编码对应计算公式的分子/分母），stat_peroid 按需取，"
                   f"各取 stat_date 最新一行，按计算公式组合；优先直取，勿从实体表重算")
        upd = {"name": name, "keywords": m.get("keywords") or [],
               "definition": defn + ptr, "doc": m.get("doc") or "",
               "tables": m.get("tables") or [], "metric_type": m.get("metric_type"),
               "enabled": bool(m.get("enabled"))}
        if m.get("required_predicates"):
            upd["required_predicates"] = m.get("required_predicates")
        ok, _ = cs.upsert_metric(upd, operator=OPERATOR, source="manual")
        if ok:
            added += 1
    print(f"  ✅ 追加指针 {added} 条；跳过（无指针/已有）{skipped} 条")

    # ══ ④ 补 mid 表字段注释 ══
    print("═" * 66)
    print("④ 补 mid_order_stat_indicator_all 字段注释")
    print("═" * 66)
    COMMENTS = {
        "stat_peroid": "周期类型：day/week/month/quarter/year",
        "stat_peroid_value": "周期值：day=YYYY-MM-DD、week=周一日期、month=YYYY-MM、quarter=YYYY-Qn、year=YYYY",
        "tab_source": "数据来源表名（MySQL原始表名，CK对应 ods_/dwd_ 前缀+_all 后缀）",
        "col_name": "指标字段编码 = 北极星指标 base_col_code = 字典 ods_meta_metric_base_dict_info_all.col_code",
        "col_value": "指标值（Float64）。周/月/季/年为截至 stat_date 的周期累计值",
        "stat_date": "数据写入/更新日期。同一(周期类型+周期值+指标编码)有多行，必须取 stat_date 最新(最大)那一行才是最新累计值，禁止 SUM 多行",
    }
    for cname, comment in COMMENTS.items():
        n = cs.set_column_comment(MID_TABLE, cname, comment, source="manual", operator=OPERATOR)
        print(f"  {'✅' if n else '⚠️'} {MID_TABLE}.{cname} ({n} 行)")

    print()
    print("完成。后续：")
    print("  1) .venv/bin/python _seed_semantic_registry.py   # 重建注册表")
    print("  2) ./serve.sh stop（launchd 自动拉起新进程，索引懒加载重建）")


if __name__ == "__main__":
    main()
