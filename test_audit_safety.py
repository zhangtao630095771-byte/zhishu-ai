# -*- coding: utf-8 -*-
"""
test_audit_safety.py —— 审计 SQL 脱敏 + request_id 传播 专项测试（整改任务 7）

★ 背景（docs/audit/02-critical-issues.md S-01）：
  ① server._audit_permission 把 payload["sql"] 明文写入 permission_audit_logs.final_sql，
     可能留存用户输入的手机号/身份证等字面量（permission._safe_sql_repr 已存在但未被调用）。
  ② server.py:826 读取 request.state.request_id，但中间件从未设置 → 审计追踪链断裂。

覆盖：
  ① 中间件为每个请求生成非空且互不相同的 request_id（公开路径也不例外）
  ② 审计记录的 request_id 与请求一致
  ③ 审计 SQL 字面量脱敏：不含手机号/身份证明文，但保留 SQL 结构（SELECT/WHERE/?）
  ④ 脱敏只影响审计存储，不影响查询结果（查询结果脱敏由 permission.mask_rows 负责，另测）

运行： .venv/bin/python test_audit_safety.py   （不连真实库、不调模型、不启动服务端口）
"""
import asyncio
from types import SimpleNamespace

import config_store
import server

PASSED, FAILED = [], []


def check(label, got, expected):
    ok = (got == expected)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望: {expected}")
        print(f"       实际: {got}")
    return ok


def check_true(label, cond, detail=""):
    ok = bool(cond)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok and detail:
        print(f"       实际: {detail}")
    return ok


def _mk_request(path="/api/ping", token=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return SimpleNamespace(method="GET", url=SimpleNamespace(path=path),
                           headers=headers, state=SimpleNamespace())


async def _call_next(req):
    return "RESPONSE"


# ═══════════════════════════════════════════════════════════
# ① request_id 生成
# ═══════════════════════════════════════════════════════════
def test_middleware_sets_request_id():
    print("\n【1】中间件为每个请求生成非空 request_id")
    r1 = _mk_request()
    resp = asyncio.run(server._auth_middleware(r1, _call_next))
    check("公开路径正常放行", resp, "RESPONSE")
    rid1 = getattr(r1.state, "request_id", "")
    check_true("request_id 非空", bool(rid1), f"request_id={rid1!r}")
    r2 = _mk_request()
    asyncio.run(server._auth_middleware(r2, _call_next))
    rid2 = getattr(r2.state, "request_id", "")
    check_true("不同请求 request_id 互不相同", bool(rid2) and rid1 != rid2,
               f"{rid1!r} vs {rid2!r}")


# ═══════════════════════════════════════════════════════════
# ②③ 审计：request_id 传播 + SQL 字面量脱敏
# ═══════════════════════════════════════════════════════════
_CAPTURE = {}


def _fake_add_audit(rec):
    _CAPTURE.clear()
    _CAPTURE.update(rec)


def test_audit_request_id_and_sql_masked():
    print("\n【2】审计记录：request_id 传播 + SQL 字面量脱敏")
    orig = config_store.add_permission_audit
    config_store.add_permission_audit = _fake_add_audit
    try:
        request = _mk_request(path="/api/ask")
        request.state.user = {"username": "tester", "role": "user"}
        request.state.request_id = "req-test-abcdef"
        payload = {
            "question": "查询手机号 13800000001 的订单",
            "sql": ("SELECT order_id FROM dwd_order o JOIN dim_user_contact c "
                    "ON o.user_id = c.user_id WHERE c.phone = '13800000001' "
                    "AND c.id_card = '110101199001011234'"),
            "ok": True,
            "columns": ["order_id"],
        }
        result = {"perm_decision": {"requested_tables": ["dwd_order", "dim_user_contact"],
                                    "output_columns": ["order_id"],
                                    "deny_reasons": [], "mask_map": {}}}
        server._audit_permission(request, payload, result)
        rec = dict(_CAPTURE)
        if not rec:
            check_true("审计被写入", False, "add_permission_audit 未被调用")
            return
        check("审计 request_id 与请求一致", rec.get("request_id"), "req-test-abcdef")
        fs = str(rec.get("final_sql", ""))
        check_true("审计 SQL 不含手机号明文 13800000001", "13800000001" not in fs, fs)
        check_true("审计 SQL 不含身份证明文 110101199001011234", "110101199001011234" not in fs, fs)
        check_true("审计 SQL 保留结构诊断价值（含 select/where/join 与 ? 占位）",
                   ("select" in fs.lower() and "where" in fs.lower()
                    and "join" in fs.lower() and "?" in fs), fs)
    finally:
        config_store.add_permission_audit = orig


def test_masking_does_not_touch_result_rows():
    print("\n【3】SQL 字面量脱敏 ≠ 查询结果脱敏（二者独立）")
    import permission
    rows = [["13800000001"], ["13800000002"]]
    masked = permission.mask_rows(["phone"], [list(r) for r in rows], {"phone": "phone"})
    check("结果脱敏（mask_rows）仍按规则打码：138****0001",
          masked[0][0], "138****0001")
    # 字面量脱敏函数只处理 SQL 文本，不碰结果行
    sql_repr = permission._safe_sql_repr("SELECT * FROM t WHERE phone='13800000001'")
    check_true("_safe_sql_repr 只作用于 SQL 文本", "13800000001" not in sql_repr, sql_repr)


if __name__ == "__main__":
    print()
    print("╔" + "═" * 56 + "╗")
    print("║" + "审计脱敏 + request_id · 专项测试（整改任务 7）".center(36) + "║")
    print("╚" + "═" * 56 + "╝")
    test_middleware_sets_request_id()
    test_audit_request_id_and_sql_masked()
    test_masking_does_not_touch_result_rows()
    print()
    print("=" * 58)
    total = len(PASSED) + len(FAILED)
    if not FAILED:
        print(f"🎉 全部通过（{len(PASSED)}/{total}）")
    else:
        print(f"⚠️ 通过 {len(PASSED)}/{total}，失败项：")
        for f in FAILED:
            print(f"   ❌ {f}")
    print("=" * 58)
    raise SystemExit(1 if FAILED else 0)
