# -*- coding: utf-8 -*-
"""
exceptions.py —— LLM 错误分类层（把「是什么错」从「在哪抛」里拆出来）

★ 为什么要有这一层？
  之前的现状（2026-11-09 审查确认）：
    · 402 余额不足 / 401 密钥失效 / 429 限流 / 超时，全都被当成普通 Exception 处理；
    · query_understanding 对【任何】异常都重试 2 次 —— 402 时白烧 2 次调用；
    · agent.ask 兜底把技术堆栈文案直接甩给用户，没有结构化错误码。

  本层只做一件事：把异常归类成「可不可重试 + 状态码 + 友好文案」，
  调用方据此决定：立即中止（fatal）/ 退避重试（rate_limit）/ 重试（transient）。

★ 判据（openai SDK 3.13）：
    AuthenticationError=401 / PermissionDeniedError=403 / RateLimitError=429 是固定子类；
    402（DeepSeek 余额不足）没有专属子类，落在通用 APIStatusError，需读 .status_code。
    → 因此「精确 isinstance 子类」和「读 status_code 鸭型」两条通道都保留，
      后者也顺带让本函数可用假对象做单测。

★ 依赖：本文件【零项目依赖】（不 import config/llm），避免任何循环依赖。
"""


class LLMError(Exception):
    """LLM 调用错误的统一分类结果（不是被抛出的异常本身，而是"归类"）。"""

    def __init__(self, kind, status_code=None, message="", retryable=False):
        # kind: fatal（不可重试）/ rate_limit（退避重试）/ transient（可重试）/ unknown
        self.kind = kind
        self.status_code = status_code      # 原始 HTTP 状态码，无则 None
        self.message = (message or "")[:500]  # 截断，避免长 body 撑爆日志
        self.retryable = retryable
        super().__init__(self.message)

    def friendly(self):
        """面向用户的一句话提示（不再回显技术堆栈）。"""
        if self.kind == "rate_limit":
            return "请求过于频繁，请稍后重试。"
        if self.kind == "transient":
            return "模型服务响应超时或网络异常，请重试。"
        if self.kind == "fatal":
            return _FATAL_HINTS.get(self.status_code,
                                    "模型服务调用失败，请稍后重试或联系管理员。")
        return "模型服务调用失败，请稍后重试。"


# 致命（不可重试）错误码 → 友好文案
_FATAL_HINTS = {
    401: "模型密钥无效，请联系管理员更新密钥。",
    402: "模型服务余额不足，请联系管理员充值。",
    403: "模型访问被拒绝，请联系管理员。",
}


def classify_llm_error(e):
    """
    把异常归类为 LLMError；无法归类（非 openai 异常）返回 None，由上层按原有逻辑处理。

    返回 LLMError 或 None。
    """
    if e is None:
        return None

    # ① 精确子类优先（这些子类自带固定 status_code）
    try:
        import openai
    except Exception:
        openai = None
    if openai is not None:
        if isinstance(e, openai.RateLimitError):
            return LLMError("rate_limit", 429, str(e), retryable=True)
        if isinstance(e, openai.AuthenticationError):
            return LLMError("fatal", 401, str(e))
        if isinstance(e, openai.PermissionDeniedError):
            return LLMError("fatal", 403, str(e))
        if isinstance(e, (openai.APITimeoutError, openai.APIConnectionError)):
            return LLMError("transient", None, str(e), retryable=True)

    # ② 鸭型读 status_code（覆盖真实 APIStatusError 的 402，也便于单测用假对象）
    code = getattr(e, "status_code", None)
    if isinstance(code, int):
        if code == 429:
            return LLMError("rate_limit", 429, str(e), retryable=True)
        if code in (401, 402, 403):
            return LLMError("fatal", code, str(e))
        if 400 <= code < 500:
            return LLMError("fatal", code, str(e))
        if 500 <= code < 600:
            return LLMError("transient", code, str(e), retryable=True)
        return LLMError("unknown", code, str(e))

    # ③ 其余 openai 错误（ContentFilter 等）
    if openai is not None and isinstance(e, openai.OpenAIError):
        return LLMError("unknown", None, str(e))

    # ④ 非 LLM 异常（ValueError / 内置 TimeoutError / SQL 错误等）→ 不归类
    return None
