# -*- coding: utf-8 -*-
"""
llm.py —— 只负责一件事：和大模型说话
★ 职责单一：别的文件不需要知道 API 怎么调，只需要调 chat()
★ 好处：将来换模型（换 OpenAI / 换本地模型），只改这一个文件
"""
from openai import OpenAI     # 引入大模型客户端
import config                 # 引入我们自己写的配置模块
#   import config = 把同目录下的 config.py 整个拿过来用
#   之后就能写 config.LLM_MODEL 来取里面的值


# 建立连接（模块被导入时执行一次，整个程序共用这一个连接）
client = OpenAI(
    api_key=config.LLM_API_KEY,       # 从 config 里取 Key
    base_url=config.LLM_BASE_URL,     # 从 config 里取地址
    max_retries=0,                    # ★ 关闭 SDK 自动重试：否则超时/慢请求会被重试 3 次，
                                      #   把一次 8s 的自审放大成 24s；自愈循环已自行负责重试。
)


def chat(system_prompt, user_prompt, timeout=None, max_tokens=None):
    """
    和大模型对话，返回它说的话（纯文本）

    参数：
        system_prompt = 系统指令（身份 + 规则 + 表结构），长期不变的那部分
        user_prompt   = 用户消息（这次问什么），每次都变的那部分
        timeout       = 可选超时（秒）；None 用客户端默认。用于查询理解等轻量调用兜底。
        max_tokens    = 可选输出上限；None 不限制。用于自审等场景防无界长生成。
    """
    # ★ 默认超时：不传时回落 config.LLM_GENERATE_TIMEOUT（之前用 SDK 默认 600s 太长，
    #   模型卡住会无限阻塞）。显式传入（如理解 8s / 自审 10/20s）仍优先。
    if timeout is None:
        timeout = config.LLM_GENERATE_TIMEOUT
    kwargs = dict(
        model=config.LLM_MODEL,                        # 用哪个模型
        messages=[                                     # 对话列表（整个 AI 应用的核心结构）
            {"role": "system", "content": system_prompt},
            #      system = 员工手册：你是谁、有哪些表、要守什么规矩
            {"role": "user", "content": user_prompt},
            #      user   = 每次派的单子：这次问什么
        ],
        temperature=config.LLM_TEMPERATURE,            # 取数必须为 0
        timeout=timeout,                               # 请求超时（秒）
    )
    if max_tokens is not None:                         # 只在显式传入时才限输出
        kwargs["max_tokens"] = max_tokens
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content.strip()
    #        resp                 ← 一整张返回表
    #          .choices           ← 结果行（模型可能给多个候选）
    #            [0]              ← 取第一行（我们只要一个答案）
    #              .message       ← 这一行的"消息"字段
    #                .content     ← 消息里的正文
    #          .strip()           ← 去掉首尾空白，≈ SQL 的 TRIM()
    #
    #        【SQL 类比】≈ SELECT content FROM resp_choices WHERE 序号 = 1;
