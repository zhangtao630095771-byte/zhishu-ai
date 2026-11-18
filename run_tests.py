# -*- coding: utf-8 -*-
"""
run_tests.py —— 分层测试运行器（代码审查报告 P1-3）

★ 为什么要把测试分层？
  审查报告指出：有些测试失败是【环境所致】而非代码缺陷 ——
  比如"能扫到 stale 条目"这条用例，本身就要求真实库里当前存在至少一条 stale 数据。
  把"纯逻辑单测"和"依赖真实库的集成测试"混在一起报，会让人误判代码质量。
  所以这里按【外部依赖】分三层，分别运行、分别汇总：

    unit        纯逻辑，无外部依赖（可随时跑、可在 CI 跑）
    config      需本地配置库 agent_config.db（本机可跑）
    integration 需真实 ClickHouse（未连库时不应据其失败判定代码缺陷）

运行：
    .venv/bin/python run_tests.py           # 跑全部
    .venv/bin/python run_tests.py unit      # 只跑纯逻辑单测
"""
import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# 测试文件 → 依赖层级
REGISTRY = [
    ("test_imports.py",          "unit"),
    ("test_pipeline.py",         "unit"),
    ("test_prompt_constraints.py", "unit"),
    ("test_gate.py",             "unit"),
    ("test_join_suggest.py",     "unit"),
    ("test_guard.py",            "unit"),
    ("test_labels.py",           "unit"),
    ("test_retrieve.py",         "unit"),
    ("test_permission.py",       "unit"),
    ("test_join_cardinality.py", "unit"),
    ("test_join_nulls.py",       "unit"),       # ClickHouse LEFT/RIGHT JOIN 未匹配补值检查（join_use_nulls）
    ("test_audit_safety.py",     "unit"),
    ("test_result_check.py",     "unit"),
    ("test_result_cache.py",     "unit"),
    ("test_prompt_assembly.py",  "unit"),
    ("test_annotate_expected.py","unit"),
    ("test_golden_demo_dataset.py", "unit"),
    ("test_result_compare.py",   "unit"),      # P0-1 结果对比引擎（六类判定 + 差异定位，纯逻辑）
    ("test_pipeline_convergence.py", "unit"),  # 双流水线收敛：唯一主流程 + fail-closed + 退役不变量
    ("test_llm_errors.py",       "unit"),      # LLM 错误分类层：4xx 快速失败 / 超时可重试 / 非 LLM 不归类
    # ★ 增量修改 + 需求一致性审核（P0~P2 建的三个文件，之前漏登记 —— 等于 173 项断言
    #   一直没被 runner 跑过。纯逻辑的两个放 unit，用到临时配置库的那个放 config。）
    ("test_modify_query.py",     "unit"),
    ("test_requirement_review.py", "unit"),
    ("evaluation/test_calendar.py", "unit"),
    ("test_acl.py",              "config"),
    ("test_knowledge_governance.py", "config"),
    ("test_sql_experience.py",   "config"),   # 经验中心：纯逻辑 + 临时库状态机（不碰真实库）
    ("test_gate_req.py",         "config"),   # 需求审核接进 ask_v2 闸门循环（临时库 + 模型替身）
    ("test_status_chain.py",     "config"),   # 状态链四徽章：落库还原语义 + 前端接线
    ("test_frontend_structure.py", "config"),  # 前端结构：文件引用/语法（纯）+ 静态托管（需 app）
    ("test_clarification.py",    "config"),   # 多轮澄清：纯逻辑 + 临时库 + 端点（打桩不连 LLM/库）
    ("test_clarification_multimetric.py", "config"),  # 多轮澄清「多指标共存」+ 指标公式过滤作用域专项
    ("test_ambiguity.py",        "unit"),     # 智能澄清引擎（第二阶段：时间+实体歧义检测，纯逻辑）
    ("test_ossie_adapter.py",    "unit"),     # Apache Ossie 适配层：Schema 校验 / 双向转换 / 往返一致性（纯逻辑）
    ("test_golden_cases.py",     "config"),   # P0-1 标准问题集管理（临时库 + 现有闸门验证）
    ("test_semantic_center.py",  "config"),   # 语义中心（阶段五~七）：AI 建模 candidate / 不覆盖人工 / 治理准入
    ("test_gaps.py",             "integration"),
]
LEVEL_ZH = {"unit": "纯逻辑单测", "config": "需本地配置库", "integration": "需真实 ClickHouse"}
ORDER = ["unit", "config", "integration"]


def _summarize(out):
    """从测试输出里抓最后的结论行"""
    lines = [l for l in (out or "").strip().splitlines() if l.strip()]
    for l in reversed(lines):
        if any(k in l for k in ("全部通过", "全部可导入", "通过 ", "失败", "❌")):
            return l.strip()
    return lines[-1].strip() if lines else "(无输出)"


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else None
    results = []
    for path, level in REGISTRY:
        if want and level != want:
            continue
        full = os.path.join(HERE, path)
        if not os.path.exists(full):
            results.append((level, path, "SKIP", "文件不存在"))
            continue
        try:
            p = subprocess.run([sys.executable, full], cwd=HERE,
                               capture_output=True, text=True, timeout=1800)
            out = (p.stdout or "") + (p.stderr or "")
            # ★ 双保险：不能只看 returncode。
            #   本项目曾出现"测试打印了一堆 ❌ 失败项、但进程仍返回 0"的情况，
            #   上层 runner 于是把失败报成 ✅（信号失灵）。所以输出里出现【失败标记】也判 FAIL。
            #
            #   ⚠️ 但"❌"不能简单按"出现过就算失败"判 —— 有些 ❌ 不在失败语境：
            #     · test_guard.py「改进点专项展示」用「旧正则 ❌ / 新解析器 ✅」做对比演示，
            #       行中间的 ❌ 表示"旧实现算错了"，是**展示**而非失败；
            #     · test_guard.py 还会打印「sqlglot 可用：✅ 是 / ❌ 否」（环境提示）。
            #   若按"任意 ❌ ⇒ FAIL"，这些**已通过**的测试会被误报成 ❌（反向的信号失灵）。
            #   故这里把"失败标记"定义为：出现「失败项 / 条失败」字样，
            #   或某行**以 ❌ 开头**（本项目所有测试的失败明细行都是"   ❌ 用例名"这一形态）。
            _lines = out.splitlines()
            looks_failed = (
                ("失败项" in out)
                or ("条失败" in out)
                or any(l.strip().startswith("❌") for l in _lines)
            )
            status = "PASS" if (p.returncode == 0 and not looks_failed) else "FAIL"
            results.append((level, path, status, _summarize(out)))
        except subprocess.TimeoutExpired:
            results.append((level, path, "FAIL", "超时"))

    print()
    print("=" * 74)
    print("分层测试报告")
    print("=" * 74)
    any_fail = False
    for level in ORDER:
        rows = [r for r in results if r[0] == level]
        if not rows:
            continue
        print(f"\n── {LEVEL_ZH[level]}（{level}）──")
        for _lv, path, status, summary in rows:
            icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(status, "?")
            if status == "FAIL":
                any_fail = True
            print(f"  {icon} {path:<28} {summary}")
        if level == "integration":
            print("     ★ 该层失败需先确认是否连接了真实库，不应直接判定为代码缺陷")
    print()
    print("=" * 74)
    print("❌ 存在失败项" if any_fail else "🎉 unit/config 层全部通过（integration 层请连库后复核）")
    print("=" * 74)
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
