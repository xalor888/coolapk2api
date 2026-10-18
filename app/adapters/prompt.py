"""把 OpenAI / Anthropic 的多轮消息压平成一条 prompt。

酷安 feedAsk 只收单条 prompt，所以这里是协议适配的核心一步。
"""
from __future__ import annotations

from typing import Any, Iterable


def _text_of(content: Any) -> str:
    """content 可能是 str，也可能是 Anthropic 的 block 列表。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def flatten(messages: Iterable[dict], system: Any = None) -> str:
    parts: list[str] = []
    sys_text = _text_of(system) if system else ""
    if sys_text:
        parts.append(f"[system]\n{sys_text}")
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        text = _text_of(msg.get("content", ""))
        if role == "assistant":
            parts.append(f"[assistant] {text}")
        elif role == "system":
            parts.append(f"[system]\n{text}")
        else:
            parts.append(f"[user] {text}")
    return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """粗估：中文约 2 字符 / token。仅用于填 usage 字段。"""
    return max(1, len(text) // 2)
