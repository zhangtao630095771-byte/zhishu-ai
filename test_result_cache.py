# -*- coding: utf-8 -*-
"""
test_result_cache.py —— 取数结果缓存 测试

★ 这个模块最危险的地方不是"缓存没命中"，而是**命中错了**：
  把 A 用户的脱敏结果给到 B，或者口径改了还给旧答案。
  所以测试重点全部放在"key 是否会区分"上，而不是"缓存是否更快"。

★ 纯逻辑测试：不连数据库（知识指纹打桩），可在 unit 层跑。

运行：
    .venv/bin/python test_result_cache.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config          # noqa: E402
import result_cache as RC   # noqa: E402

_passed = 0
_failed = []


def ok(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name} {detail}")


IDX = {"allowed_tables": ["t_a", "t_b"]}
U1 = {"username": "alice", "role": "analyst"}
U2 = {"username": "bob", "role": "analyst"}


def _with(**kw):
    """临时改 config 属性，返回还原函数"""
    origin = {}
    for k, v in kw.items():
        origin[k] = getattr(config, k, None)
        setattr(config, k, v)
    def restore():
        for k, v in origin.items():
            setattr(config, k, v)
    return restore


def test_disabled():
    """TTL=0 → 完全不参与缓存（评测/排障时用这个）"""
    RC.clear()
    restore = _with(RESULT_CACHE_TTL=0)
    try:
        k = RC.make_key(IDX, "查一下订单量", U1)
        ok(k is None, "TTL=0 时 make_key 返回 None")
        RC.put(k, {"ok": True})
        ok(RC.get(k) is None, "TTL=0 时 get 永远不命中")
        ok(RC.stats()["enabled"] is False, "stats 报告未启用")
    finally:
        restore()


def test_key_isolation():
    """★ 核心：不同用户 / 不同权限 / 不同知识 / 不同开关 必须是不同 key"""
    RC.clear()
    restore = _with(RESULT_CACHE_TTL=60)
    old_fp = RC._knowledge_fp
    RC._knowledge_fp = lambda: "K1"
    try:
        k_alice = RC.make_key(IDX, "查一下订单量", U1)
        k_bob = RC.make_key(IDX, "查一下订单量", U2)
        ok(k_alice and k_bob, "正常构造出 key")
        ok(k_alice != k_bob, "★ 不同用户 → 不同 key（防止脱敏/行级权限串号）")

        idx2 = {"allowed_tables": ["t_a"]}
        k_acl = RC.make_key(idx2, "查一下订单量", U1)
        ok(k_alice != k_acl, "★ 可见表变了 → 不同 key（ACL 变更自动失效）")

        RC._knowledge_fp = lambda: "K2"
        k_know = RC.make_key(IDX, "查一下订单量", U1)
        ok(k_alice != k_know, "★ 知识指纹变了 → 不同 key（改口径自动失效）")
        RC._knowledge_fp = lambda: "K1"

        restore2 = _with(EXP_SUCCESS_RECALL=False)
        try:
            k_flag = RC.make_key(IDX, "查一下订单量", U1)
            ok(k_alice != k_flag, "★ 经验开关变了 → 不同 key（A/B/C/D 对照不互相污染）")
        finally:
            restore2()

        k_same = RC.make_key(IDX, "查一下订单量", U1)
        ok(k_same == k_alice, "同输入 → 同 key（缓存可复用）")

        ok(RC.make_key(IDX, "  查一下订单量  ", U1) == k_alice,
           "首尾空白不影响 key（归一化生效）")
        ok(RC.make_key(IDX, "查一下退款量", U1) != k_alice, "不同问题 → 不同 key")
    finally:
        RC._knowledge_fp = old_fp
        restore()


def test_fp_failure_no_cache():
    """★ 知识指纹算不出来时，必须拒绝缓存（不能猜一个 key 出来）"""
    restore = _with(RESULT_CACHE_TTL=60)
    old_fp = RC._knowledge_fp
    RC._knowledge_fp = lambda: ""
    try:
        ok(RC.make_key(IDX, "任意问题", U1) is None,
           "★ 指纹缺失 → 不缓存（宁可不缓存，也不能猜 key）")
    finally:
        RC._knowledge_fp = old_fp
        restore()


def test_ttl_and_evict():
    RC.clear()
    restore = _with(RESULT_CACHE_TTL=1, RESULT_CACHE_MAX=3)
    old_fp = RC._knowledge_fp
    RC._knowledge_fp = lambda: "K"
    try:
        k = RC.make_key(IDX, "问题A", U1)
        RC.put(k, {"ok": True, "rows": [[1]]})
        ok(RC.get(k) is not None, "刚写入能命中")
        time.sleep(1.1)
        ok(RC.get(k) is None, "超过 TTL 后失效")

        for i in range(5):
            RC.put(RC.make_key(IDX, f"问题{i}", U1), {"ok": True})
        ok(RC.stats()["size"] <= 3, "容量上限生效（不超过 MAX）")
    finally:
        RC._knowledge_fp = old_fp
        restore()


def test_should_cache():
    ok(RC.should_cache({"ok": True, "rows": []}) is True, "成功结果（0 行）可缓存")
    ok(RC.should_cache({"ok": True, "rows": [[1]]}) is True, "成功结果可缓存")
    ok(RC.should_cache({"ok": False, "error": "拒答"}) is False,
       "★ 失败结果不缓存（不能把偶发故障固化成稳定故障）")
    ok(RC.should_cache({"ok": True, "degraded": True}) is False,
       "★ 降级结果不缓存（否则 V2 恢复了也看不出来）")
    ok(RC.should_cache(None) is False, "非 dict 不缓存")


def test_clear():
    RC.clear()
    restore = _with(RESULT_CACHE_TTL=60)
    old_fp = RC._knowledge_fp
    RC._knowledge_fp = lambda: "K"
    try:
        k = RC.make_key(IDX, "问题X", U1)
        RC.put(k, {"ok": True})
        ok(RC.get(k) is not None, "写入后可命中")
        n = RC.clear("测试")
        ok(n >= 1, "clear 返回清理条数")
        ok(RC.get(k) is None, "clear 后不再命中")
    finally:
        RC._knowledge_fp = old_fp
        restore()


def main():
    for fn in (test_disabled, test_key_isolation, test_fp_failure_no_cache,
               test_ttl_and_evict, test_should_cache, test_clear):
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
