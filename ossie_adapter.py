# -*- coding: utf-8 -*-
"""
ossie_adapter.py —— Apache Ossie 标准适配层（唯一入口，禁止散落）。

★ 定位：知数AI 内部语义模型 ⇄ Apache Ossie 标准模型的【双向转换 + 校验】。
  架构原则（任务书 §5.2）：不直接用 OSSIE 替换内部 Semantic Layer，
  而是「内部语义层 + OSSIE Adapter」两层：
      内部知识资产 → 内部 Semantic Layer → 本 Adapter → OSSIE YAML/JSON → 外部平台。
  Agent 仍优先用内部统一语义层；OSSIE 只负责标准化导入导出与外部互通。

★ 为什么必须独立成模块、不散落到 Agent/RAG/页面？
  ① 解析/校验/转换是纯函数，散落会重复实现、口径不一；
  ② 往返一致性测试需要一个确定的「内部模型 ⇄ OSSIE」函数对；
  ③ 未来升级 OSSIE 规范版本，只需改这一个文件（schema 在 ossie_schema/ 目录）。

★ 轻依赖原则：本模块【不引入 jsonschema】。项目未装 jsonschema，
  故内置一个精简的 JSON Schema 校验器（只支持本项目 schema 用到的构造），
  直接消费内置的 ossie-schema.json（schema 是唯一事实源，不硬编码字段）。
  YAML 解析用 PyYAML（环境中已存在，由 fastembed 等间接依赖带入）。

★ 对象映射（详见 docs/knowledge-center-ossie/01-review-report.md §8）：
    Semantic Model    ← 业务主题（一个主题 = 一个模型）
    Dataset           ← 业务实体（entity.name + 主表 + 字段）
    Field             ← 字段注释（column_comments）
    Metric            ← 指标（metrics：sql_template→expression.sql，definition→description）
    Dimension         ← Field 子对象 is_time（本次仅映射时间维度）
    Relationship      ← 关联规则（relationships）
    AI Context        ← keywords / doc / 字段注释（同义词）
    nonportable_extensions ← 知数AI 内部扩展属性（P0/P1/P2 分级、审核状态、粒度、Join 基数等）
"""
import json
import os
import re

try:
    import yaml as _yaml          # PyYAML：解析 OSSIE YAML 文档
    _HAS_YAML = True
except Exception:                 # 环境缺 PyYAML 时降级为只支持 JSON
    _yaml = None
    _HAS_YAML = False


# ═══════════════════════════════════════════════════════════
# ① 版本与 schema 常量
# ═══════════════════════════════════════════════════════════
# ★ 锁定规范版本（与 ossie_schema/VERSION.md 保持一致）
OSSIE_VERSION = "0.3.0.dev0"
SCHEMA_COMMIT = "e6ad94ee87a53fd8f8b6dbf0de33fab59e406c0c"

# ★ 知数AI 扩展属性的 vendor 名（导出的 nonportable_extensions 用它标记）
ZHISHU_VENDOR = "zhishu-ai"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.path.join(BASE_DIR, "ossie_schema")
SCHEMA_PATH = os.path.join(SCHEMA_DIR, "ossie-schema.json")

# ★ 知数AI 内部扩展属性清单（往返时必须保留的字段 → 塞进 nonportable_extensions.data）
#   这些属性 OSSIE 标准无法表达，但知数AI 治理依赖它们，导出时不能静默丢弃。
#   映射：data 里的键 → 内部对象字段名
_EXTENSION_FIELDS = (
    "knowledge_level",      # P0/P1/P2 知识分级
    "status",               # 审核状态（approved/candidate/rejected/...）
    "source",               # 知识来源（manual/ai）
    "enabled",              # 开放状态
    "version",              # 版本号
    "stable_key",           # 稳定业务键
    "grain",                # 数据粒度（实体）
    "cardinality",          # Join 基数（关系）
    "join_sql",             # 完整 Join SQL（关系，from_columns/to_columns 提取不完美时用于回填）
    "pre_agg_required",     # 1:N 需预聚合（关系）
    "metric_type",          # 指标度量类型（count/amount/ratio）
    "tables",               # 指标依赖表（JSON 数组）
    "conflict_key",         # 指标冲突键
    "required_predicates",  # 指标硬约束
    "required_columns",
    "forbidden_predicates",
    "depends_metrics",
)


def _load_schema():
    """从内置目录加载 JSON Schema（只读，不联网）。"""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# 模块加载时缓存 schema（只读一次，避免每次校验重复读盘）
_SCHEMA = _load_schema()
_SCHEMA_DEFS = _SCHEMA.get("$defs", {})


# ═══════════════════════════════════════════════════════════
# ② 精简 JSON Schema 校验器（支持 draft 2020-12 子集）
#   覆盖 ossie-schema.json 用到的全部构造：type/const/enum/required/
#   properties/additionalProperties/items/anyOf/oneOf/$ref/$defs/minItems/minLength
# ═══════════════════════════════════════════════════════════
def _type_of(v):
    """把 Python 值映射回 JSON 类型名（integer/number 区分）。"""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _resolve_pointer(ref):
    """解析 JSON Pointer（形如 "#/$defs/Field" 或 "#/$defs/SemanticModel/properties/datasets"）。

    返回指向的 schema 子节点；解析失败返回 None。
    ★ 支持任意多层路径 + ~0/~1 转义（JSON Pointer 规范）。
    """
    if not ref or not ref.startswith("#/"):
        return None
    parts = [p.replace("~1", "/").replace("~0", "~") for p in ref[2:].split("/")]
    node = _SCHEMA
    for p in parts:
        if isinstance(node, dict) and p in node:
            node = node[p]
        else:
            return None
    return node


def _validate_node(value, schema, path, errors):
    """递归校验单个节点（就地追加错误到 errors，每个错误带 JSON Pointer 路径）。"""
    if schema is None:
        return
    # $ref 展开（支持多层 JSON Pointer，如 #/$defs/SemanticModel/properties/datasets）
    if "$ref" in schema:
        target = _resolve_pointer(schema["$ref"])
        if target is not None:
            _validate_node(value, target, path, errors)
        # 解析不到目标（外联引用等）则不校验；本 schema 均为文档内局部引用
        return

    t = _type_of(value)

    # 类型检查（integer 兼容 number）
    if "type" in schema:
        want = schema["type"]
        ok = (t == want) or (want == "number" and t == "integer")
        if not ok:
            errors.append(f"{path}: 期望类型 {want}，实际 {t}")

    # 常量
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: 期望常量 {schema['const']!r}，实际 {value!r}")

    # 枚举
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} 不在枚举 {schema['enum']} 中")

    # 字符串最小长度
    if "minLength" in schema and t == "string" and len(value) < schema["minLength"]:
        errors.append(f"{path}: 字符串长度 {len(value)} 小于 {schema['minLength']}")

    # 数组：最小项数 + 逐项校验
    if "minItems" in schema and t == "array" and len(value) < schema["minItems"]:
        errors.append(f"{path}: 数组项数 {len(value)} 小于 {schema['minItems']}")
    if "items" in schema and t == "array":
        for i, item in enumerate(value):
            _validate_node(item, schema["items"], f"{path}/{i}", errors)

    # 对象：required + properties + additionalProperties
    if t == "object":
        if "required" in schema:
            for req in schema["required"]:
                if req not in value:
                    errors.append(f"{path}: 缺少必填字段 {req!r}")
        if "properties" in schema:
            for pname, pschema in schema["properties"].items():
                if pname in value:
                    _validate_node(value[pname], pschema, f"{path}/{pname}", errors)
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}).keys())
            for k in value:
                if k not in allowed:
                    errors.append(f"{path}: 出现未定义字段 {k!r}")

    # anyOf：满足任一
    if "anyOf" in schema:
        if not any(_ok_of(value, alt) for alt in schema["anyOf"]):
            errors.append(f"{path}: 不满足 anyOf 中任一分支")
    # oneOf：恰好满足一个
    if "oneOf" in schema:
        if sum(1 for alt in schema["oneOf"] if _ok_of(value, alt)) != 1:
            errors.append(f"{path}: 不满足 oneOf 恰好一个分支")


def _ok_of(value, schema):
    """判断节点是否通过某个子 schema（不收集错误，只返回布尔）。"""
    errs = []
    _validate_node(value, schema, "#", errs)
    return not errs


def validate_document(doc):
    """按内置 ossie-schema.json 校验一个语义模型文档。

    返回 (ok, errors)：ok 为 True 表示通过；errors 为带路径的错误列表。
    """
    if not isinstance(doc, dict):
        return False, ["根节点必须是对象"]
    errors = []
    _validate_node(doc, _SCHEMA, "#", errors)
    return (len(errors) == 0), errors


# ═══════════════════════════════════════════════════════════
# ③ 格式解析与版本识别
# ═══════════════════════════════════════════════════════════
def detect_format(text):
    """根据内容判断文档格式：返回 'yaml' / 'json' / None（无法判断）。"""
    s = (text or "").lstrip()
    if not s:
        return None
    if s[0] in "{[":
        return "json"
    return "yaml"


def parse_document(text):
    """解析 YAML 或 JSON 文本为 dict（自动识别格式）。

    返回 (doc, err)：成功 err 为 None；失败 doc 为 None。
    ★ YAML 陷阱：nonportable_extensions.data 里的标量可能被 YAML 加载器误转
      （yes→bool、010→int、2024-01-01→date）。这里用 safe_load，类型转换在
      后续 validate_document 的 additionalProperties/类型检查里兜底拦截。
    """
    fmt = detect_format(text)
    if fmt == "json":
        try:
            return json.loads(text), None
        except Exception as e:
            return None, f"JSON 解析失败：{e}"
    if fmt == "yaml":
        if not _HAS_YAML:
            return None, "当前环境缺少 PyYAML，无法解析 YAML（请安装 pyyaml）"
        try:
            doc = _yaml.safe_load(text)
            return doc, None
        except Exception as e:
            return None, f"YAML 解析失败：{e}"
    return None, "无法识别文档格式（仅支持 YAML / JSON）"


def detect_version(doc):
    """读取文档的规范版本号（无则返回空字符串）。"""
    if isinstance(doc, dict):
        return doc.get("version") or ""
    return ""


def is_supported_version(doc):
    """判断文档版本是否为当前支持的版本。"""
    return detect_version(doc) == OSSIE_VERSION


# ═══════════════════════════════════════════════════════════
# ④ 内部语义模型 → OSSIE 标准模型（导出方向）
# ═══════════════════════════════════════════════════════════
def _extensions(data):
    """把知数AI 内部扩展属性打包成 nonportable_extensions 数组。

    ★ data 必须是 JSON 可表示对象；空 dict 也保留（表示"有扩展但为空"）。
    """
    clean = {k: v for k, v in (data or {}).items() if k in _EXTENSION_FIELDS and v is not None}
    if not clean:
        return []
    return [{"vendor_name": ZHISHU_VENDOR, "data": clean}]


def _field_from_column(col):
    """字段（内部 {name,type,comment,source}）→ OSSIE Field。

    ★ 本次仅映射时间维度：字段类型含 Date/DateTime 时标 is_time=true。
    """
    name = col.get("name") or ""
    field = {
        "name": name,
        "expression": {"sql": name},      # 简单列引用（内部无计算字段）
    }
    if col.get("comment"):
        field["description"] = col["comment"]
    ctype = (col.get("type") or "").lower()
    # 时间类型 → 时间维度；非时间类型不标
    if any(k in ctype for k in ("date", "datetime", "timestamp", "time")):
        field["dimension"] = {"is_time": True}
    ext = _extensions({"source": col.get("source"), "status": col.get("status")})
    if ext:
        field["nonportable_extensions"] = ext
    return field


def _dataset_from_entity(ds):
    """内部数据集（{name,source,description,grain,synonyms,fields,extensions}）→ OSSIE Dataset。"""
    d = {
        "name": ds.get("name") or "",
        "source": ds.get("source") or "",
    }
    if ds.get("description"):
        d["description"] = ds["description"]
    syns = ds.get("synonyms") or []
    if syns:
        d["ai_context"] = {"synonyms": [str(s) for s in syns]}
    fields = ds.get("fields") or []
    if fields:
        d["fields"] = [_field_from_column(f) for f in fields]
    ext = _extensions(ds.get("extensions") or {})
    if ext:
        d["nonportable_extensions"] = ext
    return d


def _metric_from_metric(m):
    """内部指标 → OSSIE Metric（统一映射为【模型作用域】指标）。

    ★ expression.sql 优先取 sql_template（结构化 SQL），否则从 definition 提取，
      都没有则退化为 definition 原文（保证 expression 至少存在）。
      definition（自然语言口径）作为 description 保留，不丢语义。
    """
    name = m.get("name") or ""
    sql = (m.get("sql_template") or "").strip()
    if not sql:
        sql = _extract_sql_from_definition(m.get("definition") or "")
    if not sql:
        sql = (m.get("definition") or "").strip() or name
    metric = {
        "name": name,
        "expression": {"sql": sql},
    }
    if m.get("definition"):
        metric["description"] = m["definition"]
    mtype = m.get("metric_type") or ""
    if mtype and mtype in ("String", "Integer", "Decimal", "Float", "Boolean",
                           "Date", "Time", "DateTime", "DateTimeTz", "Opaque"):
        metric["datatype"] = mtype
    syns = m.get("keywords") or []
    if syns:
        metric["ai_context"] = {"synonyms": [str(s) for s in syns]}
    ext = _extensions(m.get("extensions") or {})
    if ext:
        metric["nonportable_extensions"] = ext
    return metric


def _extract_sql_from_definition(definition):
    """从自然语言口径文本里尽力提取 SQL 片段（启发式）。

    ★ 指标 definition 形如「完单量 = COUNT(*) WHERE status=6」，
      提取「=」右侧的部分作为 expression 的兜底；提不到返回空串。
    """
    d = (definition or "").strip()
    if not d:
        return ""
    # 取 "=" 或 "＝" 右侧
    for sep in ("=", "＝", ":"):
        if sep in d:
            right = d.split(sep, 1)[1].strip()
            if right:
                return right
    return ""


def _relationship_from_rel(rel):
    """内部关系 → OSSIE Relationship。

    ★ from_columns/to_columns 从 join_sql 尽力提取（等号两侧的列名）；
      提取不完美时完整 join_sql 保留在 nonportable_extensions 里用于往返回填。
    """
    name = rel.get("name") or f"{rel.get('from_entity', '')}->{rel.get('to_entity', '')}"
    from_cols, to_cols = _extract_join_columns(rel.get("join_sql") or "")
    # OSSIE 要求 from_columns/to_columns 至少 1 项；提不到就填占位（并在扩展里保留真相）
    if not from_cols:
        from_cols = [rel.get("from_entity") or name]
    if not to_cols:
        to_cols = [rel.get("to_entity") or name]
    r = {
        "name": name,
        "from": rel.get("from_entity") or "",
        "to": rel.get("to_entity") or "",
        "from_columns": from_cols,
        "to_columns": to_cols,
    }
    if rel.get("rule_note"):
        r["ai_context"] = {"instructions": rel["rule_note"]}
    ext = _extensions({
        "cardinality": rel.get("cardinality"),
        "join_sql": rel.get("join_sql"),
        "pre_agg_required": rel.get("pre_agg_required"),
    })
    if ext:
        r["nonportable_extensions"] = ext
    return r


def _extract_join_columns(join_sql):
    """从 join_sql（如 'a.order_id = b.order_id AND ...'）提取左右两侧列名。

    返回 (from_columns, to_columns) 两个列表。
    ★ 启发式：按 AND/逗号切段，取每段第一个 '=' 两侧的最后一节标识符作为列名。
      提取失败返回空列表，由调用方兜底。
    """
    if not join_sql:
        return [], []
    from_cols, to_cols = [], []
    for part in re.split(r"\bAND\b|,", join_sql, flags=re.IGNORECASE):
        if "=" not in part:
            continue
        left, right = part.split("=", 1)
        lc = _last_ident(left)
        rc = _last_ident(right)
        if lc and rc:
            from_cols.append(lc)
            to_cols.append(rc)
    return from_cols, to_cols


def _last_ident(text):
    """取一段 SQL 里最后一个标识符（支持 a.col / col / `col` / "col"）。"""
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text or "")
    return tokens[-1] if tokens else ""


def build_model(name, description="", datasets=None, metrics=None,
                relationships=None, model_extensions=None):
    """把内部语义模型组装成 OSSIE 标准文档（dict，可直接 dump 成 YAML/JSON）。

    参数：
        name       模型名（业务主题名）
        datasets   内部数据集列表（_dataset_from_entity 的输入形态）
        metrics    内部指标列表（模型作用域）
        relationships 内部关系列表
        model_extensions 模型级扩展属性 dict
    """
    doc = {
        "version": OSSIE_VERSION,
        "name": name,
        "datasets": [_dataset_from_entity(d) for d in (datasets or [])],
    }
    if description:
        doc["description"] = description
    if metrics:
        doc["metrics"] = [_metric_from_metric(m) for m in metrics]
    if relationships:
        doc["relationships"] = [_relationship_from_rel(r) for r in relationships]
    ext = _extensions(model_extensions or {})
    if ext:
        doc["nonportable_extensions"] = ext
    return doc


# ═══════════════════════════════════════════════════════════
# ⑤ OSSIE 标准模型 → 内部语义模型（导入方向）
# ═══════════════════════════════════════════════════════════
def _read_extensions(obj):
    """从 OSSIE 对象读回知数AI 扩展属性（data 原样返回，键过滤到白名单）。"""
    out = {}
    for ext in (obj.get("nonportable_extensions") or []):
        if ext.get("vendor_name") == ZHISHU_VENDOR and isinstance(ext.get("data"), dict):
            for k, v in ext["data"].items():
                if k in _EXTENSION_FIELDS:
                    out[k] = v
    return out


def _dataset_to_internal(ds):
    """OSSIE Dataset → 内部数据集。"""
    ext = _read_extensions(ds)
    syns = []
    ai = ds.get("ai_context")
    if isinstance(ai, dict):
        syns = ai.get("synonyms") or []
    fields = []
    for f in (ds.get("fields") or []):
        fext = _read_extensions(f)
        is_time = (f.get("dimension") or {}).get("is_time", False)
        fields.append({
            "name": f.get("name") or "",
            "expression": _expression_sql(f.get("expression")),
            "comment": f.get("description") or "",
            "source": fext.get("source") or "manual",
            "status": fext.get("status") or "approved",
            "is_time": bool(is_time),
        })
    return {
        "name": ds.get("name") or "",
        "source": ds.get("source") or "",
        "description": ds.get("description") or "",
        "grain": ext.get("grain") or "",
        "synonyms": syns,
        "fields": fields,
        "extensions": ext,
    }


def _metric_to_internal(m, model_ext=None):
    """OSSIE Metric → 内部指标。"""
    ext = _read_extensions(m)
    ai = m.get("ai_context")
    syns = []
    if isinstance(ai, dict):
        syns = ai.get("synonyms") or []
    elif isinstance(ai, str):
        syns = [ai]
    return {
        "name": m.get("name") or "",
        "sql_template": _expression_sql(m.get("expression")),
        "definition": m.get("description") or "",
        "keywords": syns,
        "metric_type": ext.get("metric_type") or "",
        "tables": ext.get("tables") or [],
        "extensions": ext,
    }


def _relationship_to_internal(r):
    """OSSIE Relationship → 内部关系。"""
    ext = _read_extensions(r)
    ai = r.get("ai_context")
    note = ""
    if isinstance(ai, dict):
        note = ai.get("instructions") or ""
    return {
        "name": r.get("name") or "",
        "from_entity": r.get("from") or "",
        "to_entity": r.get("to") or "",
        "from_columns": r.get("from_columns") or [],
        "to_columns": r.get("to_columns") or [],
        "join_sql": ext.get("join_sql") or _columns_to_join_sql(
            r.get("from"), r.get("to"), r.get("from_columns"), r.get("to_columns")),
        "cardinality": ext.get("cardinality") or "",
        "pre_agg_required": ext.get("pre_agg_required") or 0,
        "rule_note": note,
        "extensions": ext,
    }


def _expression_sql(expr):
    """取 expression 的顶层 sql（权威）；无则取 dialects 里第一个。"""
    if not isinstance(expr, dict):
        return ""
    if expr.get("sql"):
        return expr["sql"]
    for d in (expr.get("dialects") or []):
        if d.get("expression"):
            return d["expression"]
    return ""


def _columns_to_join_sql(from_ds, to_ds, from_cols, to_cols):
    """无扩展属性时，用 from/to 字段名拼一个 join_sql 兜底。"""
    pairs = []
    for fc, tc in zip(from_cols or [], to_cols or []):
        pairs.append(f"{from_ds}.{fc} = {to_ds}.{tc}")
    return " AND ".join(pairs) if pairs else ""


def parse_model(doc):
    """OSSIE 文档 → 内部语义模型（dict，供导入流程使用）。

    返回内部模型结构：
      {name, description, datasets[], metrics[], relationships[], model_extensions}
    ★ 不写库、不产生副作用，纯转换 —— 导入流程由上层（API/服务层）决定。
    """
    return {
        "name": doc.get("name") or "",
        "description": doc.get("description") or "",
        "datasets": [_dataset_to_internal(d) for d in (doc.get("datasets") or [])],
        "metrics": [_metric_to_internal(m) for m in (doc.get("metrics") or [])],
        "relationships": [_relationship_to_internal(r) for r in (doc.get("relationships") or [])],
        "model_extensions": _read_extensions(doc),
    }


# ═══════════════════════════════════════════════════════════
# ⑥ 引用完整性校验
# ═══════════════════════════════════════════════════════════
def validate_references(doc):
    """校验 OSSIE 文档的引用完整性（不依赖 schema 的类型校验）。

    检查：
      ① relationship 的 from/to 必须指向已声明的 dataset；
      ② relationship 的 from_columns/to_columns 字段数必须相等。
    返回 (ok, issues)：issues 为人类可读问题列表。

    ★ 说明：不做「模型作用域 metric 表达式引用的 dataset 是否存在」的检查 ——
      知数AI 的指标 expression 是【物理 SQL】（用物理表名/别名如 o.xxx、s.xxx），
      而非 OSSIE 规范的 dataset.field 语义；按 dataset 名去匹配会大量误报。
      该语义鸿沟已记录在 P1 报告「Metric expression 无法无损双向」。
    """
    issues = []
    ds_names = {d.get("name") for d in (doc.get("datasets") or [])}

    for r in (doc.get("relationships") or []):
        for side, key in (("from", "from"), ("to", "to")):
            v = r.get(key)
            if v and v not in ds_names:
                issues.append(f"关系 {r.get('name')!r} 的 {side} 指向不存在的 dataset {v!r}")
        if len(r.get("from_columns") or []) != len(r.get("to_columns") or []):
            issues.append(f"关系 {r.get('name')!r} 的 from_columns/to_columns 数量不一致")

    return (len(issues) == 0), issues


# ═══════════════════════════════════════════════════════════
# ⑦ 导入冲突识别
# ═══════════════════════════════════════════════════════════
def detect_conflicts(internal_model, existing):
    """对比待导入内部模型与现有对象，识别冲突。

    参数：
        internal_model  parse_model 的产出
        existing        现有对象名称集合（由上层传入，如现有指标名/实体名集合）
    返回 dict：
        {new: [...], exists: [...], conflict: [...]}
        new       = 全新的对象名（可安全新增）
        exists    = 已存在且内容一致（跳过）
        conflict  = 已存在但需要覆盖/更新的对象名（需用户确认）
    """
    seen = set()
    for d in internal_model.get("datasets") or []:
        seen.add(("dataset", d.get("name")))
    for m in internal_model.get("metrics") or []:
        seen.add(("metric", m.get("name")))
    for r in internal_model.get("relationships") or []:
        seen.add(("relationship", r.get("name")))

    new, exists, conflict = [], [], []
    for kind, name in seen:
        if not name:
            continue
        if name not in existing:
            new.append((kind, name))
        else:
            # 简化：已存在一律记为冲突，交由上层做「跳过/覆盖」决策（不做内容哈希对比）
            conflict.append((kind, name))
    return {"new": new, "exists": exists, "conflict": conflict}


# ═══════════════════════════════════════════════════════════
# ⑧ 兼容性报告
# ═══════════════════════════════════════════════════════════
def compatibility_report(doc, internal_model=None):
    """生成导出兼容性报告：哪些内容能/不能被外部工具识别。

    返回 dict：
      {version, name, counts, nonportable, warnings}
      nonportable = 导出为扩展属性、外部工具可能无法识别的字段清单（含说明）
      warnings    = 需人工关注的项（如 dimension 未映射、from_columns 兜底占位）
    """
    internal = internal_model or parse_model(doc)
    report = {
        "version": doc.get("version"),
        "name": doc.get("name"),
        "counts": {
            "datasets": len(doc.get("datasets") or []),
            "metrics": len(doc.get("metrics") or []),
            "relationships": len(doc.get("relationships") or []),
        },
        "nonportable": [],
        "warnings": [],
    }
    # 扩展属性说明：凡是进入 nonportable_extensions 的内部字段，外部工具不保证识别
    report["nonportable"].append({
        "field": "nonportable_extensions",
        "vendor": ZHISHU_VENDOR,
        "note": "知数AI 内部治理属性（knowledge_level/status/grain/cardinality 等）以扩展属性承载，"
                "外部工具不保证识别；知数AI 导入时可无损恢复。",
    })
    # 关系兜底占位告警
    for r in (doc.get("relationships") or []):
        name = r.get("name")
        from_cols = r.get("from_columns") or []
        if from_cols and from_cols[0] in (r.get("from"), name):
            report["warnings"].append(
                f"关系 {name!r} 的 from_columns 无法从 join_sql 提取，已用实体名占位，"
                "完整 join_sql 已存入扩展属性。")
    return report


# ═══════════════════════════════════════════════════════════
# ⑨ 序列化（导出文件）
# ═══════════════════════════════════════════════════════════
def to_yaml(doc):
    """把 OSSIE 文档序列化为 YAML 字符串（ensure ascii 关闭，中文直出）。"""
    return _yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False)


def to_json(doc):
    """把 OSSIE 文档序列化为 JSON 字符串。"""
    return json.dumps(doc, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════
# ⑩ 往返一致性测试（内部 → OSSIE → 内部，语义无损对比）
# ═══════════════════════════════════════════════════════════
def roundtrip_check(internal_model):
    """把内部模型导出为 OSSIE 再导回内部，对比关键语义是否保留。

    返回 (ok, report)：
      report = {datasets_count, metrics_count, relationships_count,
                missing_metrics, missing_datasets, missing_relationships,
                preserved_extensions, lost_fields}
    """
    doc = build_model(
        internal_model.get("name") or "",
        description=internal_model.get("description") or "",
        datasets=internal_model.get("datasets") or [],
        metrics=internal_model.get("metrics") or [],
        relationships=internal_model.get("relationships") or [],
        model_extensions=internal_model.get("model_extensions") or {},
    )
    back = parse_model(doc)

    orig_ds = {d["name"] for d in internal_model.get("datasets") or []}
    back_ds = {d["name"] for d in back.get("datasets") or []}
    orig_m = {m["name"] for m in internal_model.get("metrics") or []}
    back_m = {m["name"] for m in back.get("metrics") or []}
    orig_r = {r.get("name") for r in internal_model.get("relationships") or []}
    back_r = {r.get("name") for r in back.get("relationships") or []}

    # 扩展属性保留检查：逐指标对比关键扩展字段
    lost_fields = []
    orig_by_name = {m["name"]: m for m in internal_model.get("metrics") or []}
    back_by_name = {m["name"]: m for m in back.get("metrics") or []}
    for name, om in orig_by_name.items():
        bm = back_by_name.get(name)
        if not bm:
            continue
        for k in ("metric_type", "tables"):
            if om.get("extensions", {}).get(k) != bm.get("extensions", {}).get(k):
                lost_fields.append(f"metric:{name}.{k}")

    ok = (orig_ds == back_ds and orig_m == back_m and orig_r == back_r and not lost_fields)
    report = {
        "datasets_count": (len(orig_ds), len(back_ds)),
        "metrics_count": (len(orig_m), len(back_m)),
        "relationships_count": (len(orig_r), len(back_r)),
        "missing_datasets": sorted(orig_ds - back_ds),
        "missing_metrics": sorted(orig_m - back_m),
        "missing_relationships": sorted(orig_r - back_r),
        "lost_fields": lost_fields,
    }
    return ok, report
