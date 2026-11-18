# -*- coding: utf-8 -*-
"""
test_ossie_adapter.py —— Apache Ossie 适配层测试（纯函数，无 DB / 无 LLM 依赖）

★ 覆盖任务书 §6.4「往返一致性测试」+ §5.5 Adapter 各能力：
   ① Schema 校验（合法 / 缺必填 / 未定义字段）
   ② 版本识别
   ③ YAML / JSON 解析
   ④ 双向转换（内部 ⇄ OSSIE）
   ⑤ 往返一致性（对象数 / 关系 / 扩展属性语义无损）
   ⑥ 引用完整性校验
   ⑦ 冲突识别
   ⑧ 兼容性报告

运行： .venv/bin/python test_ossie_adapter.py
"""
import json

import ossie_adapter as oa


# ── 一个最小可用的内部语义模型（供各用例复用）──
def _sample_model():
    return {
        "name": "订单主题",
        "description": "订单与客户分析",
        "datasets": [
            {
                "name": "代驾订单", "source": "ods_tu_order_all",
                "description": "代驾订单明细", "grain": "一行=一个订单",
                "synonyms": ["订单", "order"],
                "fields": [
                    {"name": "order_id", "type": "Int64", "comment": "订单号", "source": "manual"},
                    {"name": "ctime", "type": "DateTime", "comment": "下单时间", "source": "manual"},
                ],
                "extensions": {"knowledge_level": 2, "status": "approved", "source": "manual"},
            },
        ],
        "metrics": [
            {"name": "完单量", "definition": "完单量 = COUNT(*) WHERE status=6", "sql_template": "COUNT(*)",
             "keywords": ["完单"], "metric_type": "count", "tables": ["ods_tu_order_all"],
             "extensions": {"knowledge_level": 1, "status": "approved",
                            "metric_type": "count", "tables": ["ods_tu_order_all"]}},
        ],
        "relationships": [
            {"name": "订单->司机", "from_entity": "代驾订单", "to_entity": "司机",
             "join_sql": "ods_tu_order_all.driver_id = ods_driver.driver_id",
             "cardinality": "N:1", "pre_agg_required": 0, "rule_note": "按司机关联",
             "extensions": {"cardinality": "N:1", "pre_agg_required": 0}},
        ],
        "model_extensions": {"knowledge_level": 1, "status": "approved"},
    }


def _build():
    m = _sample_model()
    return oa.build_model(m["name"], description=m["description"],
                          datasets=m["datasets"], metrics=m["metrics"],
                          relationships=m["relationships"], model_extensions=m["model_extensions"])


PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  ✅ {name}")
    else:
        FAILED.append(name)
        print(f"  ❌ {name} {detail}")


# ═══════════════════════════════════════════════════════════
def test_schema_valid():
    doc = _build()
    ok, errs = oa.validate_document(doc)
    check("合法文档通过 Schema 校验", ok, str(errs))


def test_schema_missing_required():
    doc = {"version": oa.OSSIE_VERSION, "name": "x"}     # 缺 datasets
    ok, errs = oa.validate_document(doc)
    check("缺必填字段 datasets 被拦截", not ok and any("datasets" in e for e in errs), str(errs))


def test_schema_unknown_field():
    doc = _build()
    doc["datasets"][0]["未知字段"] = "x"                  # additionalProperties: false
    ok, errs = oa.validate_document(doc)
    check("未定义字段被拦截（additionalProperties=false）", not ok, str(errs))


def test_schema_wrong_version():
    doc = _build()
    doc["version"] = "0.1.1"
    ok, errs = oa.validate_document(doc)
    check("版本常量不匹配被拦截", not ok and any("常量" in e for e in errs), str(errs))


def test_version_detect():
    doc = _build()
    check("版本识别", oa.detect_version(doc) == oa.OSSIE_VERSION)
    check("版本支持判断", oa.is_supported_version(doc))
    check("不支持版本判断", not oa.is_supported_version({"version": "9.9.9"}))


def test_parse_yaml_and_json():
    doc = _build()
    y = oa.to_yaml(doc)
    j = oa.to_json(doc)
    dy, e1 = oa.parse_document(y)
    dj, e2 = oa.parse_document(j)
    ok1, _ = oa.validate_document(dy)
    ok2, _ = oa.validate_document(dj)
    check("YAML 序列化后回解析可校验", e1 is None and ok1, str(e1))
    check("JSON 序列化后回解析可校验", e2 is None and ok2, str(e2))
    check("格式自动识别", oa.detect_format(y) == "yaml" and oa.detect_format(j) == "json")


def test_roundtrip():
    m = _sample_model()
    ok, rep = oa.roundtrip_check(m)
    check("往返一致性（对象无丢失）", ok, json.dumps(rep, ensure_ascii=False))
    check("扩展属性往返不丢失", rep["lost_fields"] == [], str(rep["lost_fields"]))


def test_reference_integrity():
    doc = _build()
    # 关系 to 指向不存在的 dataset「司机」→ 应报问题
    ok, issues = oa.validate_references(doc)
    check("关系指向不存在 dataset 被识别", not ok and any("司机" in i for i in issues), str(issues))
    # 把「司机」补成 dataset 后应通过
    doc["datasets"].append({"name": "司机", "source": "ods_driver"})
    ok2, issues2 = oa.validate_references(doc)
    check("补齐 dataset 后引用完整性通过", ok2, str(issues2))


def test_conflict_detect():
    m = _sample_model()
    internal = {
        "datasets": [{"name": "代驾订单"}],
        "metrics": [{"name": "完单量"}],
        "relationships": [{"name": "订单->司机"}],
    }
    c = oa.detect_conflicts(internal, existing={"完单量", "新指标"})
    # 「完单量」已在 existing → 冲突；「代驾订单」「订单->司机」不在 → 新增
    has_metric_conflict = ("metric", "完单量") in [tuple(x) for x in c["conflict"]]
    has_ds_new = ("dataset", "代驾订单") in [tuple(x) for x in c["new"]]
    check("冲突识别（已存在指标→冲突）", has_metric_conflict, str(c))
    check("冲突识别（新对象→新增）", has_ds_new, str(c))


def test_metric_expression_mapping():
    doc = _build()
    # 完单量的 expression.sql 应来自 sql_template
    m = doc["metrics"][0]
    check("指标 expression.sql 取 sql_template", m["expression"]["sql"] == "COUNT(*)")
    check("指标 definition 作为 description 保留", m["description"].startswith("完单量"))
    # 无 sql_template 时从 definition 提取兜底
    dm = oa._metric_from_metric({"name": "x", "definition": "x = COUNT(id)"})
    check("无 sql_template 时从 definition 提取", dm["expression"]["sql"] == "COUNT(id)")


def test_time_dimension_mapping():
    doc = _build()
    fields = {f["name"]: f for f in doc["datasets"][0]["fields"]}
    # ctime 是 DateTime → 应标 is_time；order_id 是 Int64 → 不应标
    check("时间字段映射为时间维度", fields["ctime"].get("dimension", {}).get("is_time") is True)
    check("非时间字段不标维度", "dimension" not in fields["order_id"])


def test_compatibility_report():
    doc = _build()
    rep = oa.compatibility_report(doc)
    check("兼容性报告含对象计数", rep["counts"]["datasets"] == 1 and rep["counts"]["metrics"] == 1)
    check("兼容性报告标注扩展属性", any("nonportable_extensions" in n["field"] for n in rep["nonportable"]))


def main():
    print()
    print("=" * 62)
    print("Apache Ossie 适配层测试")
    print("=" * 62)
    for fn in [test_schema_valid, test_schema_missing_required, test_schema_unknown_field,
               test_schema_wrong_version, test_version_detect, test_parse_yaml_and_json,
               test_roundtrip, test_reference_integrity, test_conflict_detect,
               test_metric_expression_mapping, test_time_dimension_mapping,
               test_compatibility_report]:
        fn()
    print("-" * 62)
    total = len(PASSED) + len(FAILED)
    if not FAILED:
        print(f"🎉 全部通过（{len(PASSED)}/{total}）")
    else:
        print(f"⚠️ {len(FAILED)}/{total} 失败：{FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
