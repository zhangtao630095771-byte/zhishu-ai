# -*- coding: utf-8 -*-
"""
test_llm_errors.py —— LLM 错误分类层（exceptions.py）与 402 快速失败的单元测试

覆盖：
  ① classify_llm_error 对 status_code 鸭型通道（402/401/403/429/500/400）的分类；
  ② 真实 openai.APITimeoutError 归类为 transient；
  ③ 非 LLM 异常（ValueError / 内置 TimeoutError / None）返回 None；
  ④ query_understanding 遇到 4xx 只调 1 次（不再白烧第 2 次），超时仍重试。
"""
import sys
import exceptions

FAILED = []


def check(name, got, want):
    ok = (got == want)
    print(("✅ " if ok else "❌ ") + name + ("" if ok else f"  got={got!r} want={want!r}"))
    if not ok:
        FAILED.append(name)


class _FakeHTTPError(Exception):
    """带 status_code 的假 HTTP 异常（模拟 openai.APIStatusError 的 402 等）。"""

    def __init__(self, status_code, msg=""):
        self.status_code = status_code
        self._msg = msg or f"Error code: {status_code}"
        super().__init__(self._msg)

    def __str__(self):
        return self._msg


def test_classify_duck():
    print("\n【分类】status_code 鸭型通道")
    e402 = exceptions.classify_llm_error(_FakeHTTPError(402, "Insufficient Balance"))
    check("402 → fatal", e402.kind, "fatal")
    check("402 → status_code 保留", e402.status_code, 402)
    check("402 → 不可重试", e402.retryable, False)
    check("402 → 友好文案含『余额不足』", "余额不足" in e402.friendly(), True)

    e401 = exceptions.classify_llm_error(_FakeHTTPError(401))
    check("401 → fatal", e401.kind, "fatal")
    check("401 → 文案含『密钥』", "密钥" in e401.friendly(), True)

    e403 = exceptions.classify_llm_error(_FakeHTTPError(403))
    check("403 → fatal", e403.kind, "fatal")

    e429 = exceptions.classify_llm_error(_FakeHTTPError(429))
    check("429 → rate_limit", e429.kind, "rate_limit")
    check("429 → 可重试", e429.retryable, True)

    e500 = exceptions.classify_llm_error(_FakeHTTPError(500))
    check("500 → transient", e500.kind, "transient")
    check("500 → 可重试", e500.retryable, True)

    e400 = exceptions.classify_llm_error(_FakeHTTPError(400))
    check("400 → fatal", e400.kind, "fatal")


def test_classify_real_timeout():
    print("\n【分类】真实 openai 异常")
    from openai import APITimeoutError
    t = exceptions.classify_llm_error(APITimeoutError("timed out"))
    check("APITimeoutError → transient", t.kind, "transient")
    check("APITimeoutError → 可重试", t.retryable, True)


def test_classify_non_llm():
    print("\n【分类】非 LLM 异常 → 不归类")
    check("ValueError → None", exceptions.classify_llm_error(ValueError("x")), None)
    check("内置 TimeoutError → None", exceptions.classify_llm_error(TimeoutError("t")), None)
    check("None → None", exceptions.classify_llm_error(None), None)


def test_understand_fatal_no_retry():
    print("\n【402 快速失败】理解层遇到 4xx 只调 1 次，不再白烧第 2 次")
    import query_understanding as QU
    calls = {"n": 0}

    def fake_chat(*a, **kw):
        calls["n"] += 1
        raise _FakeHTTPError(402, "Insufficient Balance")

    orig = QU.llm.chat
    QU.llm.chat = fake_chat
    try:
        r = QU.understand("查询订单量", index={"metrics": [], "entities": []})
    finally:
        QU.llm.chat = orig
    check("402 → degraded=True", r.get("degraded"), True)
    check("402 → 只调 1 次", calls["n"], 1)


def test_understand_transient_retries():
    print("\n【transient 仍重试】超时类错误仍重试 1 次（2 次调用后降级）")
    import query_understanding as QU
    calls = {"n": 0}

    def fake_chat(*a, **kw):
        calls["n"] += 1
        raise TimeoutError("timed out")

    orig = QU.llm.chat
    QU.llm.chat = fake_chat
    try:
        r = QU.understand("查询订单量", index={"metrics": [], "entities": []})
    finally:
        QU.llm.chat = orig
    check("超时 → degraded=True", r.get("degraded"), True)
    check("超时 → 调用 2 次", calls["n"], 2)


def main():
    test_classify_duck()
    test_classify_real_timeout()
    test_classify_non_llm()
    test_understand_fatal_no_retry()
    test_understand_transient_retries()
    print()
    if FAILED:
        print(f"❌ 失败 {len(FAILED)} 项：{FAILED}")
        return 1
    print("🎉 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
