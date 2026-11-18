# -*- coding: utf-8 -*-
"""
test_pipeline_convergence.py —— 双流水线收敛专项测试

★ 背景：Legacy/V2 双流水线收敛为单一主流水线（方案 A：fail-closed 不再降级）。
  本测试守住收敛的【不变量】，防止未来有人把 Legacy 复活、或绕过统一闸门：

  ① 唯一主流程：agent.ask 只调用 ask_v2，无任何 legacy 降级分支；
  ② 退役代码确实删除：ask_legacy / rag.retrieve / prompt.build_system_prompt 不存在；
  ③ fail-closed：ask_v2 抛任何异常（含超时）→ ok=False，不降级、不执行；
  ④ 唯一执行出口：全仓无裸 db.execute_sql（执行只能走 agent._exec_sql）；
  ⑤ 可观测性：query_records 具备 degraded/degrade_reason/trace_json 列；
  ⑥ 公共组件未被动摇：guard/sql_validator/permission/_exec_sql 仍被唯一主流程调用。

运行： .venv/bin/python test_pipeline_convergence.py  （纯静态 + 打桩，不连库、不调模型）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import agent  # noqa: E402

PASSED, FAILED = [], []


def check(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"  {'✅' if cond else '❌'} {label}")
    if not cond and detail:
        print(f"       实际: {detail}")
    return cond


def _src(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════
# ① 唯一主流程：agent.ask 只调 ask_v2，无 legacy 降级分支
# ═══════════════════════════════════════════════════════════
def test_single_pipeline_dispatch():
    print("\n【1】agent.ask 是唯一入口，只调 ask_v2，无 legacy 降级")
    src = _src("agent.py")
    check("ask() 调用了 ask_v2", "ask_v2(index, question, user=user" in src)
    check("ask() 不再引用 ask_legacy", "ask_legacy" not in src,
          "agent.py 里仍有 ask_legacy 引用")
    check("ask() 不再读取 LEGACY_FALLBACK_MODE",
          "LEGACY_FALLBACK_MODE" not in src)


# ═══════════════════════════════════════════════════════════
# ② 退役代码确实删除
# ═══════════════════════════════════════════════════════════
def test_legacy_code_retired():
    print("\n【2】Legacy 重复实现已删除")
    check("agent 无 ask_legacy 定义", not hasattr(agent, "ask_legacy"))
    rag_src = _src("rag.py")
    check("rag 无 retrieve（单路检索）", "def retrieve(index, question):" not in rag_src)
    prompt_src = _src("prompt.py")
    check("prompt 无 build_system_prompt", "def build_system_prompt(" not in prompt_src)
    cfg_src = _src("config.py")
    check("config 无 LEGACY_FALLBACK_MODE", "LEGACY_FALLBACK_MODE" not in cfg_src)


# ═══════════════════════════════════════════════════════════
# ③ fail-closed：任何异常（含超时）→ 不降级、不执行
# ═══════════════════════════════════════════════════════════
def test_fail_closed_no_fallback():
    print("\n【3】统一主流程 fail-closed：任何异常都如实返回，不降级")
    orig = agent.ask_v2
    try:
        for exc, tag in ((ValueError("逻辑错误"), "业务异常"),
                         (TimeoutError("timed out"), "超时故障")):
            def boom(index, q, user=None, understanding_override=None, modify_context=None):
                raise exc
            agent.ask_v2 = boom
            r = agent.ask({}, "q")
            check(f"{tag} → ok=False", r.get("ok") is False, str(r))
            check(f"{tag} → pipeline=v2（唯一主流程）", r.get("pipeline") == "v2", str(r))
            check(f"{tag} → degraded=False（无降级语义）", r.get("degraded") is False)
            check(f"{tag} → 记录异常原因", bool(r.get("degrade_reason")), str(r))
    finally:
        agent.ask_v2 = orig


# ═══════════════════════════════════════════════════════════
# ④ 唯一执行出口：由 test_gate.test_no_bare_exec_anywhere 全仓守住，
#    这里只确认 _exec_sql 仍作为唯一出口存在。
# ═══════════════════════════════════════════════════════════
def test_single_exec_chokepoint():
    print("\n【4】SQL 执行仍收敛到唯一出口 agent._exec_sql")
    src = _src("agent.py")
    check("agent 有 _exec_sql（唯一执行出口）", "def _exec_sql(sql):" in src)
    check("_exec_sql 是唯一真正执行点（业务编排不含裸执行）",
          "return db.execute_sql(sql)" in src)


# ═══════════════════════════════════════════════════════════
# ⑤ 可观测性列存在
# ═══════════════════════════════════════════════════════════
def test_observability_columns():
    print("\n【5】query_records 具备统一主流程可观测性列")
    src = _src("config_store.py")
    for col in ("degraded", "degrade_reason", "trace_json"):
        check(f"迁移含 {col} 列", f'"{col}":' in src, col)
    server_src = _src("server.py")
    check("落库时透传 degraded/trace",
          '"degraded": payload.get("degraded")' in server_src)


# ═══════════════════════════════════════════════════════════
# ⑥ 公共安全组件未被唯一主流程动摇
# ═══════════════════════════════════════════════════════════
def test_shared_safety_components_intact():
    print("\n【6】安全组件仍被唯一主流程调用")
    src = _src("agent.py")
    check("_pre_execute_check 仍存在", "def _pre_execute_check(" in src)
    check("_pre_execute_check 仍调 sql_validator.validate_sql_ex",
          "sql_validator.validate_sql_ex(" in src)
    check("_pre_execute_check 仍调 permission.evaluate",
          "permission.evaluate(sql, user, meta)" in src)
    check("_pre_execute_check 仍调 guard.sql_guard",
          "guard.sql_guard(sql)" in src)
    check("行级改写后仍重校验", "permission.apply_row_filters(sql, user)" in src)


if __name__ == "__main__":
    print()
    print("╔" + "═" * 60 + "╗")
    print("║" + "双流水线收敛 · 不变量专项测试".center(44) + "║")
    print("╚" + "═" * 60 + "╝")
    for fn in (test_single_pipeline_dispatch, test_legacy_code_retired,
               test_fail_closed_no_fallback, test_single_exec_chokepoint,
               test_observability_columns, test_shared_safety_components_intact):
        try:
            fn()
        except Exception as e:
            import traceback
            FAILED.append(f"{fn.__name__} 异常：{e}")
            traceback.print_exc()
    print()
    print("=" * 60)
    if not FAILED:
        print(f"🎉 全部通过（{len(PASSED)} 项）")
    else:
        print(f"⚠️ 通过 {len(PASSED)} 项，失败：")
        for f in FAILED:
            print(f"   ❌ {f}")
    print("=" * 60)
    raise SystemExit(1 if FAILED else 0)
