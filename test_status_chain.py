# -*- coding: utf-8 -*-
"""
test_status_chain.py —— 状态链四徽章（已生成 · 已校验 · 需求已核 · 已执行）与业务回填

★ 为什么需要这个测试文件？
  这一层的产出是【用户看到的结论】。它错了后果和算错数一样严重，甚至更糟：
  算错数用户可能核对发现；而界面把"执行成功"画成满屏绿色，
  用户会以为"需求也核过了、业务也确认了" —— 这正是这次改造要消灭的状态。

  但是这一层几乎全是"展示逻辑"，没有返回值可以断言，最容易出的问题是：
    ① 三态被写回两态（"待审核"被显示成"业务未通过"）；
    ② "未审"被画成"不通过"（用户跑去查一个不存在的问题）；
    ③ 前端少接一个 DOM 容器 / 少调一个渲染函数（点了没反应，且不报错）；
    ④ 有人把响应体里的 status_chain 字段删了（徽章静默变空）。

  所以这里测两类东西：
    · 后端还原语义（可执行的断言，覆盖 ①②）
    · 前端接线（静态文本扫描，覆盖 ③④）

运行：
    .venv/bin/python test_status_chain.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config_store as CS          # noqa: E402  ← 状态链还原逻辑就在里面

FE = os.path.join(HERE, "frontend")
PASSED, FAILED = [], []


def check(label, got, expected):
    """断言"实际 == 期望"。"""
    ok = (got == expected)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望: {expected}")
        print(f"       实际: {got}")
    return ok


def check_true(label, cond, detail=""):
    """断言"条件为真"，失败时打印实际值。"""
    ok = bool(cond)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok and detail != "":
        print(f"       实际: {detail}")
    return ok


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _chain(sql="SELECT 1", ok=1, req="", review="none"):
    """造一条"落库记录"，跑还原函数（不碰真实库）。"""
    return CS._chain_from_record({
        "sql": sql, "ok": ok, "req_verdict": req, "review_status": review,
    })


# ═══════════════════════════════════════════════════════════
# ① 业务已核必须是三态：True / False / None
#   ★ 这是本次最容易写错的一处（第一版就写错了）：`rs == "approved"` 会把
#     pending 算成 False，界面上变成"业务未通过" —— 把"还没人看"说成"被否了"。
#     审核人被冤枉，用户会以为自己的数已经出过问题。
# ═══════════════════════════════════════════════════════════
def test_business_verified_three_states():
    print("\n【①】业务已核：未提交/待审=None，通过=True，驳回=False")
    check("① 未提交审核 → None", _chain(review="none")["business_verified"], None)
    check("① 待审核 → None（不是 False）", _chain(review="pending")["business_verified"], None)
    check("① 审核通过 → True", _chain(review="approved")["business_verified"], True)
    check("① 审核驳回 → False", _chain(review="rejected")["business_verified"], False)


# ═══════════════════════════════════════════════════════════
# ② 四步的"推不出来"必须是 None，不能猜
#   ★ 落库只有 ok/sql/req_verdict 三列。gate_passed 与 executed 在失败记录上
#     无法区分"闸门拦的"还是"执行报错的"，此时给 False 就是在编造结论。
# ═══════════════════════════════════════════════════════════
def test_partial_inference():
    print("\n【②】历史记录还原：能推的推，推不出来的给 None（不猜）")
    c = _chain(sql="SELECT 1", ok=1)
    check("② 成功记录 → generated=True", c["generated"], True)
    check("② 成功记录 → gate_passed=True（执行成功⇒闸门必过）", c["gate_passed"], True)
    check("② 成功记录 → executed=True", c["executed"], True)
    check("② 成功记录 → exec_ok=True", c["exec_ok"], True)

    c2 = _chain(sql="SELECT 1", ok=0)
    check("② 失败记录 → exec_ok=False", c2["exec_ok"], False)
    check("② 失败记录 → gate_passed=None（分不清闸门拦的还是执行错）", c2["gate_passed"], None)
    check("② 失败记录 → executed=None", c2["executed"], None)

    c3 = _chain(sql="", ok=0)
    check("② 没有 SQL → generated=False", c3["generated"], False)
    check("② 没有 SQL → exec_ok=False", c3["exec_ok"], False)

    check("② 标记 partial=True（前端据此说明「哪些没留存」）", c3["partial"], True)
    check("② result_sane 未落库 → None", c3["result_sane"], None)


# ═══════════════════════════════════════════════════════════
# ③ 需求审核结论的默认值
#   ★ 老记录没有 req_verdict 列（P1 才加的）。默认必须是 "skipped"（没审），
#     绝不能默认 "pass" —— 那等于替没有人做过的审核签字。
# ═══════════════════════════════════════════════════════════
def test_requirement_verdict_default():
    print("\n【③】需求审核结论：老记录没有该列 → skipped（不是 pass）")
    check("③ req_verdict 为空 → skipped", _chain(req="")["requirement_verdict"], "skipped")
    check("③ req_verdict 为 None → skipped", _chain(req=None)["requirement_verdict"], "skipped")
    check("③ 原样保留 fail", _chain(req="fail")["requirement_verdict"], "fail")
    check("③ 原样保留 unsure", _chain(req="unsure")["requirement_verdict"], "unsure")


# ═══════════════════════════════════════════════════════════
# ④ 记录转字典这一步必须带上状态链（所有消费者统一拿到，不允许各页各拼）
# ═══════════════════════════════════════════════════════════
def test_record_dict_has_chain():
    print("\n【④】_record_row_to_dict 必须产出 status_chain")
    cols = ["id", "sql", "ok", "req_verdict", "review_status", "tables", "metrics",
            "dicts", "columns", "attempts", "remind_count"]
    row = [7, "SELECT 1", 1, "pass", "approved", "[]", "[]", "[]", "[]", 1, 0]
    d = CS._record_row_to_dict(cols, row)
    check_true("④ 字典里有 status_chain", isinstance(d.get("status_chain"), dict), d.keys())
    check("④ 链里四步齐全",
          sorted(k for k in d["status_chain"] if k in
                 ("generated", "gate_passed", "requirement_verdict", "executed")),
          ["executed", "gate_passed", "generated", "requirement_verdict"])
    check("④ 审核通过的记录 → business_verified=True",
          d["status_chain"]["business_verified"], True)
    check("④ partial 标记存在（历史回看要说明没留存的部分）",
          d["status_chain"].get("partial"), True)


# ═══════════════════════════════════════════════════════════
# ⑤ 前端接线：缺一个就是"点了没反应"或"徽章永远空"
# ═══════════════════════════════════════════════════════════
def test_frontend_wiring():
    print("\n【⑤】前端接线：DOM 容器 / 渲染函数 / 调用点 / 样式 一个都不能少")
    index = read(os.path.join(FE, "index.html"))
    ask = read(os.path.join(FE, "static", "js", "ask.js"))
    hist = read(os.path.join(FE, "static", "js", "history.js"))
    css = read(os.path.join(FE, "static", "app.css"))

    check_true("⑤ index.html 有 #chainBar 容器", 'id="chainBar"' in index)
    check_true("⑤ index.html 有 #reqClarifyBox 容器", 'id="reqClarifyBox"' in index)
    check_true("⑤ ask.js 定义 renderStatusChain",
               re.search(r"function\s+renderStatusChain\s*\(", ask) is not None)
    check_true("⑤ ask.js 定义 renderReqClarify",
               re.search(r"function\s+renderReqClarify\s*\(", ask) is not None)
    check_true("⑤ ask.js 定义 refreshBizChip（审核状态变化时同步徽章）",
               re.search(r"function\s+refreshBizChip\s*\(", ask) is not None)
    check_true("⑤ renderAnswer 里调用了 renderStatusChain",
               "renderStatusChain(r)" in ask)
    check_true("⑤ renderAnswer 里调用了 renderReqClarify",
               "renderReqClarify(r)" in ask)
    check_true("⑤ 历史回看也画状态链（renderHistoryDetail 调用）",
               "renderStatusChain(rec)" in hist)
    check_true("⑤ 审核状态变化时刷新业务徽章（renderReviewState 调用）",
               "refreshBizChip()" in hist)
    for cls in (".chain-step.ok", ".chain-step.bad", ".chain-step.warn",
                ".chain-step.skip", ".chain-step.unknown"):
        check_true(f"⑤ 样式 {cls} 存在（五态必须能分辨）", cls in css)


# ═══════════════════════════════════════════════════════════
# ⑥ 语义不变量：skip（没审）与 bad（不通过）必须是两种样子
#   ★ 这条最容易在改样式时被抹掉：把 skip 也画成红色，用户就会去查一个不存在的问题。
# ═══════════════════════════════════════════════════════════
def test_skip_not_rendered_as_bad():
    print("\n【⑥】「未审」不得画成「不通过」：skip 与 bad 是两种状态")
    ask = read(os.path.join(FE, "static", "js", "ask.js"))
    m = re.search(r"const REQ_VERDICT_VIEW\s*=\s*\{(.*?)\};", ask, re.S)
    check_true("⑥ 找到 REQ_VERDICT_VIEW 定义", m is not None)
    body = m.group(1) if m else ""
    check_true("⑥ skipped 用的是 skip 而不是 bad",
               re.search(r"skipped:\s*\[\s*[\"']skip[\"']", body) is not None, body[:200])
    check_true("⑥ fail 用的是 bad", re.search(r"fail:\s*\[\s*[\"']bad[\"']", body) is not None)
    check_true("⑥ unsure 用的是 warn", re.search(r"unsure:\s*\[\s*[\"']warn[\"']", body) is not None)
    check_true("⑥ pass 用的是 ok", re.search(r"pass:\s*\[\s*[\"']ok[\"']", body) is not None)
    # ★ 执行步骤的三态：false（确实没执行）不能画成 unknown（无从判断）
    check_true("⑥ executed=false 用 skip、null 才用 unknown",
               'c.executed === false ? "skip"' in ask, ask[:0])


# ═══════════════════════════════════════════════════════════
# ⑦ 响应体契约：字段被删 → 徽章静默变空，且没有任何报错
# ═══════════════════════════════════════════════════════════
def test_payload_contract():
    print("\n【⑦】/api/ask 响应体仍带着状态链三件套")
    src = read(os.path.join(HERE, "server.py"))
    for key in ('"status_chain"', '"req_review"', '"needs_clarification"'):
        check_true(f"⑦ _build_ask_payload 含 {key}", key in src)


def main():
    for fn in (test_business_verified_three_states, test_partial_inference,
               test_requirement_verdict_default, test_record_dict_has_chain,
               test_frontend_wiring, test_skip_not_rendered_as_bad,
               test_payload_contract):
        try:
            fn()
        except Exception as e:
            import traceback
            FAILED.append(f"{fn.__name__} 异常：{e}")
            print(f"  ❌ {fn.__name__} 抛异常：{e}")
            traceback.print_exc()

    print("\n" + "=" * 60)
    if FAILED:
        print(f"❌ 失败 {len(FAILED)} 项（通过 {len(PASSED)} 项）")
        for f in FAILED:
            print("  ❌", f)
        return 1
    print(f"🎉 全部通过（{len(PASSED)} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
