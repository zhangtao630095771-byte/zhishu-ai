# -*- coding: utf-8 -*-
"""
test_prompt_assembly.py —— Prompt 组装收敛的等价性测试

★ 为什么需要这个测试？
  agent.py 里原来有 4 处手工 `system_prompt = system_prompt + ...`，
  收敛成 _build_system_prompt() 之后，**只要顺序或分隔符变了，模型行为就会变**，
  而且不会报错、只会答错。所以必须证明：新函数拼出来的字符串
  与旧实现**逐字节相同**。

★ 怎么证明？
  在测试里把旧实现原样抄一遍（_legacy_assemble），
  对同一组打桩输入分别跑新旧两条路径，比较字符串相等。
  旧实现抄错的话，测试会失败 —— 这正是我们想要的保护。

运行：
    .venv/bin/python test_prompt_assembly.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import agent            # noqa: E402
import calendar_util    # noqa: E402
import config           # noqa: E402
import config_store     # noqa: E402
import prompt           # noqa: E402
import rag              # noqa: E402
import semantic_layer   # noqa: E402

_passed = 0
_failed = []


def ok(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name} {detail}")


class _Stub:
    """临时替换模块属性"""
    def __init__(self, mod, name, fn):
        self.mod, self.name = mod, name
        self.origin = getattr(mod, name, None)
        self.new = fn

    def __enter__(self):
        setattr(self.mod, self.name, self.new)
        return self

    def __exit__(self, *a):
        setattr(self.mod, self.name, self.origin)


def _legacy_assemble(sidx, ctx, resolved, question, exp_block=""):
    """★ 改造前 ask_v2 里的原样逻辑（逐行抄写，用作对照基准）"""
    role = config_store.get_prompt("role").format(dialect=config.SQL_DIALECT)
    time_context = calendar_util.build_time_context(question)
    if agent.needs_no_time_filter(question):
        time_context += ("\n【口径约束】本问题问的是配置/比例的搭配分布，"
                         "不是时间区间指标；除非用户明确要求，不要添加创建时间、"
                         "订单时间等任何时间过滤条件。")
    system_prompt = semantic_layer.build_prompt_parts(
        resolved, role, time_context=time_context)

    dict_names, dict_reason = rag.retrieve_dicts(sidx, question)
    dict_block = prompt._build_dict_block(
        sidx.get("dicts") or [], dict_names, question,
        tables=sidx.get("tables") or [], table_names=resolved.get("tables") or [])
    if dict_block:
        system_prompt = system_prompt + "\n\n# " + dict_block + "\n"

    hard_block = prompt.build_hard_constraints_block(
        sidx.get("tables") or [], resolved.get("tables") or [])
    if hard_block:
        system_prompt = system_prompt + "\n\n" + hard_block + "\n"

    if exp_block:
        system_prompt = system_prompt + "\n\n" + exp_block + "\n"
    return system_prompt, dict_names, dict_reason


def _run_case(label, sidx, resolved, question, exp_block, dict_names,
              dict_block, hard_block, base):
    """打桩 → 新旧两条路径各跑一次 → 比较"""
    with _Stub(config_store, "get_prompt", lambda k: "ROLE[{dialect}]"):
        with _Stub(calendar_util, "build_time_context", lambda q: "TIME_CTX"):
            with _Stub(semantic_layer, "build_prompt_parts",
                       lambda r, role, time_context="": base + "|" + role + "|" + time_context):
                with _Stub(rag, "retrieve_dicts", lambda i, q: (dict_names, "reason-stub")):
                    with _Stub(prompt, "_build_dict_block",
                               lambda *a, **kw: dict_block):
                        with _Stub(prompt, "build_hard_constraints_block",
                                   lambda *a, **kw: hard_block):
                            old, on, orr = _legacy_assemble(
                                sidx, None, resolved, question, exp_block)
                            new, nn, nr, parts = agent._build_system_prompt(
                                sidx, None, resolved, question, exp_block=exp_block)

    ok(old == new, f"{label}：新旧 prompt 逐字节相同",
       f"\n旧={old!r}\n新={new!r}" if old != new else "")
    ok(on == nn, f"{label}：dict_names 一致")
    ok(orr == nr, f"{label}：dict_reason 一致")
    return new, parts


def test_equivalence():
    print("【等价性】新 _build_system_prompt 与旧实现必须逐字节相同")
    sidx = {"dicts": [{"name": "d1"}], "tables": [{"name": "t1"}]}
    resolved = {"tables": ["t1"], "metrics": ["m1"]}

    # ① 全部段落都有
    _run_case("全段落", sidx, resolved, "查询代驾完单量",
              "EXP_BLOCK", ["d1"], "DICT_BLOCK", "HARD_BLOCK", "BASE")
    # ② 没有经验块
    _run_case("无经验", sidx, resolved, "查询代驾完单量",
              "", ["d1"], "DICT_BLOCK", "HARD_BLOCK", "BASE")
    # ③ 字典召回但拼不出块（接线断开场景）
    _run_case("字典块为空", sidx, resolved, "查询代驾完单量",
              "EXP_BLOCK", ["d1"], "", "HARD_BLOCK", "BASE")
    # ④ 完全没有字典
    _run_case("无字典", sidx, resolved, "查询代驾完单量",
              "EXP_BLOCK", [], "", "HARD_BLOCK", "BASE")
    # ⑤ 没有任何附加块
    _run_case("只有基础段", sidx, resolved, "查询代驾完单量",
              "", [], "", "", "BASE")


def test_order():
    """段落顺序 = 权威性顺序，必须能一眼验证"""
    print("\n【顺序】经验块必须在最后，硬约束在经验之前")
    sidx = {"dicts": [{"name": "d1"}], "tables": [{"name": "t1"}]}
    resolved = {"tables": ["t1"], "metrics": ["m1"]}
    with _Stub(config_store, "get_prompt", lambda k: "ROLE"):
        with _Stub(calendar_util, "build_time_context", lambda q: "TIME"):
            with _Stub(semantic_layer, "build_prompt_parts",
                       lambda r, role, time_context="":
                       f"BASE_PART|{role}|{time_context}"):
                with _Stub(rag, "retrieve_dicts", lambda i, q: (["d1"], "r")):
                    with _Stub(prompt, "_build_dict_block", lambda *a, **k: "DICT"):
                        with _Stub(prompt, "build_hard_constraints_block",
                                   lambda *a, **k: "HARD"):
                            text, _, _, parts = agent._build_system_prompt(
                                sidx, None, resolved, "q", exp_block="EXP")

    ok([p[0] for p in parts] == ["base(P0~P4)", "dicts", "hard_constraints", "experience"],
       "分段顺序正确", str([p[0] for p in parts]))
    ok(text.rstrip().endswith("EXP"), "★ 经验块在最后（结构上无法覆盖口径）")
    ok(text.index("HARD") < text.index("EXP"), "硬约束排在经验之前")
    ok(text.index("BASE_PART") < text.index("DICT") < text.index("HARD"),
       "P0~P4 → 字典 → 硬约束 → 经验")
    ok("BASE_PART|ROLE|TIME" in text, "角色与时间口径进入基础段")
    ok(parts[0][1].startswith("BASE_PART"), "parts 记录基础段原文")


def test_dump_off_by_default():
    print("\n【落盘】默认不写文件")
    ok(getattr(config, "PROMPT_DUMP", False) is False, "PROMPT_DUMP 默认关闭")
    # 关闭时调用不应抛错、也不应创建目录
    try:
        agent._dump_prompt("q", "text", [("base", "text")])
        ok(True, "关闭时 _dump_prompt 安全返回")
    except Exception as e:
        ok(False, "关闭时 _dump_prompt 安全返回", str(e))


def main():
    for fn in (test_equivalence, test_order, test_dump_off_by_default):
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
