# -*- coding: utf-8 -*-
"""
test_annotate_expected.py —— 评测集标注器的纯逻辑测试

★ 不连库、不调 LLM，只验证"搬运口径"这件事本身是否可靠：
  写回会不会损坏文件、种子匹配会不会把不同问题配成同一条。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "evaluation"))
sys.path.insert(0, HERE)

import annotate_expected as A   # noqa: E402

_passed = 0
_failed = []


def ok(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name} {detail}")


def test_norm():
    ok(A.norm_question("  查一下 订单量 ") == "查一下 订单量", "压空白")
    ok(A.norm_question("Order") == "order", "压小写")
    ok(A.norm_question("不含退款") != A.norm_question("含退款"),
       "★ 归一化不能抹掉语义差异（不含退款 ≠ 含退款）")


def test_expected_result_from():
    er = A.expected_result_from(["a", "b"], [[1, 2], [3, 4]], precision=4)
    ok(er["columns"] == ["a", "b"], "columns 保留")
    ok(er["rows"] == [[1, 2], [3, 4]], "rows 保留")
    ok(er["precision"] == 4, "precision 保留")
    ok("rows" in er and "columns" in er, "结构满足 checks.check_result 的 dict 格式")


def test_write_case_roundtrip():
    """写回单行后，文件其余行必须原样保留、目标行正确更新"""
    d = tempfile.mkdtemp()
    fp = os.path.join(d, "golden.jsonl")
    lines = [
        {"id": "g001", "question": "查询订单量"},
        {"id": "g002", "question": "统计完单量"},
        {"id": "g003", "question": "查询完单率"},
    ]
    with open(fp, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n")

    data = A.load_cases(d)
    ok(len(data["entries"]) == 3, "加载 3 条")
    # 找到 g002，改它，写回
    e2 = next(e for e in data["entries"] if e["case"]["id"] == "g002")
    e2["case"]["expected_sql"] = "SELECT 1"
    A.write_case(e2, e2["case"])

    data2 = A.load_cases(d)
    cases = {e["case"]["id"]: e["case"] for e in data2["entries"]}
    ok(cases["g002"]["expected_sql"] == "SELECT 1", "目标行已更新")
    ok("expected_sql" not in cases["g001"] and "expected_sql" not in cases["g003"],
       "★ 其余行未被污染")
    ok(cases["g001"]["question"] == "查询订单量", "其余行内容原样")
    # 备份文件已生成
    baks = [x for x in os.listdir(d) if ".bak-" in x]
    ok(len(baks) == 1, "生成了备份文件")


def test_match_seed():
    """种子匹配：只按问题完全相同匹配；已有 expected_sql 的条目不重复种"""
    entries = [
        {"file": "x", "line": 0, "case": {"id": "g001", "question": "查询订单量"}},
        {"file": "x", "line": 1, "case": {"id": "g002", "question": "查询订单量",
                                          "expected_sql": "SELECT 99"}},
        {"file": "x", "line": 2, "case": {"id": "g003", "question": "查询退款量"}},
    ]
    exp = [
        {"question": "查询订单量", "sql_raw": "SELECT count(*) FROM dwd_tu_order_all",
         "case_no": "EXP-1", "verify_status": "reexec_ok"},
    ]
    m = A.match_seed(exp, entries)
    ok(len(m) == 1, "只有一个可种子的匹配", str(m))
    ok(m[0]["entry"]["case"]["id"] == "g001", "只匹配 g001")
    ok(m[0]["sql_raw"].startswith("SELECT count(*)"), "搬运的是黄金 SQL")
    # 完全不同的问题不匹配
    exp2 = [{"question": "查询司机数量", "sql_raw": "SELECT 1", "case_no": "E2",
             "verify_status": "reexec_ok"}]
    ok(len(A.match_seed(exp2, entries)) == 0, "问题不同 → 不匹配")
    # 空 SQL 不搬运
    exp3 = [{"question": "查询订单量", "sql_raw": "  ", "case_no": "E3",
             "verify_status": "reexec_ok"}]
    ok(len(A.match_seed(exp3, entries)) == 0, "★ 空 SQL 不作为种子")


def main():
    for fn in (test_norm, test_expected_result_from, test_write_case_roundtrip,
               test_match_seed):
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
