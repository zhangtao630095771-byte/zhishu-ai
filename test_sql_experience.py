# -*- coding: utf-8 -*-
"""
test_sql_experience.py —— SQL 经验中心 测试

★ 三层：
  ① 纯逻辑（不碰库）：规范化、可信度状态机、适用性判定、失败模式检查、Prompt 措辞
  ② 状态机 + 写入（临时库）：审核/验证/发布/撤销/修正对照/去重/使用日志
  ③ 关键红线：审核通过但执行失败 → 永远不能成为黄金 SQL（实测数据里真有这种案例）

运行：
    .venv/bin/python test_sql_experience.py
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config_store            # noqa: E402
import sql_experience as X     # noqa: E402

_passed = 0
_failed = []


def ok(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name} {detail}")


# ═══════════════════════════════════════════════════════════
# ① 纯逻辑
# ═══════════════════════════════════════════════════════════
def test_normalize():
    a = "SELECT a FROM t WHERE x='1'  -- 注释\n"
    b = "select a from t where x='1'"
    ok(X.normalize_sql(a) != X.normalize_sql(b), "规范化区分大小写（保留原文）")
    ok(X.sql_hash(a) == X.sql_hash(b), "指纹忽略大小写与注释",
       f"{X.sql_hash(a)} vs {X.sql_hash(b)}")
    ok(X.sql_hash("SELECT 1") != X.sql_hash("SELECT 2"), "不同 SQL 指纹不同")
    ok(X.norm_question("查询 订单量？") == X.norm_question("查询订单量"),
       "问题归一化去掉标点空白")
    ok(X.sql_hash("/* 无法回答 */ SELECT 1") == X.sql_hash("SELECT 1"),
       "块注释不影响指纹")


def test_trust_state_machine():
    """★ 这是整套机制的安全底座，逐条覆盖"""
    base = {"kind": "success", "exec_ok": 1, "review_status": "approved",
            "verify_status": "unverified", "status": "active", "golden_published": 0}
    ok(X.compute_trust(base) == "candidate", "审核通过但未验证 → candidate")
    ok(X.compute_trust(dict(base, verify_status="reexec_ok")) == "golden_candidate",
       "重跑可执行 → golden_candidate")
    ok(X.compute_trust(dict(base, verify_status="manual_confirmed")) == "golden_candidate",
       "人工确认未发布 → golden_candidate")
    ok(X.compute_trust(dict(base, verify_status="manual_confirmed", golden_published=1))
       == "golden", "人工确认 + 已发布 → golden")
    ok(X.compute_trust(dict(base, verify_status="result_verified", golden_published=1))
       == "golden", "结果已验证 + 已发布 → golden")
    # ★ 红线 1：审核通过但执行失败
    ok(X.compute_trust(dict(base, exec_ok=0, verify_status="manual_confirmed",
                            golden_published=1)) == "candidate",
       "★ 审核通过但执行失败 → 永远 candidate")
    # ★ 红线 2：验证不通过
    ok(X.compute_trust(dict(base, verify_status="verify_failed", golden_published=1))
       == "candidate", "★ 验证不通过 → 不能是 golden")
    # ★ 红线 3：未审核
    ok(X.compute_trust(dict(base, review_status="none", verify_status="manual_confirmed",
                            golden_published=1)) == "candidate",
       "★ 未通过分类审核 → 不能是 golden")
    # ★ 红线 4：失败/修正案例
    ok(X.compute_trust(dict(base, kind="failure", verify_status="result_verified",
                            golden_published=1)) == "candidate",
       "★ 失败案例 → 不能是 golden")
    # ★ 红线 5：失效/撤销
    ok(X.compute_trust(dict(base, status="revoked", verify_status="result_verified",
                            golden_published=1)) == "revoked", "撤销 → revoked")
    ok(X.compute_trust(dict(base, status="stale")) == "revoked", "待复核 → 退出正向召回")


def test_applicable():
    case = {"status": "active", "tables": ["ctc.ods_a", "ods_b"], "metrics": ["完单量"],
            "dialect": "ClickHouse"}
    ok(X._applicable(case, ["ods_a"], ["完单量"], ["ods_a", "ods_b"], "ClickHouse")[0],
       "表与指标都匹配 → 可召回")
    r = X._applicable(case, ["ods_a"], ["完单量"], ["ods_a"], "ClickHouse")
    ok(not r[0] and "不可访问" in r[1], "涉及无权限表 → 拒绝", str(r))
    r = X._applicable(case, ["ods_a"], ["订单量"], ["ods_a", "ods_b"], "ClickHouse")
    ok(not r[0] and "指标" in r[1], "指标口径不一致 → 拒绝", str(r))
    r = X._applicable(case, ["ods_x"], ["完单量"], ["ods_a", "ods_b"], "ClickHouse")
    ok(not r[0] and "交集" in r[1], "表集合无交集 → 拒绝", str(r))
    r = X._applicable(case, ["ods_a"], ["完单量"], ["ods_a", "ods_b"], "SQLite")
    ok(r[0] and r[2], "方言不同 → 允许但标记仅参考", str(r))
    r = X._applicable(dict(case, status="revoked"), ["ods_a"], ["完单量"],
                      ["ods_a", "ods_b"], "ClickHouse")
    ok(not r[0], "已撤销案例 → 不参与召回")


def test_check_sql():
    bad_sql = "SELECT count(1) FROM ods_tu_order_all WHERE status = 40"
    corrected = ("SELECT count(1) FROM ods_tu_order_all WHERE status = 40 "
                 "AND is_salesman = 0")
    failure = {
        "id": 1, "case_no": "EXP-T-0001", "failure_type": "wrong_field",
        "sql_hash": X.sql_hash(bad_sql), "sql_raw": bad_sql,
        "corrected_sql": corrected, "question": "q",
    }
    m = X.ExperienceMatch()
    m.failures = [failure]
    issues = X.check_sql(bad_sql, m)
    ok(any(i["code"] == "same_as_known_bad_sql" for i in issues),
       "与历史错 SQL 指纹相同 → error", str(issues))

    # 换了写法（不再 fingerprint 相同）但用了错表
    other = "SELECT count(1) FROM ods_wrong_table WHERE status = 40"
    m2 = X.ExperienceMatch()
    m2.failures = [dict(failure, sql_hash=X.sql_hash(other), sql_raw=other,
                        corrected_sql="SELECT count(1) FROM ods_right_table")]
    issues = X.check_sql("SELECT count(1) FROM ods_wrong_table WHERE x=1", m2)
    ok(any(i["code"] == "known_wrong_table" for i in issues),
       "命中历史错表且未用正表 → warning", str(issues))

    # 缺状态条件
    m3 = X.ExperienceMatch()
    m3.failures = [dict(failure, failure_type="missing_filter",
                        sql_hash="x", sql_raw="SELECT 1",
                        corrected_sql="SELECT 1 FROM t WHERE status = 40")]
    issues = X.check_sql("SELECT 1 FROM t WHERE status = 20", m3)
    ok(any(i["code"] == "missing_status_filter" for i in issues),
       "修正写法里的状态条件缺失 → warning", str(issues))
    ok(X.check_sql("SELECT 1 FROM t", None) == [], "没有失败经验 → 无告警")


def test_prompt_block_wording():
    m = X.ExperienceMatch()
    m.cases = [{"id": 1, "case_no": "EXP-T-0001", "question": "q", "sql_raw": "select 1",
                "trust_level": "golden", "trust_zh": "正式黄金SQL",
                "verify_status": "result_verified", "verify_zh": "人工确认 + 机器证据",
                "review_status": "approved", "grain": "按天", "score": 0.9, "reasons": []}]
    m.failures = [{"id": 2, "case_no": "EXP-T-0002", "question": "q2",
                   "failure_type": "wrong_table", "sql_raw": "select 2",
                   "failure_symptom": "数翻倍", "corrected_sql": "select 3",
                   "root_cause_confirmed": 1}]
    m.rules = [{"id": 3, "rule_no": "RULE-1", "layer": "metric_rule", "title": "t",
                "content": "c", "template_sql": "select 4", "scope": "s"}]
    block = X.build_prompt_block(m)
    ok("不得覆盖" in block, "成功案例块明确「不得覆盖规则」")
    ok("禁止作为答案参考" in block, "失败经验明确「禁止作为答案参考」")
    ok("EXP-T-0001" in block and "EXP-T-0002" in block and "RULE-1" in block,
       "三类内容都带出处编号")
    ok("可信度" in block and "验证" in block, "案例标注可信度与验证状态")


def test_group_and_conflict():
    cases = [{"metrics": ["完单量"], "tables": ["t1"]},
             {"metrics": [], "tables": ["t2"]},
             {"metrics": [], "tables": []}]
    g = X._group_cases(cases)
    ok("完单量" in g and "t2" in g and "未归类" in g, "按指标优先、否则主表分组", str(list(g)))
    rule = {"metrics": ["完单量"], "tables": ["t9"]}
    note = X._conflict_of(rule, {"完单量": {"tables": ["t1"]}})
    ok("不一致" in note, "规则用表与指标核准用表冲突 → 记录冲突说明", note)
    ok(X._conflict_of(rule, {}) == "", "无指标定义时不臆造冲突")


def test_parse_json():
    ok(X._parse_json_obj('```json\n{"rules":[]}\n```') == {"rules": []},
       "能解析带代码块的 JSON")
    ok(X._parse_json_obj("不是JSON") is None, "非法输出返回 None（不崩）")


# ═══════════════════════════════════════════════════════════
# ② 状态机 + 写入（临时库，不碰生产配置库）
# ═══════════════════════════════════════════════════════════
def with_temp_db(fn):
    """把 config_store 指向临时库，跑完还原。★ 不允许测试改动真实配置库。"""
    import gc
    orig_db = config_store.CONFIG_DB
    orig_ready = dict(config_store._state)
    tmp = tempfile.mktemp(suffix=".db")
    try:
        config_store.CONFIG_DB = tmp
        config_store._state["ready"] = False
        config_store.init_store()
        config_store._state["ready"] = True
        fn()
    finally:
        config_store.CONFIG_DB = orig_db
        config_store._state.update(orig_ready)
        gc.collect()
        for suffix in ("", "-wal", "-shm"):
            p = tmp + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def test_write_flow():
    def _run():
        # 导入（含一条 approved+ok=1 / approved+ok=0 的脏数据）
        conn = sqlite3.connect(config_store.CONFIG_DB)
        conn.execute("DELETE FROM query_records")
        rows = [
            ("查询完单量", "SELECT count(1) FROM ods_a", 1, '["ods_a"]', '["完单量"]', "approved"),
            # ★ 审核通过但执行失败：实测库里真有这种数据（id=163）
            ("查询订单量", "SELECT count(1) FROM ods_a WHERE status=40", 0, '["ods_a"]',
             '["订单量"]', "approved"),
        ]
        for q, s, okv, tables, metrics, st in rows:
            conn.execute(
                "INSERT INTO query_records (username, question, sql, ok, tables, metrics, "
                "dicts, columns, row_count, result_preview, answer, info, attempts, pipeline, "
                "understanding, review_status, created_at) "
                "VALUES (?,?,?,?,?,?,'[]','[]',0,'','','',1,'v2','{}',?,'2026-10-01')",
                ("u", q, s, okv, tables, metrics, st))
        conn.commit()
        conn.close()

        stats = X.import_from_records(operator="test")
        ok(stats["created"] == 2, "两条记录导入成功", str(stats))
        ok(stats["by_kind"].get("success") == 1 and stats["by_kind"].get("failure") == 1,
           "按 exec_ok 正确分类（失败也被识别）", str(stats["by_kind"]))

        # 再导一次：应更新而不是新建（幂等）
        stats2 = X.import_from_records(operator="test")
        ok(stats2["created"] == 0 and stats2["updated"] == 2, "重复导入幂等", str(stats2))

        cases = X.list_cases(limit=10)["items"]
        succ = [c for c in cases if c["kind"] == "success"][0]
        fail = [c for c in cases if c["kind"] == "failure"][0]
        ok(succ["trust_level"] == "candidate" and succ["verify_status"] == "unverified",
           "导入不自动升级：审核通过仍是 candidate/未验证")
        ok(fail["failure_type"] == "sql_error", "执行失败的案例类型=sql_error")

        # ★ 红线：approved + ok=0 不能升黄金候选
        #   ① 直接导入的失败案例（kind=failure）→ 第一道门就挡住
        okd, msg = X.promote_case(fail["id"], "golden_candidate", "tester")
        ok(not okd, "★ 执行失败的案例拒绝升级为黄金候选", msg)
        #   ② 就算被误标成 success（历史脏数据的样子），exec_ok=0 也必须挡住
        dirty, _ = X.upsert_case({"kind": "success", "source_type": "manual", "source_id": "",
                                  "question": "脏数据", "sql_raw": "select 9",
                                  "review_status": "approved", "exec_ok": False,
                                  "verify_status": "manual_confirmed"})
        okd, msg = X.promote_case(dirty, "golden_candidate", "tester")
        ok(not okd and "执行失败" in msg, "★ approved+ok=0 拒绝升级（实测脏数据的形态）", msg)

        # 未验证 → 不能升黄金候选
        no, msg = X.promote_case(succ["id"], "golden_candidate", "tester")
        ok(not no and "验证" in msg, "未验证 → 拒绝升黄金候选", msg)

        # 机器重跑（用假证据，不真的连库）
        vok, vmsg = X.set_case_verify(succ["id"], "reexec_ok", "reexec",
                                      {"summary": "测试"}, "tester")
        ok(vok, "记录重跑验证", vmsg)
        c = X.get_case(succ["id"])
        ok(c["trust_level"] == "golden_candidate", "approved+ok+重跑 → golden_candidate",
           c["trust_level"])

        # 正式发布需要人工确认
        no, msg = X.promote_case(succ["id"], "golden", "tester")
        ok(not no and "人工确认" in msg, "重跑不足以发布正式黄金 SQL", msg)

        X.set_case_verify(succ["id"], "manual_confirmed", "manual", {"summary": "跟运营核对"}, "tester")
        yes, msg = X.promote_case(succ["id"], "golden", "tester")
        ok(yes, "人工确认后发布正式黄金 SQL", msg)
        ok(X.get_case(succ["id"])["trust_level"] == "golden", "trust_level=golden")

        # 撤销必须写原因
        no, msg = X.promote_case(succ["id"], "revoked", "tester", "")
        ok(not no, "撤销不写原因 → 拒绝", msg)
        yes, msg = X.promote_case(succ["id"], "revoked", "tester", "口径变更")
        ok(yes and X.get_case(succ["id"])["trust_level"] == "revoked", "撤销成功并降级")

        # 修正对照
        okc, msg, cid = X.attach_correction(fail["id"], "SELECT count(1) FROM ods_a WHERE status=4",
                                            note="状态码写错", operator="tester",
                                            root_cause="用了取消状态而非完成状态")
        ok(okc and cid, "挂修正 SQL 并生成修正案例", msg)
        fc = X.get_case(fail["id"])
        ok(fc["root_cause_confirmed"] == 1, "写根因 = 确认根因")
        corr = X.get_case(cid)
        ok(corr["kind"] == "correction" and corr["correction_of"] == fail["id"],
           "修正案例指向原失败案例")

        # 去重：同问题同 SQL 再导一次 → dup_of 指向已有案例
        X.upsert_case({"kind": "success", "source_type": "manual", "source_id": "",
                       "question": "查询完单量", "sql_raw": "SELECT count(1) FROM ods_a",
                       "review_status": "none", "exec_ok": True}, operator="tester")
        # 已撤销的案例不再参与去重（status!=active），应视为新案例
        dup = [c for c in X.list_cases(limit=20)["items"]
               if c["question"] == "查询完单量" and c["source_type"] == "manual"]
        ok(len(dup) == 1, "手工案例入库")

        # 使用日志
        m = X.ExperienceMatch()
        m.cases = [{"id": succ["id"], "case_no": "X", "score": 0.9, "reasons": ["t"]}]
        m.failures = [{"id": fail["id"], "case_no": "Y", "score": 0.6}]
        m.rejected = [{"case_no": "Z", "reason": "指标不一致", "stage": "applicability"}]
        X.log_usage(m, record_id=1, question="查询完单量", username="u", ok=True)
        u = X.list_usage(limit=20)
        recalls = [r for r in u if r["event"] == "recall"]
        skips = [r for r in u if r["event"] == "skip"]
        ok(len(recalls) == 2, "召回日志 2 条（成功案例 + 失败模式）", str(len(recalls)))
        ok(len(skips) == 1, "跳过日志 1 条（含未采用原因）", str(len(skips)))
        ok(any(r["event"] == "skip" and "指标" in (r["reason"] or "") for r in u),
           "未采用原因被记录")

        # 总览
        ov = X.overview()
        ok(ov["cases_total"] >= 4 and ov["approved_but_failed"] >= 1,
           "总览统计（含 approved+ok=0 计数）", str(ov["approved_but_failed"]))

        # 影响面
        aff = X.affected_cases(table="ods_a")
        ok(len(aff) >= 2, "按表查受影响案例", str(len(aff)))

    with_temp_db(_run)


def test_no_write_on_rejected_review():
    def _run():
        cid, _ = X.upsert_case({"kind": "success", "source_type": "manual", "source_id": "",
                                "question": "q", "sql_raw": "select 1", "exec_ok": True,
                                "review_status": "none"})
        okc, msg = X.set_case_review(cid, "rejected", "admin", "")
        ok(not okc and "原因" in msg, "驳回不写原因 → 拒绝")
        okc, _ = X.set_case_review(cid, "rejected", "admin", "口径错")
        c = X.get_case(cid)
        ok(okc and c["review_status"] == "rejected" and c["verify_status"] == "unverified",
           "★ 审核结论不影响验证状态（两者独立）")
        # 失败案例不能标记为结果已验证
        fid, _ = X.upsert_case({"kind": "failure", "source_type": "manual", "source_id": "",
                                "question": "q2", "sql_raw": "select 2", "exec_ok": False,
                                "failure_type": "wrong_table"})
        okv, msg = X.set_case_verify(fid, "result_verified", "manual", {}, "t")
        ok(not okv, "失败案例不能标记结果已验证", msg)
    with_temp_db(_run)


def main():
    for fn in (test_normalize, test_trust_state_machine, test_applicable, test_check_sql,
               test_prompt_block_wording, test_group_and_conflict, test_parse_json,
               test_write_flow, test_no_write_on_rejected_review):
        try:
            fn()
        except Exception as e:
            import traceback
            _failed.append(f"{fn.__name__} 异常：{e}\n{traceback.format_exc()}")
    print("=" * 60)
    if _failed:
        print(f"❌ 失败 {len(_failed)} 项（通过 {_passed} 项）")
        for f in _failed:
            print("  ❌", f)
        return 1
    print(f"🎉 全部通过（{_passed} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
