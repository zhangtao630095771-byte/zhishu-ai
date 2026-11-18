# -*- coding: utf-8 -*-
"""
test_semantic_center.py —— 语义中心重构（阶段五~七）关键不变量测试

★ 覆盖（AI 智能建模 + 取数融合的铁律）：
  ① AI 录入的维度 → candidate（待审核），不直接生效；
  ② AI 不覆盖人工登记的维度（source 护栏）；
  ③ 严格模式下 candidate 不进生产检索（治理准入）；
  ④ 业务场景整链建模的 Prompt 覆盖实体/关系/指标/维度全类型。

★ 只依赖本地配置库（config 层），不调真实 LLM、不连 ClickHouse。
"""
import config_store
import governance
import semantic_modeling
import ai_ingest


def _cleanup_dim(test_key):
    conn = config_store._connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM semantic_dimensions WHERE dimension_key = ?", (test_key,))
    conn.commit()
    conn.close()


# ── ① AI 录入维度 → candidate ──
def test_ai_dimension_is_candidate():
    _cleanup_dim("test_sem_ai_dim")
    ok, _ = semantic_modeling.upsert_dimension(
        {"dimension_key": "test_sem_ai_dim", "name": "测试AI维度", "dimension_type": "status"},
        source="ai")
    assert ok, "AI 登记维度应成功"
    d = [x for x in semantic_modeling.list_dimensions()
         if x["dimension_key"] == "test_sem_ai_dim"][0]
    assert d["status"] == "candidate", f"AI 维度应为 candidate，实际 {d['status']}"
    assert d["source"] == "ai", f"AI 维度 source 应为 ai，实际 {d['source']}"
    _cleanup_dim("test_sem_ai_dim")


# ── ② AI 不覆盖人工维度 ──
def test_ai_dimension_not_override_manual():
    _cleanup_dim("test_sem_man_dim")
    ok1, _ = semantic_modeling.upsert_dimension(
        {"dimension_key": "test_sem_man_dim", "name": "人工维度", "dimension_type": "status"},
        source="manual")
    assert ok1, "人工登记维度应成功"
    ok2, msg = semantic_modeling.upsert_dimension(
        {"dimension_key": "test_sem_man_dim", "name": "AI想改", "dimension_type": "status"},
        source="ai")
    assert not ok2, "AI 不得覆盖人工维度"
    assert "不覆盖" in msg
    _cleanup_dim("test_sem_man_dim")


# ── ③ 严格模式下 candidate 不进生产检索 ──
def test_candidate_not_visible_in_strict_mode():
    if not governance.strict():
        # 非严格模式本就不拦 candidate，跳过（不误报）
        return
    ids = config_store.get_visible_knowledge_ids("entities", "name")
    assert ids is not None, "entities 有 status 列，应返回可见集合"
    conn = config_store._connect()
    cur = conn.cursor()
    cand = [r[0] for r in cur.execute(
        "SELECT name FROM entities WHERE status='candidate'").fetchall()]
    conn.close()
    for n in cand:
        assert n not in ids, f"candidate 实体「{n}」不应进入生产检索"


# ── ④ 整链建模 Prompt 覆盖全类型 ──
def test_scenario_prompt_covers_all_types():
    p = ai_ingest.build_scenario_prompt("订单经营场景")
    for key in ("entities", "relationships", "metrics", "dimensions",
                "model_name", "model_display"):
        assert key in p, f"场景建模 Prompt 缺少类型 {key}"


# ── ⑤ 治理准入与 semantic_layer 一致（approved 可见） ──
def test_approved_visible():
    ids = config_store.get_visible_knowledge_ids("metrics", "name")
    if ids is None:
        return  # 无 status 列则不过滤（旧库兼容）
    # 任一 approved 指标应在可见集合中
    conn = config_store._connect()
    cur = conn.cursor()
    approved = [r[0] for r in cur.execute(
        "SELECT name FROM metrics WHERE status='approved' LIMIT 5").fetchall()]
    conn.close()
    for n in approved:
        assert n in ids, f"approved 指标「{n}」应可见"


if __name__ == "__main__":
    _passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            _passed += 1
            print(f"  ✅ {name}")
    print(f"\n🎉 全部通过（{_passed} 项）")
