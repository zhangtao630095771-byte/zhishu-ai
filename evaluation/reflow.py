# -*- coding: utf-8 -*-
"""
evaluation/reflow.py —— 线上错误回流入口（Error Reflow / Feedback → Dataset）

★ 作用：把线上真实反馈（config_store.feedbacks）转成【用例草稿】，
        进入 inbox（待确认区），人工补全期望值后 promote 到 golden / regression。
        这是"持续优化闭环"里最关键的一环 —— 没有回流，评测集半年后就会与真实业务脱节。

★ 三条铁律：
  ① 反馈 ≠ 正确答案：草稿只提供上下文与复现信息，**期望值必须人工确认**才能转正；
  ② 草稿区与正式测试集**物理分离**，防止污染 Golden（Test Set Leakage 防火墙）；
  ③ 每条用例保留来源（feedback_id）+ 时间，可追溯。

用法：
    # ① 从线上反馈生成草稿（默认取 pending，可用 --all 取全部）
    .venv/bin/python evaluation/reflow.py --collect

    # ② 查看草稿区
    .venv/bin/python evaluation/reflow.py --list

    # ③ 手工新增一条（默认进 inbox；加 --confirm 直接转正）
    .venv/bin/python evaluation/reflow.py --add "查询2026年9月大板完单量" \
        --metrics 大板完单量 --tables ods_tu_large_handcart_order_all --confirm

    # ④ 把草稿转正（转正前必须已填好期望字段，否则拒绝）
    .venv/bin/python evaluation/reflow.py --promote rf-7-20261008 --target golden

    # ⑤ 对 inbox 草稿跑一次（先红后绿：看当前系统到底错在哪）
    .venv/bin/python evaluation/reflow.py --run
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATASETS = os.path.join(HERE, "datasets")
INBOX_DIR = os.path.join(DATASETS, "inbox")
INBOX = os.path.join(INBOX_DIR, "inbox_v1.jsonl")

# reason → 复现/核对提示（帮助人工快速定位该看什么）
REASON_HINTS = {
    "wrong_filter": "检查过滤条件：是否用了无关字段去过滤",
    "wrong_enum":   "检查枚举值映射：如 status=6/40、is_service_staff 等",
    "wrong_metric": "核对指标口径：公式与分母是否符合 Metric Registry",
    "wrong_table":  "核对表选择与权限：是否用错表或依赖表不可见",
    "wrong_time":   "核对时间范围：注意系统用左闭右开 [start, end+1)",
    "missing_data": "确认是否缺表/字段/指标：可能需要先补知识库",
    "other":        "见 detail 补充说明",
}

# 转正时必须至少具备的期望字段（强制人工确认，防"反馈即答案"）
EXPECT_KEYS = ("expected_ok", "expected_abstain", "expected_tables",
               "expected_metrics", "expected_sql_contains", "expected_format")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _write_jsonl(path, items):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def _append_jsonl(path, item):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════
# ① 反馈 → 草稿
# ═══════════════════════════════════════════════════════════
def draft_from_feedback(fb):
    """把一条 feedback 变成用例草稿（不含期望值，期望值留给人工确认）"""
    fid = fb.get("id")
    q = (fb.get("question") or "").strip()
    reason = fb.get("reason") or "other"
    tries = fb.get("tables")
    mets = fb.get("metrics")
    def _j(x):
        if not x:
            return []
        try:
            return json.loads(x) if isinstance(x, str) else x
        except Exception:
            return []
    return {
        "id": f"rf-{fid}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}",
        "source": "feedback",
        "feedback_id": fid,
        "status": "draft",                       # draft → promoted / dropped
        "question": q,
        "category": "reflow",
        "reason": reason,
        "reason_hint": REASON_HINTS.get(reason, REASON_HINTS["other"]),
        "detail": (fb.get("detail") or "").strip(),
        # 复现现场（当时的错误表现，供人工判断）
        "observed": {
            "sql": fb.get("sql"),
            "tables": _j(tries),
            "metrics": _j(mets),
            "exec_ok": fb.get("exec_ok"),
            "result": fb.get("result"),
        },
        # ★ 期望值留空，必须人工填写（这是防"反馈即答案"的关键）
        "needs_review": ["expected_ok", "expected_tables", "expected_metrics",
                         "expected_sql_contains", "time_range"],
        "created_at": _now(),
    }


def collect(status="pending", include_existing=False):
    """从 config_store.feedbacks 生成草稿，写入 inbox"""
    import config_store as cs
    items = cs.get_feedbacks(status=status or None)
    inbox = _read_jsonl(INBOX)
    seen_fb = {d.get("feedback_id") for d in inbox}
    seen_q = {(d.get("question") or "").strip() for d in inbox}   # ★ inbox 内去重
    # 也避免与正式集重复
    existing_q = set()
    for sub in ("golden", "regression"):
        for d in _read_jsonl(os.path.join(DATASETS, sub, f"{sub}_v1.jsonl")):
            existing_q.add((d.get("question") or "").strip())

    added, skipped = [], []
    for fb in items:
        fid = fb.get("id")
        q = (fb.get("question") or "").strip()
        if not q:
            skipped.append((fid, "空问题"))
            continue
        if fid in seen_fb:
            skipped.append((fid, "已在 inbox"))
            continue
        if q in seen_q:
            skipped.append((fid, "inbox 内已有同问题草稿（去重）"))
            continue
        if q in existing_q:
            skipped.append((fid, "与正式集重复"))
            continue
        d = draft_from_feedback(fb)
        inbox.append(d)
        seen_q.add(q)
        added.append(d)

    _write_jsonl(INBOX, inbox)
    return {"added": added, "skipped": skipped, "inbox": inbox}


# ═══════════════════════════════════════════════════════════
# ② 草稿转正（人工确认后）
# ═══════════════════════════════════════════════════════════
def promote(draft_id, target="golden"):
    """把 inbox 草稿转成正式用例。转正前必须已填期望字段（强制人工确认）。"""
    inbox = _read_jsonl(INBOX)
    draft = next((d for d in inbox if d.get("id") == draft_id), None)
    if not draft:
        return {"ok": False, "msg": f"inbox 中找不到草稿 {draft_id}"}
    if draft.get("status") != "draft":
        return {"ok": False, "msg": f"草稿状态为 {draft.get('status')}，不可转正"}

    has_expect = any(k in draft for k in EXPECT_KEYS)
    if not has_expect:
        return {"ok": False,
                "msg": ("转正被拒绝：草稿尚未填写任何期望字段"
                        f"（需至少一个：{', '.join(EXPECT_KEYS)}）。"
                        "请先编辑 inbox_v1.jsonl 补全期望值 —— 反馈本身不是正确答案。")}

    case = {
        "id": draft.get("id"),
        "name": draft.get("name") or f"回流-{draft.get('reason')}",
        "category": "reflow",
        "question": draft["question"],
        "source": "feedback",
        "feedback_id": draft.get("feedback_id"),
        "notes": f"来源反馈#{draft.get('feedback_id')}｜{draft.get('reason_hint')}｜{draft.get('detail','')}".strip("｜"),
    }
    for k in ("expected_ok", "expected_abstain", "expected_tables", "expected_metrics",
              "expected_sql_contains", "expected_sql_not_contains", "expected_format",
              "forbidden_metrics", "time_range", "time_sensitive", "difficulty"):
        if k in draft:
            case[k] = draft[k]

    target_path = os.path.join(DATASETS, target, f"{target}_v1.jsonl")
    _append_jsonl(target_path, case)
    draft["status"] = "promoted"
    draft["promoted_to"] = target
    draft["promoted_at"] = _now()
    _write_jsonl(INBOX, inbox)
    return {"ok": True, "msg": f"已转正到 {target}：{target_path}", "case": case}


# ═══════════════════════════════════════════════════════════
# ③ 手工新增
# ═══════════════════════════════════════════════════════════
def add_manual(question, metrics=None, tables=None, abstain=None,
               sql_contains=None, confirm=False, category="manual"):
    cid = f"m-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    case = {"id": cid, "name": f"手工-{question[:18]}", "category": category,
            "question": question, "source": "manual", "created_at": _now()}
    if metrics:
        case["expected_metrics"] = [s.strip() for s in metrics.split(",") if s.strip()]
    if tables:
        case["expected_tables"] = [s.strip() for s in tables.split(",") if s.strip()]
    if sql_contains:
        case["expected_sql_contains"] = [s.strip() for s in sql_contains.split(",") if s.strip()]
    if abstain is not None:
        case["expected_abstain"] = bool(abstain)
        case["expected_ok"] = not bool(abstain)
    if confirm:
        p = os.path.join(DATASETS, "golden", "golden_v1.jsonl")
        _append_jsonl(p, case)
        return {"ok": True, "msg": f"已直接写入 golden：{p}", "case": case}
    _append_jsonl(INBOX, case)
    return {"ok": True, "msg": f"已写入 inbox（待确认）：{INBOX}", "case": case}


# ═══════════════════════════════════════════════════════════
# ④ 对 inbox 跑一次（先红后绿）
# ═══════════════════════════════════════════════════════════
def run_inbox():
    from evaluation import runner
    res = runner.run(INBOX, quiet=False)
    return res


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="线上错误回流入口")
    ap.add_argument("--collect", action="store_true", help="从线上反馈生成草稿")
    ap.add_argument("--all", action="store_true", help="collect 时取全部状态（默认仅 pending）")
    ap.add_argument("--list", action="store_true", help="列出 inbox 草稿")
    ap.add_argument("--promote", metavar="DRAFT_ID", help="把草稿转正")
    ap.add_argument("--target", default="golden", choices=["golden", "regression"])
    ap.add_argument("--add", metavar="QUESTION", help="手工新增用例")
    ap.add_argument("--metrics", default=None)
    ap.add_argument("--tables", default=None)
    ap.add_argument("--sql-contains", default=None)
    ap.add_argument("--abstain", action="store_true")
    ap.add_argument("--confirm", action="store_true", help="手工新增时直接转正")
    ap.add_argument("--run", action="store_true", help="对 inbox 跑一次评测")
    a = ap.parse_args()

    if a.collect:
        r = collect(status=None if a.all else "pending")
        print(f"新增草稿 {len(r['added'])} 条，跳过 {len(r['skipped'])} 条")
        for d in r["added"]:
            print(f"  + [{d['id']}] {d['question']}  (原因: {d['reason']})")
        for fid, why in r["skipped"]:
            print(f"  - feedback#{fid} 跳过：{why}")
        print(f"\n草稿区：{INBOX}\n请编辑补全期望字段后 promote。")

    if a.list:
        inbox = _read_jsonl(INBOX)
        print(f"inbox 共 {len(inbox)} 条：")
        for d in inbox:
            print(f"  [{d.get('id')}] status={d.get('status')} | {d.get('question')} | 待补={d.get('needs_review')}")

    if a.promote:
        r = promote(a.promote, a.target)
        print(("✅ " if r["ok"] else "❌ ") + r["msg"])

    if a.add:
        r = add_manual(a.add, a.metrics, a.tables, a.abstain if a.abstain else None,
                       a.sql_contains, a.confirm)
        print(("✅ " if r["ok"] else "❌ ") + r["msg"])

    if a.run:
        res = run_inbox()
        print("\n===== inbox 评测 =====")
        for k, v in res["metrics"].items():
            print(f"  {k} = {v}")


if __name__ == "__main__":
    main()
