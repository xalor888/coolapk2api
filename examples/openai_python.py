#!/usr/bin/env python3
"""OpenAI SDK 示例：把 base_url 指到本地网关即可。

依赖：pip install openai
运行：python examples/openai_python.py
"""
from __future__ import annotations

import os
import sys

try:
    from openai import OpenAI
except ImportError:
    sys.exit("先安装 SDK：pip install openai")

BASE = os.environ.get("BASE", "http://127.0.0.1:8321")
KEY = os.environ.get("KEY", "not-needed")  # 网关未设 api_key 时随便填
MODEL = os.environ.get("MODEL", "deepseek-v3")

client = OpenAI(base_url=f"{BASE}/v1", api_key=KEY)

print("== 非流式 ==")
resp = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": "你是一个简洁的助手"},
        {"role": "user", "content": "用三句话解释什么是反向代理"},
    ],
)
print(resp.choices[0].message.content)
print(f"(usage: {resp.usage.total_tokens} tokens, 估算值)")

print("\n== 流式 ==")
stream = client.chat.completions.create(
    model=MODEL,
    stream=True,
    messages=[{"role": "user", "content": "列出三条使用反向代理的好处"}],
)
for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)
print()
