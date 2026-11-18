# -*- coding: utf-8 -*-
"""
导入北极星指标体系 + KPI 体系知识到知数AI（幂等、可重复执行）

★ 背景（2026-11-18）
  钉钉知识库《KPI数据设计方案》《北极星数据设计方案》定义了公司指标体系：
    - 北极星：指标层→字典层→数仓层→聚合层 四层结构
      · 元数据表 dim_meta_metric_norths_star_info_all（325 条指标定义，已同步 CK）
      · 基础字典表 ods_meta_metric_base_dict_info_all（706 条，含计算逻辑/统计范围/来源表，已同步 CK）
      · 数据表 dws_metric_norths_star_{day,week,month,year}_data（在 MySQL ctc_metric 库，未同步 CK）
    - KPI：ctc_kpi_dim_{business,department,metric} + ctc_kpi_fact_monthly_{plan,kpi} 宽表（MySQL，未同步 CK）

★ 本脚本做的事（全部走 config_store 官方写入入口，带治理 history/version/status）
  ① 开表权限（table_acl，白名单模式）：2 张元数据表 + 25 张指标权威来源表
  ② 导入 325 条北极星指标到 metrics 表（P1 层，definition 组装口径+公式+统计范围+权威来源表）
     · 跳过 3 条与现有指标重名（完单量/小板完单量/代驾完单量）——保留知数AI已治理的更具体口径
  ③ 写入 kb_doc《北极星与KPI指标体系架构与取数路由规则》——解决"取指标数据 vs 取原实体表"路由问题
  ④ 在 sql_rules（P0）追加一条【指标取数路由】规则

★ 执行后需：.venv/bin/python _seed_semantic_registry.py 重建注册表，然后 ./restart.sh 重启服务

运行： .venv/bin/python _seed_northstar_metrics.py
"""
import re
import db
import config_store as cs

OPERATOR = "admin"

# ── 与知数AI现有指标重名，跳过导入（保留已治理口径） ──
SKIP_NAMES = {"完单量", "小板完单量", "代驾完单量"}

# ── 指标体系元数据表（CK 已同步，开权限让 AI 可查指标定义本身） ──
META_TABLES = [
    ("dim_meta_metric_norths_star_info_all", "北极星指标元数据表（325条指标定义/口径/负责人），用户导入指标体系 2026-11-18"),
    ("ods_meta_metric_base_dict_info_all", "基础指标字典（706条：计算逻辑/统计范围/来源表），用户导入指标体系 2026-11-18"),
]


def _clean(s):
    """清洗文本：去首尾空白、统一换行"""
    if not s or not isinstance(s, str):
        return ""
    return s.strip().strip("\r\n").strip()


def guess_metric_type(name):
    """按指标名称启发式推导度量类型（与现有 metrics 的 metric_type 语义一致）"""
    if any(k in name for k in ("率", "占比", "比例", "百分比")):
        return "ratio"
    if any(k in name for k in ("金额", "费", "额", "收入", "成本", "信息费", "GMV")):
        return "amount"
    if any(k in name for k in ("量", "数", "次数", "人数", "单数", "分")):
        return "count"
    return "other"


def build_definition(n, dict_items, ck_tables, raw_tabs):
    """组装 P1 权威口径文本（会以【MUST】注入 prompt，必须自含完整计算指引）"""
    lines = [_clean(n["scope"]) or f"{n['name']}（北极星指标，口径见元数据表 id={n['id']}）"]
    if _clean(n["formula"]):
        lines.append(f"  ★ 计算公式：{_clean(n['formula'])}")
    d = dict_items[0] if dict_items else None
    if d:
        if d.get("logic"):
            lines.append(f"  ★ 基础统计逻辑：{d['logic']}")
        if d.get("scope"):
            lines.append(f"  ★ 统计范围约束：{d['scope']}")
    if ck_tables:
        lines.append(f"  ★ 权威口径来源表（取数必须用这些表）：{'、'.join(ck_tables)}"
                     f"（北极星字典源表：{raw_tabs}）")
    else:
        lines.append(f"  ★ 字典源表 {raw_tabs} 未同步数仓（ClickHouse），当前无权威来源表；"
                     "取数前须与指标负责人确认口径，不得从近似实体表猜测计算")
    lines.append(f"  ★ 口径来源：北极星指标体系 dim_meta_metric_norths_star_info_all（id={n['id']}），"
                 f"{_clean(n['dept']) or '未登记部门'} / 负责人 {_clean(n['owner']) or '未登记'}")
    return "\n".join(lines)


def main():
    cs._ensure_ready()

    # ══ 0. 读取 CK 数据 ══
    rows = db.execute_sql(
        "SELECT id, name, scope, calc_formula, base_col_code, level, type, department, owner "
        "FROM dim_meta_metric_norths_star_info_all WHERE is_deleted = 0")
    north = [dict(zip(["id", "name", "scope", "formula", "base_col", "level",
                       "type", "dept", "owner"], r)) for r in rows[1]]
    print(f"北极星有效指标：{len(north)} 条")

    dict_rows = db.execute_sql(
        "SELECT col_code, col_name, business_def, calc_logic, stat_scope, tab_source "
        "FROM ods_meta_metric_base_dict_info_all WHERE is_active = 1")
    dmap = {}
    for r in dict_rows[1]:
        code = r[0]
        dmap.setdefault(code, []).append(dict(
            name=_clean(r[1]), bdef=_clean(r[2]), logic=_clean(r[3]),
            scope=_clean(r[4]), tab=_clean(r[5])))

    ck_tables = set(db.list_tables())

    def map_tab(t):
        t = t.strip()
        if t in ck_tables:
            return t
        for cand in (f"ods_{t}_all", f"dwd_{t}_all", f"{t}_all", f"ods_{t}", f"dwd_{t}"):
            if cand in ck_tables:
                return cand
        return None

    # ══ ① 开表权限 ══
    print("═" * 66)
    print("① 开表权限（table_acl，白名单模式）")
    print("═" * 66)
    opened = set()
    for tbl, note in META_TABLES:
        cs.set_table_acl(tbl, True, note)
        opened.add(tbl)
        print(f"  ✅ {tbl}")

    # 收集所有指标涉及的 CK 来源表（导入时也会用到）
    all_ck_deps = set()
    for n in north:
        codes = [c.strip() for c in (str(n["base_col"]) or "").split(",") if c.strip()]
        for c in codes:
            for it in dmap.get(c, []):
                for p in str(it["tab"]).split("/"):
                    m = map_tab(p)
                    if m:
                        all_ck_deps.add(m)
    for tbl in sorted(all_ck_deps):
        cs.set_table_acl(tbl, True, "北极星指标体系权威来源表（指标口径依赖），2026-11-18 导入")
        opened.add(tbl)
        print(f"  ✅ {tbl}")
    print(f"  → 共开 {len(opened)} 张表权限")

    # ══ ② 导入指标 ══
    print("═" * 66)
    print("② 导入北极星指标到 metrics（P1 层）")
    print("═" * 66)
    ok_cnt, skip_cnt, no_table_cnt = 0, 0, 0
    for n in north:
        name = _clean(n["name"])
        if not name:
            continue
        if name in SKIP_NAMES:
            skip_cnt += 1
            print(f"  ⏭️  {name}（与现有指标重名，保留已治理口径）")
            continue
        codes = [c.strip() for c in (str(n["base_col"]) or "").split(",") if c.strip()]
        dict_items = []
        for c in codes:
            dict_items.extend(dmap.get(c, []))
        # 来源表：合并字典条目里所有 tab 的映射
        ck_tabs, raw_tabs = [], []
        for it in dict_items:
            for p in str(it["tab"]).split("/"):
                p = p.strip()
                if not p:
                    continue
                if p not in raw_tabs:
                    raw_tabs.append(p)
                m = map_tab(p)
                if m and m not in ck_tabs:
                    ck_tabs.append(m)
        definition = build_definition(n, dict_items, ck_tabs, "、".join(raw_tabs) or "未登记")
        keywords = [name]
        v = name.replace("周期内", "").replace("当月", "")
        if v and v != name:
            keywords.append(v)
        metric = {
            "name": name,
            "keywords": keywords,
            "definition": definition,
            "doc": (f"{name}：{_clean(n['scope'])}。"
                    f"（北极星{'公司级' if n['level'] == 0 else '部门级'}指标，"
                    f"{_clean(n['dept']) or ''}，负责人 {_clean(n['owner']) or '未登记'}）"),
            "tables": ck_tabs,
            "metric_type": guess_metric_type(name),
            "enabled": True,
        }
        # 字典有明确统计范围约束时，把关键过滤条件提炼为机器可校验的硬约束
        preds = []
        d = dict_items[0] if dict_items else None
        if d and d.get("scope"):
            for frag in re.split(r"[，,。；;\n]", d["scope"]):
                frag = frag.strip()
                if not frag:
                    continue
                # 只保留形如 key=value / key!=value / key in(...) 的可校验片段
                if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*\s*(=|!=|>=|<=|>|<|in\s*\()", frag, re.I):
                    preds.append(frag)
        if preds:
            metric["required_predicates"] = preds
        ok, msg = cs.upsert_metric(metric, operator=OPERATOR, source="manual")
        if ok:
            ok_cnt += 1
            if not ck_tabs:
                no_table_cnt += 1
        else:
            print(f"  ⚠️  {name} → {msg}")
    print(f"  → 成功导入 {ok_cnt} 条（其中 {no_table_cnt} 条无权威来源表，口径文本已注明）；"
          f"跳过重名 {skip_cnt} 条")

    # ══ ③ 指标体系知识文档（P4 层，承载"取数路由"核心规则） ══
    print("═" * 66)
    print("③ 写入《北极星与KPI指标体系架构与取数路由规则》kb_doc")
    print("═" * 66)
    route_doc = {
        "title": "北极星与KPI指标体系架构与取数路由规则（必读）",
        "keywords": ["指标体系", "北极星", "KPI", "指标口径", "取数路由", "指标数据",
                     "指标宽表", "完成率", "同比", "环比", "计划", "达成"],
        "priority": 1,
        "enabled": True,
        "content": """# 北极星与KPI指标体系（公司统一建设，2026-11-18 导入知数AI）

## 一、体系架构（四层：指标层→字典层→数仓层→聚合层）

北极星：
- 指标元数据 dim_meta_metric_norths_star_info_all（CK可查，325条）：name指标名/scope业务口径/calc_formula计算公式/level级别(0公司/1部门)/department部门/owner负责人/parent_id父子层级/base_col_code关联基础字典
- 基础指标字典 ods_meta_metric_base_dict_info_all（CK可查，706条）：col_code基础指标编码/business_def业务定义/calc_logic计算逻辑/stat_scope统计范围/tab_source来源表
- 指标数据表 dws_metric_norths_star_{day,week,month,year}_data（MySQL ctc_metric库，【未同步CK，当前不可查】）：metric_id+value+stat_date/week/month/year，由元数据表 1:N 生成

KPI（月度经营考核）：
- 3张维度表 ctc_kpi_dim_{business(业务线/二级/三级),department(部门),metric(指标名/单位)}
- ctc_kpi_fact_monthly_plan 月度计划事实表（stat_year/stat_month/business_id/dept_id/metric_id/plan_value/is_adjusted/original_plan/adjust_reason/expected_yoy_value/expected_mom_value）
- ctc_kpi_fact_monthly_kpi 月度KPI宽表（冗余全部维度+计划字段，免JOIN直查；自有：actual_value实际值/completion_rate完成率/incomplete_reason/yoy_value同比数值/yoy_growth同比增长率/mom_value环比数值/mom_growth环比增长率）
- 以上5张表在 MySQL ctc_metric 库，【未同步CK，当前不可查】

## 二、取数路由规则（核心：先指标口径，后实体表）

用户问指标数值（如"上月完单量""3月GMV""接单率趋势"）时，按以下优先级取数：

1.【权威路径·当前生效】按 P1 指标定义（prompt中【MUST】条目）的口径取数：计算逻辑、统计范围约束（如 vehicle!=7 排除大板、status=6 完单）、权威来源表都已写明，必须照此生成 SQL，禁止绕过口径自行从任意实体表 count/sum（口径漂移 = 结果不可信）。
2.【指标结果表·暂不可用】体系设计中指标数值应从 dws_metric_norths_star_*_data（北极星）或 ctc_kpi_fact_monthly_kpi（KPI宽表）直取。这些表当前未同步数仓，不要生成引用它们的 SQL。
3.【KPI类问题】用户问"计划/完成率/达成/考核"时：当前无数据表支撑，不要编造；用权威口径从事实表计算实际值，并明确告知"计划与完成率数据尚未接入（ctc_kpi_* 表在MySQL未同步）"。
4.【明细下钻】仅当用户明确要看明细/列表/单据（如"看订单明细""这批司机列表"）时，才直接查原实体表。
5.【同环比】北极星KPI宽表已预存 yoy/mom 字段（未同步）；现阶段同比/环比须用权威口径表按时间窗口自算：同比=本期vs上年同期，环比=本期vs上期。

## 三、指标元数据查询口径

- 查"某指标怎么定义/谁负责/口径是什么" → 查 dim_meta_metric_norths_star_info_all（name匹配，取scope/calc_formula/department/owner）
- 查"基础计算逻辑/来源表" → ods_meta_metric_base_dict_info_all（col_code匹配dim表base_col_code，取calc_logic/stat_scope/tab_source；tab_source是MySQL原始表名，CK对应表为 ods_/dwd_ 前缀+ _all 后缀）
""",
    }
    ok, msg = cs.upsert_kb_doc(route_doc, operator=OPERATOR, source="manual")
    print(f"  {'✅' if ok else '⚠️'} {msg}")

    # ══ ④ sql_rules 追加 P0 路由规则（幂等：已有标记则跳过） ══
    print("═" * 66)
    print("④ sql_rules（P0）追加【指标取数路由】规则")
    print("═" * 66)
    prompts = {r[0]: r[1] for r in
               __import__("sqlite3").connect(
                   "/Users/zhangtao/Downloads/autosql/zhishu-ai/agent_config.db"
               ).execute("SELECT key, content FROM prompts").fetchall()}
    rules = prompts.get("sql_rules", "")
    RULE_BLOCK = """

# 指标取数路由（北极星/KPI 指标体系，2026-11-18）
1. 用户问指标数值（完单量、GMV、接单率等）时，必须按【MUST】指标口径生成 SQL（含计算逻辑、统计范围约束、权威来源表）；禁止绕过口径从任意实体表直接 count/sum。
2. 北极星数据表（dws_metric_norths_star_*_data）与 KPI 表（ctc_kpi_*）尚未同步数仓，禁止生成引用这些表的 SQL。
3. KPI 计划/完成率/考核类问题：用权威口径计算实际值，并明确告知"计划与完成率数据尚未接入"。
4. 仅用户明确要明细/下钻（订单明细、司机列表）时才直接查原实体表。
5. 同比=本期 vs 上年同期，环比=本期 vs 上期，按口径表时间窗口自算。
"""
    if "指标取数路由" in rules:
        print("  ⏭️  已存在，跳过（幂等）")
    else:
        cs.set_prompt("sql_rules", rules.rstrip() + RULE_BLOCK)
        print("  ✅ 已追加")

    print()
    print("完成。后续步骤：")
    print("  1) .venv/bin/python _seed_semantic_registry.py   # 重建语义注册表")
    print("  2) ./restart.sh                                    # 重启服务（重建索引）")


if __name__ == "__main__":
    main()
