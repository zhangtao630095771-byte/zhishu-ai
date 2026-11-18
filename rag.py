# -*- coding: utf-8 -*-
"""
rag.py —— 检索引擎（RAG 的技术实现）
★ 职责：把数据库表结构和业务指标，变成可检索的向量库，并按问题召回相关内容
★ 定位：这是"引擎"，不含业务知识（业务知识在 knowledge.py）
★ 换检索算法（关键词→向量→混合→加Rerank）只改这个文件
"""
import math                          # 开平方用（算向量长度）
import re                            # ★ 从字段注释里截取高信号短语（字段级关键词召回）

import config                        # 配置模块
import config_store                  # ★ 配置存储（读表白名单）
import db                            # 数据库模块
import knowledge                     # 业务知识模块
import governance                    # ★ 知识治理：生产检索按 status 准入

# ★ 注意：这里【故意不】在顶部 import fastembed
#   原因有两个：
#     ① 有些调用方只需要"检索的直接逻辑"（比如 match_disabled_metric 的纯字符串匹配、
#        filter_visible_tables 的权限判断），根本用不到向量模型。
#        在顶部硬性 import，会让这些调用方也无辜地依赖一个几百 MB 的包。
#        （test_acl.py 就是这样的调用方 —— 这个坑我们真实踩过）
#     ② 企业内网环境下，让"能跑的代码"和"要下载模型的代码"解耦，
#        出问题时更容易定位到底是哪一层的问题。
#   → 所以改成"用到时才导入"，见下面的 _load_embed_model()


# ═══════════════════════════════════════════════════════════
# 第 0 块：★ 权限过滤 —— 只保留"允许 AI 访问"的表
# ═══════════════════════════════════════════════════════════
def _filter_visible(schema_rows):
    """
    按表白名单过滤表结构：不可见的表，根本不会进入知识库

    ★ 为什么这一步最有效？
      因为 AI 的知识完全来自我们给它的 RAG 上下文。
      它看不见的表 → 它不知道表名和字段 → 它不可能写出来，也编不出来。
      （这和我们之前"只注入命中的表"防止幻觉，是同一个道理）

    参数 schema_rows = [(表名, 字段名, 类型), ...]（从数据库抓回来的全部）
    返回 过滤后的同格式列表
    """
    all_tables = {r[0] for r in schema_rows}         # 先取出所有表名（集合自带去重）

    visible_tables = config_store.filter_visible_tables(
        all_tables, config.ACL_DEFAULT_VISIBLE
    )
    #   ★ 这里改成调用 config_store 的【批量】判断函数。
    #     原来的写法是"每一个字段行调一次 is_table_visible"，
    #     而后者每次都会新建一个数据库连接 —— 真实库几百张表时会明显拖慢重建索引。
    #     现在只查一次库，剩下的在内存里判断。

    filtered = [r for r in schema_rows if r[0] in visible_tables]
    #           列表推导式：只保留表名在可见集合里的行

    hidden_count = len(all_tables) - len(visible_tables)
    if hidden_count > 0:                                        # 有表被屏蔽
        print(f"⚠️ 权限过滤：已屏蔽 {hidden_count} 张不可见的表")

    return filtered


# ═══════════════════════════════════════════════════════════
# 第 1 块：把数据库抓回来的原始结构，组装成知识库格式
# ═══════════════════════════════════════════════════════════
def _field_keywords(comments, limit=12):
    """从【人工】字段注释里挑高信号短语，作为表的关键词。

    ★ 解决什么问题？
      表向量只包含「表名 + 表注释」，用户只报字段名（如“信息费默认比例”）时，
      表注释里没这个词 → 向量排不上来 → 表召回失败。
      把字段注释的短语加进 keywords，关键词命中优先于向量，能确定性召回。

    ★ 为什么只取第一个标点/括号前的短语？
      “司机认证状态（1=未认证，2=待审核…）” 整段不是用户会写的查询词；
      截成“司机认证状态”才有命中价值。
    """
    out = []
    for c in comments:
        phrase = re.split(r"[（(，,。；;：:]", (c or "").strip())[0].strip()
        if 4 <= len(phrase) <= 20 and phrase not in out:
            out.append(phrase)
        if len(out) >= limit:
            break
    return out


def _build_tables(schema_rows):
    """[(表名,字段名,类型), ...] → RAG 需要的表知识库列表"""
    comments = knowledge.get_table_comments()       # ★ 表注释：人工维护的（优先级最高）
    col_comments = knowledge.get_column_comments()  # ★ 字段注释：人工维护的（优先级最高）
    # ★ 治理准入（状态 + 来源两道闸）：
    #   ① 状态闸：rejected/deprecated/archived 永不可见；candidate 仅非严格模式可见。
    #   ② 来源闸：source='ai' 的注释在严格模式下不进生产（等价 candidate 待遇）。
    #   两道闸都要，缺一不可 —— 只看来源会漏掉"人工录入但后来被判错"的条目，
    #   只看状态会漏掉"状态没同步、但来源是 AI"的条目。
    _t_src = config_store.get_table_comment_sources()
    _t_vis = config_store.get_visible_knowledge_ids("table_comments", "table_name")
    comments = {t: c for t, c in comments.items()
                if (_t_vis is None or t in _t_vis)
                and governance.visible_source(_t_src.get(t))}
    _c_src = config_store.get_column_comment_sources()
    # ★ 字段注释的键是 "表名.字段名"（复合键）——必须用表达式取 id，
    #   否则同名不同表的字段会挤成一条（如 carstate 在多张表含义不同）。
    _c_vis = config_store.get_visible_knowledge_ids(
        "column_comments", "table_name || '.' || column_name")
    col_comments = {k: v for k, v in col_comments.items()
                    if (_c_vis is None or k in _c_vis)
                    and governance.visible_source(_c_src.get(k))}
    # ★ 数据库注释只抓【可见的表】：整库字段注释有 16000+ 行，
    #   会撞上连接的 max_result_rows 上限被杀掉（我们就踩过这个坑，
    #   结果整库字段注释被静默丢弃）；而且没进 RAG 的表，注释也用不上。
    visible_names = sorted({r[0] for r in schema_rows})
    db_comments = db.fetch_comments(visible_names)  # ★ 表/字段注释：从数据库自动发现
    #   【为什么两套注释？】
    #     数据库自带的注释 → 冷启动零成本（真实数仓几百张表，不可能手写）
    #     人工维护的注释   → 能补充/修正业务口径（数据库里往往只写了技术信息）
    #   优先级：人工 > 数据库 > 表名兜底

    table_c = db_comments.get("tables", {})         # 数据库里的表注释
    column_c = db_comments.get("columns", {})       # 数据库里的字段注释

    grouped = {}                                    # 空字典：表名 → 字段行列表
    col_names = {}                                  # 空字典：表名 → 字段名列表（结构化列清单，供 SQL 列级校验）
    manual_used = 0                                 # 人工写的字段注释用了几条
    ai_used = 0                                     # AI 生成的用了几条（未复核，要分开数）
    #   ★ 为什么要分开数？
    #     `col_comments` 现在混装两种来源：人写的（manual）和 AI 生成的（ai）。
    #     它们都进 RAG、都覆盖库自带注释，但**可信度完全不同** ——
    #     全算成"人工注释"会让运维以为这批知识都经过人把关，
    #     一旦 AI 编错，没人会想到去复核。日志必须如实区分。
    col_sources = config_store.get_column_comment_sources()

    # ★ 字段级关键词：只收人工注释（AI 注释未复核，怕引入噪声）
    #   按表分组，后面拼进该表的 keywords。
    field_kw = {}
    for key, c in col_comments.items():
        if col_sources.get(key) == "ai":
            continue
        tbl = key.rsplit(".", 1)[0] if "." in key else ""
        if tbl:
            field_kw.setdefault(tbl, []).append(c)

    for t, col, dtype in schema_rows:               # 遍历抓回来的每一行
        col_names.setdefault(t, []).append(col)     # ★ 结构化列名清单（SQL AST 列级校验用）
        line = f"  - {col} ({dtype})"               # 字段名 + 类型
        # ★ 字段注释优先级：人工 / AI > 数据库自动发现
        key = f"{t}.{col}"
        c = col_comments.get(key) or column_c.get(key, "")
        if col_comments.get(key):
            if col_sources.get(key) == "ai":
                ai_used += 1
            else:
                manual_used += 1
        if c:
            line += f"   {c}"                       # ★ 拼上字段注释
            #   【为什么字段注释这么关键？】
            #     我们踩过的坑：模型不知道 status 各枚举值的含义，
            #     把"完单量"写成了 status=7（已取消）。
            #     直到在字段上写明"6已完成 7已取消"才修好。
            #     字段注释是 text-to-SQL 准确率最直接的杠杆。
        grouped.setdefault(t, []).append(line)
    #   setdefault：如果 t 这个键不存在，先建一个空列表，再往里 append
    #   【SQL 类比】≈ GROUP BY 表名，把字段聚成一组

    if manual_used or ai_used:
        parts = []
        if manual_used:
            parts.append(f"{manual_used} 条人工")
        if ai_used:
            parts.append(f"{ai_used} 条 AI 生成（未复核）")
        print(f"  ✅ 本次注入了 {' + '.join(parts)} 字段注释（优先级高于数据库注释）")
        if ai_used:
            #   别让这条日志变成"看起来都搞定了"的假安慰 —— 明确提示要复核
            print(f"     ⚠️ 其中 {ai_used} 条是 AI 生成的，可信但没核实过，"
                  f"建议在「知识缺口」里复核后转为人工版")

    tables = []                                     # 最终结果的空列表
    for t, cols in grouped.items():                 # 遍历每张表和它的字段
        comment = comments.get(t)                   # ① 先试人工维护的注释
        if not comment:                             # ② 没有就试数据库自带的
            comment = table_c.get(t)
        if not comment:                             # ③ 都没有，用表名兜底
            comment = t
        tables.append({                             # 组装成一个表字典
            "name": t,                              # 表名
            # ★ 关键词 = 表名 + 人工字段注释短语
            #   关键词命中优先于向量，解决“用户只报字段名”时表召回不到的问题
            "keywords": [t] + _field_keywords(field_kw.get(t, [])),
            "doc": f"{t}：{comment}",                # embedding 用的富文本
            "columns": "\n".join(cols),             # 字段定义：命中后注入给模型
            "column_list": col_names.get(t, []),    # ★ 结构化列名清单（SQL AST 列级校验）
        })
    return tables


# ═══════════════════════════════════════════════════════════
# 第 1.5 块：★ 组装"数据字典"知识库（编码 → 中文含义）
# ═══════════════════════════════════════════════════════════
#  【为什么字典要单独一类，而不是塞进字段注释？】
#    字段注释回答"这个字段是什么"（一句话）；
#    字典回答"这个字段的每个取值是什么"（一个列表）。
#    渠道字典有 28 条取值，塞进字段注释会让注释又长又难维护。
#    而且字典是跨表复用的（同一套渠道编码，订单表、客户表都有），
#    绑定在某一张表的字段注释上会重复。
#
#  【为什么必须带 field_hint？】
#    模型知道"101=车拖车"还不够，它得知道"这个编码用在 app_entity_source 字段上"。
#    否则它可能把渠道编码用到别的字段上（比如 vehicle），结果静默算错。
MAX_DICT_ITEMS_IN_PROMPT = 100      # 注入 prompt 时最多列多少条取值（超长的字典截断并注明）


def _build_dicts(entries):
    """字典条目 → RAG 需要的知识库列表"""
    out = []
    for e in entries:
        items = e["items"]                          # [{"value":..,"label":..}, ...]
        if not items:                               # 空字典没有注入价值
            continue
        shown = items[:MAX_DICT_ITEMS_IN_PROMPT]
        pairs = " | ".join(f"{i['value']}={i['label']}" for i in shown)
        if len(items) > len(shown):
            pairs += f" | …（共 {len(items)} 项，此处省略 {len(items) - len(shown)} 项）"

        field = e.get("field_hint") or ""
        head = f"- {e['name']}" + (f"（用于字段 {field}）" if field else "")
        # ── ★ 关键词 = 字典名 + 人工配的词 + 【全部取值的中文含义】 ──
        #   【为什么取值含义也必须进关键词？】
        #     用户问"车拖车的完单量"，"车拖车"是【应用主体】字典里的一个 label。
        #     不把它当关键词，字典就召不回 → 模型只能瞎猜
        #     （实测会编出 company_id = 'ctc'，数字全错还不报错）。
        #   【为什么不能只靠向量兜底？】
        #     字典的 doc 是"词表"不是自然语言文章，而 BGE 的检索指令是为
        #     "短问题 → 长文章"设计的 —— 加在字典查询上反而把相似度压低
        #     0.07~0.12（"车拖车"那题 0.5689 → 0.4911，恰好掉出 0.55 阈值）。
        #     详见 _hybrid_retrieve 的 query_prefix 说明。
        #     关键词是子串精确匹配，确定性高，不靠"分数刚好过线"。
        labels = [str(i["label"]) for i in items]
        #   ★ 过滤太短和纯数字的 label：
        #     长度 < 2 或纯数字的标签（如 "0"、"1"）出现在正常问句里是巧合，
        #     会让字典被无端召回。本库 3 个字典实测最短是"安卓""微信"，
        #     但换成别的字典就难说，这道保护必须有。
        label_kw = [l for l in labels if len(l) >= 2 and not l.isdigit()]

        out.append({
            "name": e["name"],
            #   字典名本身也算关键词
            "keywords": (e["keywords"] or []) + [e["name"]] + label_kw,
            # ★ embedding 用"名称 + 所有中文标签"：用户换个说法问（语义相近）时靠这条兜底
            "doc": f"{e['name']}：{' '.join(labels)}",
            "text": f"{head}\n  {pairs}",
            "count": len(items),
            # ★ 下面两个字段不参与检索，是给【结果展示】用的：
            #   field_hint → 结果表的这一列该显示什么中文名
            #   items      → 把编码值翻译成中文（app_entity_source=101 → 车拖车）
            #   放在索引里，免得展示时再查一次配置库。
            "field_hint": e.get("field_hint") or "",
            "items": items,
        })
    return out


# ═══════════════════════════════════════════════════════════
# 第 2 块：★ 加载 embedding 模型（离线优先）
# ═══════════════════════════════════════════════════════════
def _load_embed_model():
    """
    加载中文 embedding 模型。策略：**离线优先，联网兜底**

    ★ 为什么要这么设计？
      企业内网环境连不上 huggingface.co，而 fastembed 默认每次启动都会
      去 HuggingFace 校验模型版本 —— 一旦网络不通，程序直接起不来。
      这个坑我们在接入时真实踩过。

    策略：
      ① 先尝试【只用本地缓存】加载（快、不依赖网络、生产环境首选）
      ② 本地没有模型时，给出清晰提示并尝试联网下载（首次准备模型用）
      ③ 下载也失败时，报出"该怎么办"而不是抛一堆看不懂的堆栈
    """
    # ★ 延迟导入：只有真的要加载模型时才 import fastembed
    #   如果没装这个包，给一句能看懂、能照做的提示，
    #   而不是让整个 import 链在文件顶部就崩掉（那样连"用不到模型的功能"也跑不了）
    try:
        from fastembed import TextEmbedding
    except ImportError as e:
        raise RuntimeError(
            "\n" + "=" * 66 + "\n"
            "❌ 没有安装 fastembed（生成向量用的库），无法建立检索索引\n"
            "\n"
            "解决办法：\n"
            "    .venv/bin/python -m pip install fastembed\n"
            "\n"
            "如果确定已经装过，那多半是【用错了 Python 环境】：\n"
            "    which python        ← 看看路径里有没有 ai-data-agent/.venv\n"
            "    正确运行方式：.venv/bin/python 你的脚本.py\n"
            + "=" * 66 + "\n"
        ) from e

    cache_dir = config.EMBED_CACHE_DIR              # 模型缓存目录（默认项目内的 models/）

    if config.EMBED_LOCAL_ONLY:                     # 配置成"只用本地缓存"
        try:
            return TextEmbedding(
                config.EMBED_MODEL,
                cache_dir=cache_dir,                # 指定缓存目录（不放系统临时目录）
                local_files_only=True,              # ★ 关键：完全不走网络
            )
        except Exception:
            # 本地没有 → 不直接崩，往下走"联网下载"分支
            print(f"  ⚠️ 本地缓存目录 {cache_dir}/ 里没有模型，尝试联网下载...")

    # ── 联网下载（首次准备模型时走这里）──
    try:
        model = TextEmbedding(config.EMBED_MODEL, cache_dir=cache_dir)
        print(f"  ✅ 模型已下载到 {cache_dir}/，以后可离线启动")
        return model
    except Exception as e:
        # ── 兜底：给出"人能看懂、能照做"的提示 ──
        raise RuntimeError(
            f"\n{'=' * 66}\n"
            f"❌ 无法加载 embedding 模型：{config.EMBED_MODEL}\n"
            f"\n"
            f"这是内网环境最常见的问题，两种解决办法：\n"
            f"\n"
            f"【办法一】在能联网的机器上先下好模型，再把 models/ 目录拷过来\n"
            f"    HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\\n"
            f"      python -c \"from fastembed import TextEmbedding;"
            f" TextEmbedding('{config.EMBED_MODEL}', cache_dir='{cache_dir}')\"\n"
            f"\n"
            f"【办法二】如果能通国内镜像，直接加这两个环境变量重跑：\n"
            f"    export HF_ENDPOINT=https://hf-mirror.com\n"
            f"    export HF_HUB_DISABLE_XET=1\n"
            f"    （xet 是 HuggingFace 的新传输协议，国内经常被拦，所以禁用它）\n"
            f"\n"
            f"原始错误：{type(e).__name__}: {str(e)[:200]}\n"
            f"{'=' * 66}\n"
        ) from e


# ═══════════════════════════════════════════════════════════
# 第 3 块：★ 建索引 —— 整个 RAG 的准备工作
# ═══════════════════════════════════════════════════════════
def _build_index():
    """
    建索引：从数据库抓表结构 → 组装知识库 → 算向量

    ★ 为什么不在 import 时自动执行？
    ★ 因为 import 时干重活有两个大问题：
        ① 慢 —— 加载模型要好几秒，别人一 import 就被拖住
        ② 依赖顺序不可控 —— 如果数据库还没建好，import 就崩了
    ★ 所以改成"显式调用"：谁需要，谁在合适的时候调一次
    ★ 这是所有大型项目的通用做法：【不要在 import 时做副作用操作】

    返回：一个字典，装着"模型 + 知识库 + 向量"，后面检索时都要用到
    """
    print("正在加载 embedding 模型...")
    model = _load_embed_model()                     # ★ 加载中文 embedding 模型

    all_rows = db.fetch_schema()                    # 从数据库抓【全部】表结构
    visible_rows = _filter_visible(all_rows)        # ★ 第一道过滤：按权限剔除不可见的表
    tables = _build_tables(visible_rows)            # 组装成 RAG 知识库（此时已不含敏感表）
    # ★ 治理准入（status）：approved/无 status 可见；candidate 仅非严格模式可见；
    #   draft/reviewing/deprecated/rejected/archived 永不进生产检索。
    metrics = knowledge.get_metrics()               # ★ 只取【已开放】的指标
    _vis_m = config_store.get_visible_knowledge_ids("metrics", "name")
    if _vis_m is not None:
        metrics = [m for m in metrics if m["name"] in _vis_m]
    disabled_metrics = knowledge.get_disabled_metrics()
    #                   ★ 停用的指标不进向量索引（AI 检索不到口径），
    #                     但要单独存下来 —— agent 要用它做"问题级拦截"。
    #                     否则模型可能用字段硬算出一个错数（静默算错，最危险）。

    _raw_dicts = knowledge.get_dict_entries()
    _vis_d = config_store.get_visible_knowledge_ids("dict_entries", "name")
    if _vis_d is not None:
        _raw_dicts = [d for d in _raw_dicts if d["name"] in _vis_d]
    dicts = _build_dicts(_raw_dicts)                # ★ 数据字典（只取已开放 + 治理准入）

    # 给表的描述文本做向量
    table_vectors = list(model.embed([t["doc"] for t in tables]))
    #                        列表推导式：把每张表的 doc 取出来，批量 embed
    #                        list(...) = 把生成器全部取出变成列表

    # 给指标的描述文本做向量
    metric_vectors = list(model.embed([m["doc"] for m in metrics]))

    # 给字典的描述文本做向量（★ 注意：空列表时 fastembed 会报错，所以要判断）
    dict_vectors = list(model.embed([d["doc"] for d in dicts])) if dicts else []

    print(f"索引完成：{len(tables)} 张表 + {len(metrics)} 个指标 + {len(dicts)} 个字典，"
          f"维度 {len(table_vectors[0]) if table_vectors else 0}")

    # ★ 索引重建状态落库（GAP-3）：记录成功 + 知识计数 + 时间戳，
    #   供「上次重建是否可信 / 是否过期」检测，避免重建失败被静默忽略。
    #   落库失败不影响本次索引使用（只打日志）。
    try:
        config_store.set_index_state("ok", {
            "tables": len(tables), "metrics": len(metrics),
            "dicts": len(dicts), "disabled_metrics": len(disabled_metrics),
        })
    except Exception as e:
        print(f"⚠️ 索引状态落库失败（不影响本次索引使用）：{e}")

    return {                                        # 把所有东西打包成一个字典返回
        "model": model,
        "tables": tables,
        "metrics": metrics,
        "dicts": dicts,
        "table_vectors": table_vectors,
        "metric_vectors": metric_vectors,
        "dict_vectors": dict_vectors,
        # ★ 已停用的指标 —— 供 agent 做"问题级拦截"（见 match_disabled_metric）
        "disabled_metrics": disabled_metrics,
        # ★ 允许访问的表名清单 —— 供 guard 做"执行前的硬校验"（第四道防线）
        "allowed_tables": [t["name"] for t in tables],
        #                  列表推导式：把知识库里每张表的 name 取出来
    }


def build_index():
    """
    对外索引构建入口（带失败状态追踪）。

    ★ 包裹 _build_index：重建成功写 ok 状态（在 _build_index 内），
      重建失败写 failed 状态 + 错误摘要后再抛出，让运维能从 index_state
      判断「当前服务是否在用可信索引」，而不是把失败静默吞掉。
    """
    try:
        return _build_index()
    except Exception as e:
        try:
            config_store.set_index_state("failed", error=str(e)[:500])
        except Exception:
            pass
        raise


# ═══════════════════════════════════════════════════════════
# 第 3.5 块：★ 指标级权限 —— 判断问题是否在问"未开放的指标"
# ═══════════════════════════════════════════════════════════
def match_disabled_metric(index, question):
    """
    检查这个问题是不是在问一个【未开放】的指标

    返回：命中的未开放指标名；没命中返回 None

    ★ 为什么"停用"之外还需要这个函数？
      停用 = 指标不进知识库 → AI 检索不到它的口径
      但！如果问题里提到了某张已经开放的表，模型仍可能用字段硬算：
          问"客单价"（已停用）→ 检索不到口径 → 但它看到 orders 表有 amount
          → 它自己拼一个 SUM(amount)/COUNT(*) 出来 → 数字是错的，而且不报错
      这叫【静默算错】，比报错危险一万倍。
        ↓
      所以必须在入口处拦一道：问题问到了停用指标 → 直接拒绝，不给模型发挥的机会。

    ★ 判定规则：用"最长关键词优先"（和 _hybrid_retrieve 里同一套逻辑）
      为什么不能简单粗暴地"命中就拦"？
        假设"完单量"被停用，关键词里有 "完单"（长度2）
        而"完单率"是开放的，关键词是 "完单率"（长度3）
        问题："查询完单率" → "完单" 也命中了 → 简单写法会把合法的"完单率"误拦
        ↓
      让【关键词最长的那个】说了算：完单率(3) > 完单(2) → 判定为在问完单率 → 放行 ✅
    """
    question_lower = question.lower()

    best_len = 0                                    # 目前命中的最长关键词长度
    best_name = None                                # 它属于哪个指标
    best_enabled = True                             # 那个指标是开放还是停用

    # 把"开放的"和"停用的"放在一起比 —— 谁更具体，谁说了算
    candidates = ([(m, True) for m in index["metrics"]]
                  + [(m, False) for m in index["disabled_metrics"]])

    for m, is_enabled in candidates:                # 逐个指标看
        keywords = m["keywords"] + [m["name"]]
        #          ★ 把指标名本身也当关键词 —— 防止配置时漏写关键词导致拦截失效
        for kw in keywords:
            if kw.lower() not in question_lower:
                continue
            kl = len(kw)
            # 更长 → 换冠军；★ 等长时【停用优先】：
            #   若当前冠军是开放指标、而这条是停用指标且关键词等长，则改判为停用（宁拦勿漏）。
            #   不这样做会漏拦：问「完单量」时，停用的「完单量」与开放的「代驾完单量」
            #   都含等长关键词「完单量」，而开放的排在前面 → 停用指标被静默放行。
            if kl > best_len or (kl == best_len and best_name is not None
                                 and not is_enabled and best_enabled):
                best_len, best_name, best_enabled = kl, m["name"], is_enabled

    if best_name and not best_enabled:              # 冠军是个停用指标
        return best_name
    return None                                     # 没问题（或者冠军是开放的）


# ═══════════════════════════════════════════════════════════
# 第 4 块：余弦相似度（第 12 步手写的那个，一路用到现在）
# ═══════════════════════════════════════════════════════════
def cosine_similarity(a, b):
    """算两个向量的方向相似度，0~1，越大越像"""
    dot = sum(x * y for x, y in zip(a, b))     # 点积：对应位置相乘再求和
    norm_a = math.sqrt(sum(x * x for x in a))  # a 的长度（勾股定理）
    norm_b = math.sqrt(sum(x * x for x in b))  # b 的长度
    return dot / (norm_a * norm_b)             # 只比方向，不比长短


# ═══════════════════════════════════════════════════════════
# 第 5 块：★ 通用混合检索（表和指标共用同一套逻辑）
# ═══════════════════════════════════════════════════════════
def _hybrid_retrieve(question, items, vectors, model, threshold, top_k,
                     query_prefix=None):
    """
    混合检索：关键词优先（精确），向量补足（语义）

    参数：
        question     = 用户的问题
        items        = 候选列表（可以是表列表，也可以是指标列表）
        vectors      = 它们对应的向量
        threshold    = 相似度阈值（低于它就不要）
        top_k        = 最多返回几个
        query_prefix = 查询侧要加的前缀（BGE 的非对称检索指令）
                       ★ 默认 None = 用 config.QUERY_INSTRUCTION
                       ★ 传 "" = 不加前缀（字典专用，原因见下）

    返回：(命中的名字列表, 全部向量分数, 命中方式说明)

    ★ 为什么要有 query_prefix 这个参数？
      QUERY_INSTRUCTION（"为这个句子生成表示以用于检索相关文章："）是 BGE 为
      **非对称检索**设计的：短查询 → 长文章，把查询向量往"文章"那一侧靠。
      表/指标的 doc 是自然语言段落，加它是对的。
      但【字典的 doc 是"词表"】："应用主体：车拖车 慧拉车 联盟拍 …" —— 它不是文章。
      给它加"检索相关文章"的指令就是南辕北辙，实测相似度被压低 0.07~0.12：
          应用主体 0.5689 → 0.4911   ← 恰好掉出 0.55 阈值，字典召不回
          来源渠道 0.4105 → 0.2892
          应用市场 0.2753 → 0.2071
      所以字典检索传 query_prefix=""，不加前缀。
    """
    # ── 第一步：算出问题的向量，和每个候选算相似度 ──
    if query_prefix is None:                        # 没指定就按默认（表/指标用）
        query_prefix = config.QUERY_INSTRUCTION
    qv = list(model.embed([query_prefix + question]))[0]            # 问题 → 向量

    scored = []                                     # 装 (名字, 分数) 的空列表
    for item, vec in zip(items, vectors):           # 候选和向量一一配对
        s = cosine_similarity(qv, vec)              # 算相似度
        scored.append((item["name"], s))            # 记下来
    scored.sort(key=lambda x: -x[1])                # 按分数从高到低排序
    #              lambda x: -x[1] = 取每个元素的第 1 项（分数）并取负数 → 大的排前面

    # ── 第二步：关键词精确命中，优先级最高 ──
    question_lower = question.lower()               # ★ 把问题统一转小写
    hit_infos = []                                  # 收集 (名字, 命中的最长关键词长度)

    for item in items:                              # 遍历每个候选
        best_len = 0                                # 这个候选命中的最长关键词长度，初始为 0
        for kw in item["keywords"]:                 # 遍历它的关键词
            if kw.lower() in question_lower:        # ★ 两边都转小写再比 —— 忽略大小写
                #   ★ 为什么必须转小写？
                #   用户可能写 "GMV" 也可能写 "gmv"，
                #   Python 的 in 区分大小写，不转就会漏命中。
                #   （这个 bug 是评测集 #8 抓出来的）
                best_len = max(best_len, len(kw))   # 取最长的那个（最长 = 最精确）
                #           max(a, b) = 取两者中较大的
        if best_len > 0:                            # 这个候选至少命中了一个关键词
            hit_infos.append((item["name"], best_len))

    # ★★★ 关键一步：按"命中的关键词长度"从长到短排序 ★★★
    hit_infos.sort(key=lambda x: -x[1])
    #   为什么？因为长词更精确。
    #   例：问题"查询完单量"里
    #       订单量 命中 "单量"   （长度 2）
    #       完单量 命中 "完单量" （长度 3）
    #   不排序的话，列表顺序决定胜负，订单量会抢走位置；
    #   排序后完单量排第一，才是对的。
    #   （这个"子串陷阱"是评测集 #7 抓出来的）
    hits = [name for name, _ in hit_infos]          # 把名字取出来组成列表
    #       列表推导式：遍历 hit_infos，每项取第 0 个元素（名字）

    #   【SQL 类比】kw in question ≈ question LIKE '%关键词%'
    #             （SQL 的 LIKE 默认也不区分大小写，Python 的 in 区分 —— 所以要手动转换）

    if hits:                                        # 关键词有命中 → 直接用，不再看向量
        return hits[:top_k], scored, "关键词精确命中（长词优先）"
        #            [:top_k] = 列表切片，只取前 top_k 个

    # ── 第三步：关键词没中，用向量补 ──
    result = []                                     # 结果列表
    for name, s in scored:                          # 按分数从高到低看
        if s >= threshold:                          # 分数达标才要
            result.append(name)
        if len(result) >= top_k:                    # 够了就停
            break
    if result:
        return result, scored, f"向量召回（{result[0]} {scored[0][1]:.4f}）"

    # ── 第四步：两条路都没中 ──
    return [], scored, "未匹配"


# ═══════════════════════════════════════════════════════════
# 第 5.5 块：★ 用户「点名表」识别
#   —— 向量分数分不开"该留的"和"噪声"时，靠用户自己写的词来定
# ═══════════════════════════════════════════════════════════
def table_self_name(doc):
    """
    从表的索引文本（形如"表名：注释"）里取出它的【业务自述名】。

    例： "ods_tu_record_all：资金流水记录明细，涵盖订单、保证金、…"
          → "资金流水记录明细"

    ★ 只取"注释里第一个标点之前"那一小段，不整条注释都用：
      整条注释含大量泛化短语（"的订单量""金额""统计"…），会误伤别的表。
      自述名是"这张表叫什么"的短语，区分度高得多。
    ★ 换行【不算】分隔符：「ods_tu_record_type_all」的注释首行是
      "code对应 ods_tu_record_all.type"，若按换行截断就取不到后半句的"资金流水"了。
    """
    c = (doc or "")
    if "：" in c:                       # 去掉开头的"表名："
        c = c.split("：", 1)[1]
    c = c.strip()
    cut = [c.find(s) for s in "，。；;、：:！？!?（）()【】[]" if c.find(s) != -1]
    if cut:
        c = c[:min(cut)]
    return c.strip()


def _lcs_len(a, b):
    """最长【公共子串】长度与子串本身（连续重合，比公共子序列更严格）。"""
    if not a or not b:
        return 0, ""
    prev = [0] * (len(b) + 1)
    best = end = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best, end = cur[j], i
        prev = cur
    return best, a[end - best:end]


def field_keyword_tables(question, tables, min_chars=4):
    """返回「通过字段关键词命中」的表：{表名: 命中的关键词长度}。

    ★ 和 named_tables 的区别：
      named_tables 只看“表自述名”（表注释第一段），用于识别用户点名表；
      这里看的是【人工字段注释】加进 keywords 的短语，用于识别“用户在报字段名”。
    ★ 为什么要单独返回？
      这类命中比向量分数确定得多，应视为“用户点名”，豁免实体过滤；
      否则会被 P2 的错误实体当成跨业务线表删掉（司机表就踩过这个坑）。
    """
    q = (question or "").lower()
    hits = {}
    for t in tables:
        name = t.get("name")
        best = 0
        for kw in (t.get("keywords") or []):
            if not kw or kw == name:
                continue
            if len(kw) >= min_chars and kw.lower() in q:
                best = max(best, len(kw))
        if best:
            hits[name] = best
    return hits


def named_tables(question, tables, min_chars=None):
    """
    用户在问题里【明确点名】了哪些表 → 返回 (表名列表, {表名: 重合词})。

    【为什么需要它】
      实体过滤器（semantic_layer.P3）会摘掉"归属别的业务线"和"相似度不够高"的表。
      但向量分数在有些场景【分不开该留的和噪声】——
      实测同一句「查询8月份资金流水表中…」：
          该留的  ods_tu_record_all(资金流水表)      0.5686
          纯噪声  ods_tu_large_handcart_order_statistics_all 0.5449
      只差 0.024，任何阈值都切不开。而"用户亲手写出的词"是比分数强得多的证据：
      「资金流水」与「ods_tu_record_all：资金流水记录明细」有 4 字连续重合。

    【判定规则】
      问题与【表自述名】的连续重合 ≥ config.NAMED_TABLE_MIN_CHARS（默认 4）字。
    """
    min_chars = config.NAMED_TABLE_MIN_CHARS if min_chars is None else min_chars
    hits, why = [], {}
    for t in (tables or []):
        n, sub = _lcs_len(question, table_self_name(t.get("doc")))
        if n >= min_chars:
            hits.append(t["name"])
            why[t["name"]] = sub
    return hits, why


# ═══════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════
# 第 6.5 块：★ 数据字典召回（第三条检索线）
# ═══════════════════════════════════════════════════════════
def retrieve_dicts(index, question):
    """
    按需召回"数据字典"（编码 → 中文含义）

    返回：(字典名列表, 命中方式说明)

    ★ 为什么单独一个函数，不并进 retrieve()？
      retrieve() 的返回值已经有 4 个元素，改签名会波及所有调用方（agent / 评测）。
      字典属于"附加知识"：召回就注入，召不回也不影响主流程。
      单独一条线，职责清晰，也不破坏已有接口。

    ★ 面试/评审常问：字典为什么不干脆全量注入？
      因为全量注入 = 每一问都背上所有字典的编码表（近千条），
      既贵又会稀释注意力（context rot）。按需召回才是对的。
    """
    dicts = index.get("dicts") or []
    if not dicts:                                   # 还没配任何字典
        return [], "未配置字典"

    names, _scores, reason = _hybrid_retrieve(
        question, dicts, index["dict_vectors"],
        index["model"], config.DICT_THRESHOLD, config.DICT_TOP_K,
        query_prefix="",        # ★ 字典 doc 是"词表"不是文章 → 不加 BGE 检索指令
        #                           （加了反而降相似度，实测 0.5689→0.4911 掉出阈值）
    )
    return names, reason
