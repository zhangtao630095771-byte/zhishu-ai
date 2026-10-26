# -*- coding: utf-8 -*-
"""
check_consistency.py —— 知识库配置 与 真实库结构 的一致性自检

★ 为什么需要这个？
  知识库里的东西（提示词、指标口径、字典的适用字段）都是【手写/复制】的，
  而真实表结构会变（换库、改表、加字段）。
  一旦漂移，症状是【静默的】：

    提示词写 `create_time`，真实列是 `ctime`
      → 模型照着写 → ClickHouse 报错 → 白跑一次重试
      → 用户只看到"慢"，看不到"为什么慢"（实测踩过这个坑）

    指标口径写了某张表，但那张表后来改名了
      → 召回指标后注入一段查不通的口径 → 模型照着编
      → SQL 能跑、数字全错

  这类问题不会自己暴露，只能【主动查】。所以做成脚本，改完配置就跑一遍。

★ 检查四项：
  ① 提示词（role / sql_rules）里提到的列名，是否真实存在
  ② 指标口径里引用的表名 / 列名
  ③ 字典的「适用字段」是不是真实列名（填错会让模型把编码套到别的列上）
  ④ 知识库覆盖情况（表注释 / 字段注释 / 指标 / 字典）

★ 不调大模型、不联网，几秒跑完。

运行： .venv/bin/python check_consistency.py
"""
import re
import sys

import config
import config_store
import db


# ═══════════════════════════════════════════════════════════
# 取真实结构
# ═══════════════════════════════════════════════════════════
NOISE = ("_local", "_mv", ".inner_id", "_bak", "_tmp")

# SQL 关键字 / 通用词 —— 这些不是列名，别拿来核对
STOPWORDS = {
    "select", "from", "where", "group", "order", "limit", "offset", "and", "or", "not",
    "null", "count", "sum", "avg", "max", "min", "case", "when", "then", "else", "end",
    "as", "coalesce", "union", "all", "insert", "update", "delete", "drop", "create",
    "join", "inner", "left", "right", "on", "in", "is", "between", "like", "cast",
    "distinct", "having", "asc", "desc", "div", "mod", "true", "false", "nullif",
    "int", "float", "string", "decimal", "date", "datetime", "timestamp", "time",
    "clickhouse", "mysql", "sqlite", "format", "native", "utf", "yyyy", "mm", "dd",
    "table", "tables", "sql", "value", "values", "total", "over", "partition", "case_when",
}


def load_real_structure():
    """
    抓真实表名与列名

    ★ 只对【会进 RAG 的表】拉列名，不对全库逐表查。
      原因：ClickHouse 走 SSH 隧道，全库 741 张表逐表查一次要好几十秒，
      实测直接把进程拖到被 kill（exit 137）。
      而漂移检查关心的本来就是"进 RAG 的配置"引用的列 ——
      提示词、指标、字典只会提到可见表的列（未授权的表压根不注入），
      所以拿可见表的列名核对是【正确】且够用的。
    """
    all_tables = set(t for t in db.list_tables() if not any(k in t for k in NOISE))
    visible = set(config_store.filter_visible_tables(all_tables, config.ACL_DEFAULT_VISIBLE))

    cols_by_table = {}                       # {表名: {列名: 类型}}
    for t in sorted(visible):
        try:
            cols_by_table[t] = {c: ty for c, ty in db.list_columns(t)}
        except Exception:
            continue                          # 个别表读不到不影响整体体检

    return all_tables, visible, cols_by_table


# ★ 否定语境标记：这些词所在的行里出现的列名是【反例】，不是要核对的列名
#   例：规则里写「禁止凭记忆写 create_time 这类列名」——
#       这里 create_time 是明确告诉模型"别写"的反面教材，保留它很有价值
#       （模型确实倾向于写 create_time），所以不该被判成配置漂移。
NEGATIVE_MARKERS = ("禁止", "不要", "不准", "错误", "别写", "严禁", "不能", "不存在", "负面")


def extract_identifiers(text):
    """
    从一段文本里挑出"像列名"的标识符

    ★ 只挑【含下划线】的（create_time / order_count / app_entity_source）：
      这类名字最有辨识度，也最容易在换库/改表后漂移。
      纯单词型（status / total / ctime）误报率太高（一堆英文单词看着都像列名），
      真要核对得靠人工，脚本不强行猜。
    ★ 先剔除 `AS xxx` 里的别名 —— 那是 SQL 示例里的输出列名，本就不该存在于表里
      （否则 `AS order_count` 会被当成幽灵列名误报）。
    ★ 再剔除【否定语境】所在行的列名 —— 那是"别这么写"的反例，不是配置引用。
    """
    found = []
    for line in text.split("\n"):
        line = re.sub(r"\bAS\s+`?[A-Za-z_][A-Za-z0-9_]*`?", " ", line, flags=re.I)
        if any(m in line for m in NEGATIVE_MARKERS):    # 反例行 → 整行跳过
            continue
        found.extend(re.findall(r"\b([a-z][a-z0-9]*_[a-z0-9_]+)\b", line))
    return sorted({i for i in found if i not in STOPWORDS})


# ═══════════════════════════════════════════════════════════
# 报告工具
# ═══════════════════════════════════════════════════════════
PROBLEMS = []                                # 收集所有问题，最后汇总


def problem(area, label, detail, hint=""):
    PROBLEMS.append((area, label, detail))
    print(f"  ❌ {label}")
    print(f"     {detail}")
    if hint:
        print(f"     → {hint}")


def ok(label):
    print(f"  ✅ {label}")


def warn(label, detail):
    print(f"  ⚠️ {label}")
    print(f"     {detail}")


# ═══════════════════════════════════════════════════════════
# ① 提示词里的列名
# ═══════════════════════════════════════════════════════════
def check_prompts(all_cols):
    print()
    print("─" * 70)
    print("① 提示词里提到的列名（应为真实存在的列）")
    print("─" * 70)

    for key in ("role", "sql_rules"):
        text = config_store.get_prompt(key)
        if not text:
            warn(f"提示词 {key} 为空", "模型将缺少这部分指令")
            continue
        idents = extract_identifiers(text)
        if not idents:
            ok(f"{key}：没有写死列名（最稳，不会因换库漂移）")
            continue
        ghosts = [i for i in idents if i not in all_cols]
        if ghosts:
            problem(
                "prompt", f"{key} 里提到了会进 RAG 的表里没有的列名：{ghosts}",
                "模型会照着写，导致 ClickHouse 报错并白白重试一次",
                "改成从表结构里挑的通用表述（如「时间列」），不要写死列名",
            )
        else:
            ok(f"{key}：写死的列名都存在 → {idents}")


# ═══════════════════════════════════════════════════════════
# ② 指标口径
# ═══════════════════════════════════════════════════════════
def check_metrics(all_tables, all_cols):
    print()
    print("─" * 70)
    print("② 指标口径里引用的表名 / 列名")
    print("─" * 70)

    metrics = config_store.get_metrics(enabled_only=False)
    if not metrics:
        warn("还没有配置任何指标", "指标口径是聚合类问题算对的前提")
        return

    for m in metrics:
        d = m.get("definition") or ""
        name = f"{m['name']}（{'开放' if m['enabled'] else '停用'}）"

        tabs = set(re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", d, flags=re.I))
        ghost_tabs = sorted(t for t in tabs if t not in all_tables)

        ghost_cols = [c for c in extract_identifiers(d) if c not in all_cols]

        if ghost_tabs or ghost_cols:
            detail = []
            if ghost_tabs:
                detail.append(f"引用了不存在的表 {ghost_tabs}")
            if ghost_cols:
                detail.append(f"引用了不存在的列 {ghost_cols}")
            problem("metric", f"{name} 的口径与真实结构不符",
                    "；".join(detail),
                    "改口径定义，或确认这张表是否已改名/下线")
        else:
            ok(f"{name} 口径正常")

    # 停用的指标也要看：它们不进索引，但配错了说明配置没维护
    disabled = [m["name"] for m in metrics if not m["enabled"]]
    if disabled:
        print(f"  ℹ️ 已停用（不进索引）：{'、'.join(disabled)}")


# ═══════════════════════════════════════════════════════════
# ③ 字典的适用字段
# ═══════════════════════════════════════════════════════════
def check_dicts(all_cols, cols_by_table):
    print()
    print("─" * 70)
    print("③ 字典的「适用字段」（填错会让模型把编码套到别的列上）")
    print("─" * 70)

    entries = config_store.get_dict_entries(enabled_only=False)
    if not entries:
        warn("还没有配置任何字典", "问「车拖车有多少单」这类问题时，模型会自己编条件")
        return

    for d in entries:
        name = d["name"]
        f = (d["field_hint"] or "").strip()

        if not d["items"]:
            problem("dict", f"字典「{name}」没有任何取值",
                    "空字典不进索引 → 配了等于没配",
                    "在页面上补取值，或用「从数据库导入」")
            continue

        if not f:
            problem("dict", f"字典「{name}」没填适用字段",
                    f"{len(d['items'])} 条取值无法对应到任何列，模型可能套到别的列上",
                    "填写它对应的列名（如 app_entity_source）")
            continue

        if f not in all_cols:
            problem("dict", f"字典「{name}」的适用字段 '{f}' 不是任何会进 RAG 的表的列名",
                    "模型会照着写出报错的 SQL",
                    "改成真实列名（见「表结构信息」页面）")
            continue

        where = [t for t, cs in cols_by_table.items() if f in cs]
        ok(f"字典「{name}」→ {f}（{len(d['items'])} 条，表 {where}）")


# ═══════════════════════════════════════════════════════════
# ④ 覆盖情况
# ═══════════════════════════════════════════════════════════
def check_coverage(visible, cols_by_table):
    print()
    print("─" * 70)
    print("④ 知识库覆盖情况（会进 RAG 的表）")
    print("─" * 70)

    table_c = config_store.get_table_comments()
    col_c = config_store.get_column_comments()

    try:
        db_cols = db.fetch_comments(sorted(visible)).get("columns", {})
    except Exception as e:
        db_cols = {}
        warn("抓取数据库自带注释失败", str(e)[:80])

    tot = com = man = 0
    for t in sorted(visible):
        cols = cols_by_table.get(t, {})
        t_tot = len(cols)
        t_com = t_man = 0
        for c in cols:
            key = f"{t}.{c}"
            is_man = bool(col_c.get(key))
            if is_man or db_cols.get(key):
                t_com += 1
            if is_man:
                t_man += 1
        tot += t_tot
        com += t_com
        man += t_man

        has_comment = bool(table_c.get(t))
        mark = "✅" if has_comment else "⚠️"
        pct = round(t_com / t_tot * 100) if t_tot else 0
        print(f"  {mark} {t:<24} 表注释={'有' if has_comment else '无'} "
              f"| 字段 {t_com}/{t_tot} ({pct}%) | 人工 {t_man}")
        if not has_comment:
            warn(f"表 {t} 没有表注释",
                 "表注释影响「模型选哪张表」，一句话就能补，建议补上")

    pct = round(com / tot * 100) if tot else 0
    print()
    print(f"  合计：字段注释 {com}/{tot}（{pct}%），其中人工维护 {man} 条")
    print(f"       指标 {len(config_store.get_metrics())} 个"
          f" / 字典 {len(config_store.get_dict_entries())} 个")
    if pct < 80:
        warn("字段注释覆盖率偏低",
             "字段注释是 text-to-SQL 准确率最直接的杠杆，优先补枚举类字段")


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
def main():
    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + "知识库配置 × 真实库结构 一致性自检".center(60) + "║")
    print("╚" + "═" * 68 + "╝")

    print("\n正在读取真实表结构…")
    all_tables, visible, cols_by_table = load_real_structure()
    all_cols = set()
    for cs in cols_by_table.values():
        all_cols.update(cs)
    print(f"  全库 {len(all_tables)} 张表 / {len(all_cols)} 个列名"
          f"（其中会进 RAG 的 {len(visible)} 张）")

    check_prompts(all_cols)
    check_metrics(all_tables, all_cols)
    check_dicts(all_cols, cols_by_table)
    check_coverage(visible, cols_by_table)

    print()
    print("=" * 70)
    if not PROBLEMS:
        print("🎉 一致性检查通过：知识库里写死的表名/列名都能对上真实结构")
    else:
        print(f"⚠️ 发现 {len(PROBLEMS)} 个「配置与真实结构不符」的问题：")
        for i, (area, label, _) in enumerate(PROBLEMS, 1):
            print(f"   {i}. [{area}] {label}")
        print()
        print("   这类问题不会自己暴露（不报错、只是慢或算错），改完配置请重跑本脚本。")
    print("=" * 70)
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    sys.exit(main())
