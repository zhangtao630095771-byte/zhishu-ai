# -*- coding: utf-8 -*-
"""
evaluation/annotate_expected.py —— 评测集 ground truth 标注器

★ 为什么要有这个工具？
  架构梳理指出：94 条用例全部没有 expected_sql / expected_result，
  导致 result_accuracy（最终业务结果准确率）【永远算不出来】——
  这是"测得准"的最大缺口，也是所有优化都无法验证的根因。

  但"补 ground truth"不是写代码，是【人工确认业务口径】。这个工具把人工的部分
  降到最低：跑一遍模型、把 SQL 和结果摆到眼前，标注人只需回答"对 / 不对"，
  对的就回写 expected_sql（+ 可选的结果快照 expected_result）。

★ 两种来源：
  ① annotate         —— 用当前模型生成 SQL，人工确认后回写（适合"从零补"）
  ② seed-from-experience —— 把 SQL 经验中心里【已验证成功】的黄金案例，
                            按"问题归一化后完全相同"匹配到数据集，直接用它的 SQL
                            当 expected_sql（适合"已有沉淀"——实现经验→评测的闭环）

★ 诚实边界（重要）：
  - expected_result 是【结构快照】，不是"永久正确"的数字。数仓每天都在变，
    隔几天的 expected_result 可能就过时。所以它只适合在【评测当天】跑出、
    【当天】比对；长期看应当把"重跑 expected_sql 得到结果"做成评测的一部分。
  - 本工具绝不自动编造 expected_result：只有【真的执行成功】才会快照。
  - 自动 --accept 模式下标注人要【事后复核】，工具会把 source 标成 auto 以提醒。

用法：
    .venv/bin/python evaluation/annotate_expected.py status
    .venv/bin/python evaluation/annotate_expected.py annotate --dataset golden --limit 5
    .venv/bin/python evaluation/annotate_expected.py annotate --dataset golden --accept
    .venv/bin/python evaluation/annotate_expected.py seed-from-experience --dataset golden --dry-run
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ═══════════════════════════════════════════════════════════
# 纯逻辑（可单测，不碰 LLM / 数据库）
# ═══════════════════════════════════════════════════════════
def norm_question(q):
    """问题归一化：只压空白和大小写（与 sql_experience.norm_question 口径一致）

    ★ 为什么不做同义词/去停用词？
      匹配是用来"搬运已确认的口径"的，一旦归一化太激进，会把
      "不含退款"和"含退款"配成同一条，等于把错误口径写进评测集。
    """
    return " ".join((q or "").split()).lower().strip()


def load_cases(dataset_path):
    """加载数据集，并记录每条用例的【来源文件 + 行号】（写回需要）。

    返回 {"files": [path, ...], "entries": [{"file","line","case"}, ...]}
    """
    files, entries = [], []
    if os.path.isdir(dataset_path):
        for fn in sorted(os.listdir(dataset_path)):
            if fn.endswith((".json", ".jsonl")):
                files.append(os.path.join(dataset_path, fn))
    else:
        files = [dataset_path]

    for fp in files:
        with open(fp, encoding="utf-8") as f:
            text = f.read().strip()
        lines = text.splitlines()
        if not lines:
            continue
        if text.startswith("["):
            # 整个数组格式：当成一个整体，不支持逐行写回（很少见，给出明确报错）
            for c in json.loads(text):
                entries.append({"file": fp, "line": None, "case": c})
        else:
            for i, ln in enumerate(lines):
                ln = ln.strip()
                if not ln or ln.startswith("//"):
                    continue
                entries.append({"file": fp, "line": i, "case": json.loads(ln)})
    return {"files": files, "entries": entries}


def expected_result_from(columns, rows, precision=4):
    """把执行结果压成 structured expected_result（checks.check_result 认得这个格式）"""
    return {"columns": list(columns or []), "rows": [list(r) for r in (rows or [])],
            "precision": precision}


def write_case(entry, case):
    """把改好的 case 写回它所在文件的那一行（先备份，再原子替换）"""
    fp = entry["file"]
    line = entry["line"]
    new_json = json.dumps(case, ensure_ascii=False)

    with open(fp, encoding="utf-8") as f:
        text = f.read()
    lines = text.splitlines()

    if line is not None and 0 <= line < len(lines):
        lines[line] = new_json
        new_text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    else:
        # 数组格式（line 为 None）：不逐行，重写整个文件
        new_text = text  # 不支持，交给上层判断
        raise RuntimeError(f"数组格式暂不支持写回：{fp}")

    # 备份 + 原子写
    bak = fp + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(fp, bak)
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
    os.replace(tmp, fp)
    return bak


def match_seed(exp_cases, dataset_entries):
    """把已验证的黄金案例匹配到数据集（按问题归一化后【完全相同】）。

    返回 [{"entry", "sql_raw", "case_no", "verify_status"}, ...]
    不返回的：问题匹配不上，或该数据集条目已有 expected_sql。
    """
    by_q = {}
    for e in dataset_entries:
        c = e["case"]
        if c.get("expected_sql"):
            continue
        by_q.setdefault(norm_question(c.get("question")), []).append(e)

    out = []
    for x in exp_cases:
        q = norm_question(x.get("question"))
        hits = by_q.get(q) or []
        if not hits:
            continue
        sql = (x.get("sql_raw") or "").strip()
        if not sql:
            continue
        for e in hits:
            out.append({"entry": e, "sql_raw": sql,
                        "case_no": x.get("case_no"), "verify_status": x.get("verify_status")})
    return out


# ═══════════════════════════════════════════════════════════
# 命令实现
# ═══════════════════════════════════════════════════════════
def _dataset_arg(a):
    d = a.dataset
    if not os.path.isabs(d):
        d = os.path.join(ROOT, d)
    if not os.path.exists(d):
        print(f"❌ 数据集不存在：{d}")
        sys.exit(2)
    return d


def _status_counts(entries):
    has_sql = sum(1 for e in entries if e["case"].get("expected_sql"))
    has_res = sum(1 for e in entries if e["case"].get("expected_result") is not None)
    return len(entries), has_sql, has_res


def cmd_status(a):
    d = _dataset_arg(a)
    data = load_cases(d)
    total, s, r = _status_counts(data["entries"])
    print(f"数据集：{os.path.relpath(d, ROOT)}")
    print(f"  用例总数       {total}")
    print(f"  有 expected_sql    {s}")
    print(f"  有 expected_result {r}")
    print(f"  ★ 缺 expected_result {total - r}（这些用例的 result_accuracy 都是「暂不可测」）")
    return 0


def cmd_seed(a):
    d = _dataset_arg(a)
    data = load_cases(d)

    import sql_experience
    res = sql_experience.list_cases(kind="success", status="active", limit=10000)
    # ★ 只搬「已验证成功」的案例；verify_status 是单值过滤，IN 语义在 Python 侧做
    verified = {"reexec_ok", "manual_confirmed", "result_verified"}
    exp_cases = [x for x in (res.get("items") or []) if x.get("verify_status") in verified]

    matched = match_seed(exp_cases, data["entries"])
    print(f"经验中心已验证成功案例：{len(exp_cases)} 条")
    print(f"问题完全匹配的数据集条目：{len(matched)} 条")

    if not matched:
        print("没有可种子的匹配（经验案例与数据集问题字面差异过大，属正常）")
        return 0

    for m in matched:
        c = m["entry"]["case"]
        c["expected_sql"] = m["sql_raw"]
        c["expected_sql_source"] = f"golden-seed:{m['case_no']}({m['verify_status']})"
        print(f"  ✅ {c.get('id')} ← {m['case_no']}（{m['verify_status']}）")

    if a.dry_run:
        print("（--dry-run：未写回）")
        return 0
    backs = [write_case(m["entry"], m["entry"]["case"]) for m in matched]
    print(f"已写回 {len(matched)} 条，备份：{backs[0]} 等")
    return 0


def cmd_annotate(a):
    d = _dataset_arg(a)
    data = load_cases(d)
    entries = [e for e in data["entries"] if not e["case"].get("expected_sql")]

    if a.limit:
        entries = entries[:a.limit]

    print(f"待标注：{len(entries)} 条")
    if not entries:
        return 0

    # 延迟导入：status / seed 不需要拉起 LLM 与 ClickHouse
    import agent
    import rag

    idx = None
    accepted = 0
    for e in entries:
        c = e["case"]
        q = c.get("question") or ""
        print("\n" + "=" * 70)
        print(f"用例 {c.get('id')}：{q}")
        if c.get("expected_tables"):
            print(f"  期望表：{c.get('expected_tables')}")
        if c.get("expected_metrics"):
            print(f"  期望指标：{c.get('expected_metrics')}")
        if c.get("expected_sql_contains"):
            print(f"  应含谓词：{c.get('expected_sql_contains')}")

        try:
            if idx is None:
                idx = rag.build_index()
            r = agent.ask(idx, q)
        except Exception as ex:
            print(f"  ⚠️ 执行失败，跳过：{ex}")
            if not a.accept:
                input("  回车继续…")
            continue

        sql = (r.get("sql") or "").strip()
        print(f"  生成 SQL：\n    {sql.replace(chr(10), chr(10) + '    ')}")
        print(f"  ok={r.get('ok')} rows={len(r.get('rows') or [])} "
              f"attempts={r.get('attempts')} pipeline={r.get('pipeline')}")

        if not r.get("ok"):
            print("  （模型未取到数，不写 expected_result；可仍确认 SQL 作为 expected_sql）")

        verdict = "y" if a.accept else input("  确认写入？[y]=是 [e]=手改SQL [s]=跳过：").strip().lower()

        if verdict == "s":
            continue
        if verdict == "e":
            import readline  # noqa: F401  （提升粘贴长 SQL 的体验）
            new_sql = input("  粘贴正确的 SQL：").strip()
            if not new_sql:
                print("  （空输入，跳过）")
                continue
            sql = new_sql
            verdict = "y"
        if verdict != "y":
            continue

        c["expected_sql"] = sql
        c["expected_sql_source"] = "manual-confirmed" if not a.accept else "auto-accept(待复核)"
        # ★ 关键：只有【人工确认】(verdict=='y') 才快照 expected_result。
        #   --accept 是"模型自己生成 → 自己当答案"，若还快照结果做 expected_result，
        #   就是拿模型比对模型，result_accuracy 会恒等于 1 —— 循环论证，毫无意义。
        if not a.accept and r.get("ok") and r.get("rows") is not None:
            c["expected_result"] = expected_result_from(r.get("columns"), r.get("rows"))
            c["expected_result_source"] = "snapshot"

        if a.dry_run:
            print("  （--dry-run：未写回）")
            continue
        bak = write_case(e, c)
        accepted += 1
        print(f"  ✅ 已写回（备份 {os.path.basename(bak)}）")

    print(f"\n完成：本次标注 {accepted} 条")
    if a.accept and accepted:
        print("⚠️ --accept 模式自动写入，请在评测前人工复核 source 含 auto 的用例")
    return 0


def main():
    ap = argparse.ArgumentParser(description="评测集 ground truth 标注器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="查看 ground truth 覆盖情况")
    p.add_argument("--dataset", default="evaluation/datasets/golden")

    p = sub.add_parser("annotate", help="跑模型生成 SQL，人工确认后写回 expected_sql/result")
    p.add_argument("--dataset", default="evaluation/datasets/golden")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--accept", action="store_true", help="自动接受模型生成的 SQL（事后须复核）")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("seed-from-experience", help="用已验证黄金案例种子 expected_sql")
    p.add_argument("--dataset", default="evaluation/datasets/golden")
    p.add_argument("--dry-run", action="store_true")

    a = ap.parse_args()
    return {"status": cmd_status, "annotate": cmd_annotate,
            "seed-from-experience": cmd_seed}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
