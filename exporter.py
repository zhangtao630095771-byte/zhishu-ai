# -*- coding: utf-8 -*-
"""
exporter.py —— 知识库导出（JSON / Markdown）

★ 为什么要做导出？
  知识库不是"一次配好"的，而是【用出来的】：
      上线时只有零星几条注释
      → 使用中踩到幻觉（模型把 status=6 当成"已取消"）
      → 回头补字段注释、加字典、修口径
      → 越用越准
  这个过程沉淀下来的东西，就是公司真正的数据资产。
  如果导出不了，这份资产就被锁死在这个工具里：
      换台机器要手抄、给数据团队评审要截图、改坏了一个版本回不去。
  所以导出不是"附加功能"，它是"知识库能被持续经营"的前提。

★ 两种格式，各有用途：
    JSON      —— 结构完整，用于【备份 / 迁移 / 程序化再导入】
                 带 format_version，将来做导入时好校验兼容性
    Markdown  —— 人可读，用于【评审 / 分享 / 存档】
                 可以直接发给数据团队让他们补注释，也能进 Git 做版本对比

★ 两种范围：
    all     —— 完整知识库（人工注释 + 数据库自带注释），用于备份
    manual  —— 只导出【人工维护】的部分，用于跨库迁移/沉淀
                （数据库自带注释跟着库走，导到别的库没意义）
"""
import json
import re
from datetime import datetime

import config
import config_store
import db
import knowledge


FORMAT_VERSION = 1          # ★ 导出格式版本号：将来做「导入」时靠它判断兼容性


# ═══════════════════════════════════════════════════════════
# ① 汇总：把所有知识读成一个结构化字典
# ═══════════════════════════════════════════════════════════
def collect(scope="all"):
    """
    汇总当前知识库

    参数：
        scope = "all"     完整知识库（含数据库自带注释）
                "manual"  只导出人工维护的部分

    返回：可直接 json.dumps 的字典
    """
    only_manual = (scope == "manual")

    # ── 会进 RAG 的表（与页面、自检脚本共用同一份规则）──
    business = knowledge.business_tables()
    visible = config_store.filter_visible_tables(set(business), config.ACL_DEFAULT_VISIBLE)

    # ── 三类注释 ──
    #   ★ 表注释有两层来源，导出时必须分开标：
    #     manual = 人写的 / 在 AI 基础上改过的定稿 → 标注「人工」
    #     ai     = AI 自动生成的临时注释（已进 RAG，人还没改）→ 标注「AI 生成」
    #   不分开的话，导出文件会把机器草稿说成人工审定，
    #   "哪些经过人把关"这个信息就丢了 —— 拿到文件的人（数据团队 / 迁移后的环境）
    #   没法判断该重点复核哪些表。
    table_c = config_store.get_table_comments()          # {表: 注释}（人工和 AI 混在一起）
    table_s = config_store.get_table_comment_sources()   # {表: manual|ai}
    col_c = config_store.get_column_comments()           # 人工字段注释 {"表.字段": 注释}
    #   ★ 一次抓回所有可见表的库自带注释（别在循环里逐表查，SSH 隧道下会很慢）
    db_c = db.fetch_comments(sorted(visible))
    db_table_c = db_c.get("tables", {})
    db_col_c = db_c.get("columns", {})

    # ── 逐表组装 ──
    tables = []
    for t in sorted(visible):
        #   按下表的 source 列拆开：同一个 table_comments 里装着两种东西
        src_t = table_s.get(t, "")
        ai_table = table_c.get(t, "") if src_t == "ai" else ""
        manual_table = table_c.get(t, "") if src_t == "manual" else ""
        db_table = db_table_c.get(t, "")

        columns = []
        for col, dtype in db.list_columns(t):
            key = f"{t}.{col}"
            manual = col_c.get(key, "")
            from_db = db_col_c.get(key, "")
            if only_manual and not manual:               # 仅人工：库自带的跳过
                continue
            columns.append({
                "column": col,
                "type": dtype,
                "comment": manual or from_db,            # 生效的那条（人工优先）
                "comment_source": "manual" if manual else ("db" if from_db else ""),
                "manual_comment": manual,                # 人工写的那条（可能为空）
                "db_comment": from_db,                   # 库自带的那条（可能为空）
            })

        # 仅人工模式下，这张表没有任何"非库自带"的知识 → 整张跳过
        #   （AI 生成的临时注释算非库自带 —— 它不跟着库走，迁移时丢了就得重新花钱生成一遍）
        if only_manual and not manual_table and not ai_table and not columns:
            continue

        tables.append({
            "table": t,
            #   优先级和 RAG 保持一致：人工 > AI > 库自带
            "comment": manual_table or ai_table or db_table,
            "comment_source": "manual" if manual_table
                              else ("ai" if ai_table else ("db" if db_table else "")),
            "manual_comment": manual_table,
            "ai_comment": ai_table,                  # AI 生成的那条（可能为空）
            "db_comment": db_table,
            "visible": True,
            "columns": columns,
        })

    # ── 指标（都是人工配的，两种范围都全量带）──
    metrics = [{
        "name": m["name"],
        "keywords": m.get("keywords") or [],
        "definition": m.get("definition") or "",
        "doc": m.get("doc") or "",
        "tables": m.get("tables") or [],
        "enabled": bool(m.get("enabled")),
    } for m in config_store.get_metrics(enabled_only=False)]

    # ── 数据字典（同上）──
    dicts = [{
        "name": d["name"],
        "dict_type": d.get("dict_type") or "",
        "field_hint": d.get("field_hint") or "",
        "keywords": d.get("keywords") or [],
        "items": d.get("items") or [],
        "enabled": bool(d.get("enabled")),
    } for d in config_store.get_dict_entries(enabled_only=False)]

    # ── 提示词 ──
    prompts = {
        "role": config_store.get_prompt("role") or "",
        "sql_rules": config_store.get_prompt("sql_rules") or "",
    }

    # ── 统计（放在开头，评审的人第一眼就想知道覆盖到什么程度）──
    cols_total = cols_commented = cols_manual = 0
    for t in tables:
        for c in t["columns"]:
            cols_total += 1
            if c["comment"]:
                cols_commented += 1
            if c["manual_comment"]:
                cols_manual += 1

    # ── 孤儿注释：人工写了、但对应的表当前不可见（未授权）或压根不存在 ──
    #   ★ 为什么不静默丢掉？
    #     这类注释多数是早期演示/试验留下的残留（本库有 5 条指向 dim_user、
    #     dwd_refund 等不存在的表），但也可能是"知识写好了、表还没授权/还没建"。
    #     导出文件是要拿去存档和评审的，少了几条却不吭声，
    #     到时候"导出和页面/配置库对不上账"，就没人敢信这份文件了。
    orphans = []
    for t in table_c:
        if t not in visible:
            orphans.append((t, "表注释"))
    for key in col_c:
        if key.rsplit(".", 1)[0] not in visible:
            orphans.append((key, "字段注释"))

    warnings = []
    if orphans:
        names = "、".join(sorted({n for n, _ in orphans}))
        warnings.append(
            f"有 {len(orphans)} 条人工知识指向的表当前不可见（未授权或不存在），"
            f"未包含在本次导出中：{names}"
        )

    return {
        "format_version": FORMAT_VERSION,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": scope,
        "source": {
            "db_type": config.DB_TYPE,
            "database": getattr(config, "CK_DATABASE", "") if config.DB_TYPE == "clickhouse" else "",
            "sql_dialect": config.SQL_DIALECT,
        },
        "stats": {
            "tables": len(tables),
            "columns_total": cols_total,
            "columns_commented": cols_commented,
            "columns_manual": cols_manual,
            "metrics": len(metrics),
            "metrics_enabled": sum(1 for m in metrics if m["enabled"]),
            "dicts": len(dicts),
            "dicts_enabled": sum(1 for d in dicts if d["enabled"]),
        },
        "warnings": warnings,
        "prompts": prompts,
        "tables": tables,
        "metrics": metrics,
        "dicts": dicts,
    }


# ═══════════════════════════════════════════════════════════
# ② JSON 格式（备份 / 迁移）
# ═══════════════════════════════════════════════════════════
def to_json(data):
    """
    导出为 JSON 字符串

    ★ ensure_ascii=False：中文直接写原文，别转成 \\uXXXX。
      导出文件是给人看、给 Git 存、给评审的 —— 一屏乱码没人愿意用。
    """
    return json.dumps(data, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════
# ③ Markdown 格式（评审 / 分享 / 存档）
# ═══════════════════════════════════════════════════════════
def _cell(text):
    """
    把一段文本塞进 Markdown 表格单元格

    ★ 表格里 "|" 会被当成列分隔符、"换行"会直接断掉表格 ——
      库里的注释恰好经常带这两种（"订单状态：2待接单 3已接单"还好，
      但写成多行或带竖线的就中招了）。不处理的话导出的表格会错位，
      读的人会以为数据本来就乱。
    """
    s = str(text if text is not None else "")
    return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def _source_cn(src):
    """来源的中文写法（保持和页面一致：人工 / AI 生成 / 库 / 待补）"""
    return {"manual": "人工", "ai": "AI 生成·未复核", "db": "库"}.get(src, "待补")


def _fence(text):
    """
    挑一个不会和内容打架的代码围栏

    ★ 为什么不能无脑写 ``` ？
      我们的 sql_rules 里恰好有一条写着「只输出 SQL，不要用 ``` 包裹」——
      这段规则本身含三个反引号。用它去包代码块，那三个反引号会被 Markdown
      当成【代码块结束】，后面的内容全跑到块外面，导出的文件结构直接乱掉。
      （这是真实踩到的：写规则时在教模型"别用 ```"，而导出时又被它绊了一跤。）

    做法：看内容里最长的连续反引号有几条，围栏就比它长一条，最少 3 条。
    """
    runs = re.findall(r"`+", text or "")
    longest = max((len(r) for r in runs), default=0)
    return "`" * max(3, longest + 1)


def _code_block(text, lang=""):
    """把一段文本包成 Markdown 代码块（自动选安全长度的围栏）"""
    body = (text or "").strip()
    fence = _fence(body)
    return [fence + lang, body, fence]


def to_markdown(data):
    """导出为 Markdown 字符串"""
    st = data["stats"]
    src = data["source"]
    scope_cn = "完整知识库（含数据库自带注释）" if data["scope"] == "all" else "仅人工维护的知识"

    L = []
    L.append("# 数据业务知识库")
    L.append("")
    L.append(f"> 导出时间：{data['exported_at']}  ")
    L.append(f"> 数据源：{src['db_type']} / {src['database'] or '（未指定库名）'}  ")
    L.append(f"> 导出范围：{scope_cn}  ")
    L.append(f"> 格式版本：v{data['format_version']}")
    L.append("")
    L.append("## 覆盖情况")
    L.append("")
    L.append("| 项目 | 数量 |")
    L.append("| --- | --- |")
    L.append(f"| 表 | {st['tables']} |")
    L.append(f"| 字段 | {st['columns_total']}（有注释 {st['columns_commented']}，其中人工维护 {st['columns_manual']}） |")
    L.append(f"| 指标口径 | {st['metrics']}（已开放 {st['metrics_enabled']}） |")
    L.append(f"| 数据字典 | {st['dicts']}（已开放 {st['dicts_enabled']}） |")
    L.append("")
    # ★ 导出不了的东西必须写出来 —— 否则这份文件会给人"知识库就这么多"的错觉
    for w in (data.get("warnings") or []):
        L.append(f"> ⚠️ {w}")
        L.append("")

    # ── 一、提示词 ──
    L.append("## 一、提示词")
    L.append("")
    L.append("### role（模型身份）")
    L.append("")
    L.extend(_code_block(data["prompts"]["role"], "text"))
    L.append("")
    L.append("### sql_rules（SQL 生成规则）")
    L.append("")
    L.extend(_code_block(data["prompts"]["sql_rules"], "text"))
    L.append("")

    # ── 二、表与字段 ──
    L.append("## 二、表结构与字段注释")
    L.append("")
    if not data["tables"]:
        L.append("_（没有可导出的表）_")
        L.append("")
    for t in data["tables"]:
        L.append(f"### {t['table']}")
        L.append("")
        if t["comment"]:
            L.append(f"**表注释**（{_source_cn(t['comment_source'])}）：{t['comment']}")
        else:
            L.append("**表注释**：_（待补充）_")
        L.append("")
        if not t["columns"]:
            L.append("_（该表没有需要导出的字段）_")
            L.append("")
            continue
        L.append("| 字段 | 类型 | 注释 | 来源 |")
        L.append("| --- | --- | --- | --- |")
        for c in t["columns"]:
            L.append(f"| {_cell(c['column'])} | {_cell(c['type'])} | "
                     f"{_cell(c['comment']) or '_（待补充）_'} | {_source_cn(c['comment_source'])} |")
        L.append("")

    # ── 三、指标口径 ──
    L.append("## 三、指标口径")
    L.append("")
    if not data["metrics"]:
        L.append("_（还没有配置指标）_")
        L.append("")
    for m in data["metrics"]:
        flag = "" if m["enabled"] else "　🔒 已停用（不注入模型）"
        L.append(f"### {m['name']}{flag}")
        L.append("")
        if m["keywords"]:
            L.append(f"- **召回关键词**：{'、'.join(m['keywords'])}")
        if m["tables"]:
            L.append(f"- **依赖表**：{'、'.join(m['tables'])}")
        L.append("")
        L.extend(_code_block(m["definition"], "sql"))
        if m["doc"]:
            L.append("")
            L.append(f"> 语义描述：{m['doc']}")
        L.append("")

    # ── 四、数据字典 ──
    L.append("## 四、数据字典")
    L.append("")
    if not data["dicts"]:
        L.append("_（还没有配置数据字典）_")
        L.append("")
    for d in data["dicts"]:
        flag = "" if d["enabled"] else "　🔒 已停用（不注入模型）"
        L.append(f"### {d['name']}{flag}")
        L.append("")
        if d["field_hint"]:
            L.append(f"- **适用字段**：`{d['field_hint']}`")
        if d["keywords"]:
            L.append(f"- **召回关键词**：{'、'.join(d['keywords'])}")
        if d["dict_type"]:
            L.append(f"- **来源字典类型**：`{d['dict_type']}`")
        L.append("")
        if not d["items"]:
            L.append("_（没有取值，该字典不会生效）_")
            L.append("")
            continue
        L.append("| 编码 | 含义 |")
        L.append("| --- | --- |")
        for it in d["items"]:
            L.append(f"| {_cell(it.get('value'))} | {_cell(it.get('label'))} |")
        L.append("")

    L.append("---")
    L.append("")
    L.append(f"_本文件由 知数AI 导出（格式版本 v{data['format_version']}），"
             f"可直接发给数据团队评审与补充。_")
    L.append("")
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════
# ④ 文件名
# ═══════════════════════════════════════════════════════════
def filenames(scope="all", fmt="json"):
    """
    生成文件名，返回 (ascii 兜底名, 中文名)

    ★ 为什么要带时间戳？
      导出多半是"存档一个版本"。不带时间戳的话，
      下载第二个版本时浏览器会存成 "知识库-完整 (1).json"，
      过两天就分不清哪个是哪个了。

    ★ 为什么要给 ascii 兜底名？
      HTTP 头里放非 ASCII 字符有讲究：现代写法是 RFC 5987 的
      `filename*=UTF-8''<百分号编码>`，但老客户端不认。
      所以两个都给：新客户端用中文名，老的回退到 ascii 名，谁都不空手。
    """
    scope_cn = "完整" if scope == "all" else "仅人工"
    scope_en = "all" if scope == "all" else "manual"
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    ext = "json" if fmt == "json" else "md"
    return f"knowledge-base-{scope_en}-{ts}.{ext}", f"知识库-{scope_cn}-{ts}.{ext}"
