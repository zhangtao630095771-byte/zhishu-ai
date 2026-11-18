# -*- coding: utf-8 -*-
"""
_review_candidates.py —— candidate 批量复核脚本（默认 dry-run，--apply 才改库）。

★ 背景（企业业务语义知识库建设 · candidate 复核）：
  64 条 candidate（AI 生成、未人工复核）卡在待复核态，严格模式下不进生产检索。
  本脚本把《09-candidate-review-precheck.md》的预审结论落成可执行动作。

★ 分档（与 09 文档一致）：
  A 档 = 证据充分、建议 approve（3 指标 + 4 实体 + 2 表注释 + 49 字段注释）
  C 档 = 已去重停用的冗余实体，建议 rejected（3 条）
  B 档 = 2 条字典（field_hint 冲突），本脚本【不处理】，需人工决策

★ 安全设计：
  1. 默认 dry-run，只打印将要执行的动作，不写库；必须显式 --apply 才生效。
  2. 全程走 config_store.set_knowledge_status（写 knowledge_history + audit_log）。
  3. 幂等：approve 已 approved 的会跳过（set_knowledge_status 对非 candidate 也不报错，
     但本脚本只对「当前 status=candidate」的条目执行，避免误改已裁决的知识）。

运行：
  .venv/bin/python _review_candidates.py            # dry-run 预览
  .venv/bin/python _review_candidates.py --apply A  # 执行 A 档 approve
  .venv/bin/python _review_candidates.py --apply C  # 执行 C 档 reject
"""
import sys
import config_store

OPERATOR = "review_candidates"

# ── A 档：建议 approve（高置信度）──
A_METRICS = ["小板完单量", "小板完单金额", "总积分"]
A_ENTITIES = ["司机保证金订单", "司机保证金转出", "客户渠道归因", "客户黑名单"]
A_TABLE_COMMENTS = ["ods_customer_blacklist_all", "ods_valet_deposit_all"]
A_DOCS = ["14"]   # 《客户积分与分渠道完单统计取数口径》，与 A 档指标口径自洽

# ── C 档：建议 rejected（已去重停用的冗余实体）──
C_ENTITIES = ["客户积分批次表", "客户积分完单记录表", "客户积分流水/完单记录表"]


def _collect_plan():
    """组装「将要执行的动作」清单。返回 [(layer, key, target_status, label), ...]"""
    plan = []
    for name in A_METRICS:
        plan.append(("metrics", name, "approved", "指标"))
    for name in A_ENTITIES:
        plan.append(("entities", name, "approved", "实体"))
    for t in A_TABLE_COMMENTS:
        plan.append(("table_comments", t, "approved", "表注释"))
    for doc_id in A_DOCS:
        plan.append(("kb_docs", doc_id, "approved", "文档"))
    # 字段注释：动态取当前 candidate 的全部条目（49 条）
    conn = config_store._connect()
    rows = conn.execute(
        "SELECT table_name, column_name FROM column_comments WHERE status='candidate'"
    ).fetchall()
    conn.close()
    for t, c in rows:
        plan.append(("column_comments", "%s.%s" % (t, c), "approved", "字段注释"))
    # C 档
    for name in C_ENTITIES:
        plan.append(("entities", name, "rejected", "冗余实体(已停用)"))
    return plan


def _is_candidate(layer, key):
    """仅当对象当前 status=candidate 时才执行，避免误改已裁决知识。"""
    spec = config_store._GOV_LAYERS.get(layer)
    if not spec:
        return False
    conn = config_store._connect()
    try:
        where_sql, args = config_store._gov_where(layer, key)
        row = conn.execute(
            "SELECT status FROM %s WHERE %s" % (spec["table"], where_sql), args
        ).fetchone()
    except Exception:
        row = None
    finally:
        conn.close()
    return bool(row) and (row[0] or "").strip().lower() == "candidate"


def run(apply=False, only=None):
    plan = _collect_plan()
    if only == "A":
        plan = [p for p in plan if p[2] == "approved"]
    elif only == "C":
        plan = [p for p in plan if p[2] == "rejected"]

    print("===== candidate 复核（%s）=====" % ("执行" if apply else "dry-run 预览"))
    done = skipped = failed = 0
    for layer, key, target, label in plan:
        if not _is_candidate(layer, key):
            print("  · %s「%s」→ 跳过（非 candidate）" % (label, key))
            skipped += 1
            continue
        if not apply:
            print("  · %s「%s」→ %s" % (label, key, target))
            continue
        ok, msg = config_store.set_knowledge_status(layer, key, target, operator=OPERATOR)
        if ok:
            print("  ✓ %s「%s」→ %s（%s）" % (label, key, target, msg))
            done += 1
        else:
            print("  ✗ %s「%s」失败：%s" % (label, key, msg))
            failed += 1

    print("\n合计：%d 条待处理；执行 %d 条；跳过 %d 条；失败 %d 条" %
          (len(plan), done, skipped, failed))
    if not apply:
        print("（dry-run，未写库。加 --apply A / --apply C 才执行）")


if __name__ == "__main__":
    args = sys.argv[1:]
    apply = False
    only = None
    if "--apply" in args:
        i = args.index("--apply")
        if i + 1 < len(args) and args[i + 1] in ("A", "C"):
            apply = True
            only = args[i + 1]
        else:
            # ★ 安全护栏：--apply 必须显式带 A 或 C，缺省一律不执行（防误操作）
            print("✗ --apply 必须带参数 A 或 C（如 --apply A / --apply C）")
            print("  未执行任何操作，保持 dry-run 安全默认。")
            sys.exit(2)
    run(apply=apply, only=only)
