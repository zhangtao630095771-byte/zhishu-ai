# -*- coding: utf-8 -*-
"""
test_retrieve.py —— 检索层专项测试（重点：数据字典召回）

★ 为什么字典召回要专门做测试？
  因为字典召回失败【没有任何症状】：
    不报错、不告警，模型只是"看不到那些编码含义"，
    然后自己编一个看起来合理的条件。
  实测真实发生过（2026-09-17）：
    问「车拖车的完单量」→ 字典没召回 → 模型编出
        WHERE company_id = 'ctc'   → 975,023
    而真值是 1,419,426 —— SQL 能跑、不报错、数字全错。
  这是最危险的一类错误（静默算错），所以必须用测试钉死。

★ 本测试用【假模型】，不加载真 embedding 模型、不联网，几秒跑完。
  假模型的查询向量与候选向量【正交】（余弦相似度恒为 0），
  于是向量路径必然不命中 —— 这样测出来的命中一定来自"关键词"路径，
  正好对着我们要钉死的那条逻辑。

运行： .venv/bin/python test_retrieve.py
"""
import numpy as np

import config
import rag


# ═══════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════
PASSED = []                                  # 记录通过项
FAILED = []                                  # 记录失败项


def check(label, got, expected):
    """断言一个结果，记录下来"""
    ok = (got == expected)
    (PASSED if ok else FAILED).append(label)
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望: {expected}")
        print(f"       实际: {got}")


def check_true(label, cond):
    """断言一个布尔条件"""
    check(label, bool(cond), True)


# ═══════════════════════════════════════════════════════════
# 假模型与假索引
# ═══════════════════════════════════════════════════════════
QVEC = np.array([1.0, 0.0, 0.0, 0.0])        # 查询向量
DVEC = np.array([0.0, 1.0, 0.0, 0.0])        # 候选向量（与查询向量正交 → 相似度 0）


class FakeModel:
    """
    假 embedding 模型

    ★ 两个作用：
      ① 返回与候选向量正交的单位向量 → 向量路径必然拿 0 分，测不到
         （于是"关键词命中"和"关键词不命中"都能干净地单独验证）
      ② 记录收到的每一段文本 → 用来验证"查询侧到底加没加 instruction"
    """

    def __init__(self):
        self.seen = []                       # 收到过的所有文本

    def embed(self, texts):
        self.seen.extend(texts)              # 记下来，供后面断言
        return [QVEC.copy() for _ in texts]


def build_fake_index():
    """
    手工拼一个"够用"的索引

    ★ 为什么要造假 index？
      测召回逻辑只需要 model / dicts / dict_vectors 三个键，
      不必真的去连 ClickHouse 抓表、加载 512 维模型（那要几十秒）。
      测试跑得快，才会愿意经常跑。
    """
    model = FakeModel()
    entries = [
        {
            "name": "应用主体", "dict_type": "app_entity_source",
            "field_hint": "app_entity_source",
            "keywords": ["渠道", "来源", "source"],
            "items": [
                {"value": "101", "label": "车拖车"},
                {"value": "204", "label": "慧拖车APP"},
                {"value": "0", "label": "0"},        # ★ 纯数字 label，应被过滤
                {"value": "9", "label": "?"},        # ★ 长度 1 的 label，应被过滤
            ],
        },
        {
            "name": "来源渠道", "dict_type": "channel_source",
            "field_hint": "channel_source",
            "keywords": ["渠道"],
            "items": [
                {"value": "101", "label": "安卓"},
                {"value": "201", "label": "微信"},
            ],
        },
        {
            "name": "空字典", "dict_type": "empty_one",
            "field_hint": "", "keywords": ["空"],
            "items": [],                             # ★ 空字典不进索引
        },
    ]
    dicts = rag._build_dicts(entries)
    vectors = [DVEC.copy() for _ in dicts]       # 每个候选配一个正交向量
    return {"model": model, "dicts": dicts, "dict_vectors": vectors}, model


# ═══════════════════════════════════════════════════════════
# 第一部分：字典条目的构建（关键词是否含取值标签）
# ═══════════════════════════════════════════════════════════
def test_build_dicts():
    print()
    print("─" * 66)
    print("① 字典构建：取值的中文含义必须进关键词")
    print("─" * 66)

    index, _ = build_fake_index()
    by_name = {d["name"]: d for d in index["dicts"]}

    check("空字典不进索引", "空字典" in by_name, False)

    kw = by_name["应用主体"]["keywords"]
    check_true("取值 '车拖车' 是关键词", "车拖车" in kw)
    check_true("取值 '慧拖车APP' 是关键词", "慧拖车APP" in kw)
    check_true("字典名 '应用主体' 仍是关键词", "应用主体" in kw)
    check_true("人工配的 '渠道' 仍是关键词", "渠道" in kw)

    check("纯数字 label '0' 被过滤", "0" in kw, False)
    check("长度 1 的 label '?' 被过滤", "?" in kw, False)

    src_kw = by_name["来源渠道"]["keywords"]
    check_true("'安卓' 是关键词", "安卓" in src_kw)
    check_true("'微信' 是关键词", "微信" in src_kw)


# ═══════════════════════════════════════════════════════════
# 第二部分：字典召回（关键词路径）
# ═══════════════════════════════════════════════════════════
def test_retrieve_dicts():
    print()
    print("─" * 66)
    print("② 字典召回：提到取值名必须能召回，无关问题必须不召回")
    print("─" * 66)

    index, _ = build_fake_index()

    # ★ 向量路径被假模型废掉了（恒 0 分），所以下面全部走关键词路径
    #   【为什么用集合比而不是比列表？】
    #     当两个字典命中的关键词一样长时（如"各渠道的订单量"里，
    #     两边的"渠道"都是 2 个字），排序是平手 → 保持索引原序。
    #     这个顺序对结果没有影响（命中的字典都会整段注入），
    #     测它就只是在测"字典在配置库里的先后"，不是我们要保证的东西。
    cases = [
        # (问题, 期望召回的字典)
        ("车拖车的完单量",        ["应用主体"]),      # ★ 这就是当初答错的那题
        ("车拖车的订单量是多少",   ["应用主体"]),
        ("慧拖车APP的订单量",     ["应用主体"]),
        ("安卓用户的订单量",       ["来源渠道"]),
        ("微信渠道的订单量",       ["来源渠道", "应用主体"]),   # 两个都有信号
        ("各渠道的订单量",        ["来源渠道", "应用主体"]),    # "渠道"两处都配了
        ("查询完单量",            []),               # ★ 无关 → 必须不召回
        ("订单总量是多少",         []),               # ★ 无关 → 必须不召回
    ]
    for q, expect in cases:
        names, reason = rag.retrieve_dicts(index, q)
        check(f"「{q}」→ {expect or '不召回'}", sorted(names), sorted(expect))

    # 确认命中方式走的是关键词（而非向量）
    _, reason = rag.retrieve_dicts(index, "车拖车的完单量")
    check_true("命中方式为关键词精确命中", "关键词" in reason)


# ═══════════════════════════════════════════════════════════
# 第三部分：查询前缀（字典不加，表/指标要加）
# ═══════════════════════════════════════════════════════════
def test_query_prefix():
    print()
    print("─" * 66)
    print("③ 查询前缀：字典不加 BGE 检索指令，表/指标必须加")
    print("─" * 66)

    # ── 字典：必须【不】加 instruction ──
    index, model = build_fake_index()
    rag.retrieve_dicts(index, "车拖车的完单量")
    dict_texts = [t for t in model.seen if "车拖车的完单量" in t]
    check_true("字典查询收到了问题文本", dict_texts)
    check_true(
        f"字典查询【没】加 instruction（原文={dict_texts[0]!r}）",
        all(t == "车拖车的完单量" for t in dict_texts),
    )

    # ── 表/指标：必须【要】加 instruction（默认行为不能被改坏）──
    model2 = FakeModel()
    rag._hybrid_retrieve(
        "车拖车的完单量",
        [{"name": "某表", "keywords": ["不存在的词"], "doc": ""}],
        [DVEC.copy()], model2, 0.5, 1,
    )
    check_true(
        "表/指标查询加了 instruction",
        any(t.startswith(config.QUERY_INSTRUCTION) for t in model2.seen),
    )

    # ── 显式传 query_prefix="" 时也不加 ──
    model3 = FakeModel()
    rag._hybrid_retrieve(
        "车拖车的完单量",
        [{"name": "某表", "keywords": ["不存在的词"], "doc": ""}],
        [DVEC.copy()], model3, 0.5, 1, query_prefix="",
    )
    check_true(
        "显式传空前缀时不加 instruction",
        all(not t.startswith(config.QUERY_INSTRUCTION) for t in model3.seen),
    )


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print("╔" + "═" * 64 + "╗")
    print("║" + "检索层专项测试（重点：数据字典召回）".center(54) + "║")
    print("╚" + "═" * 64 + "╝")

    test_build_dicts()
    test_retrieve_dicts()
    test_query_prefix()

    print()
    print("=" * 66)
    total = len(PASSED) + len(FAILED)
    if not FAILED:
        print(f"🎉 全部通过（{len(PASSED)}/{total}）")
    else:
        print(f"⚠️ 通过 {len(PASSED)}/{total}，失败项：")
        for f in FAILED:
            print(f"   ❌ {f}")
    print("=" * 66)
    # ★ 失败必须让进程返回非 0 —— 否则 CI/上层 runner 会把失败的测试当成功（曾经就是这样漏掉的）
    raise SystemExit(1 if FAILED else 0)
