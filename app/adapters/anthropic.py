"""Anthropic Messages 协议适配（含 SSE 事件序列）。"""
from __future__ import annotations

import json
import time
import uuid
from typing import Iterator

from .prompt import estimate_tokens


def error(message: str, code: int = 400, err_type: str = "invalid_request_error") -> dict:
    return {"type": "error", "error": {"type": err_type, "message": message}}


def message(model: str, content: str, prompt: str) -> dict:
    return {
        "id": "msg_" + uuid.uuid4().hex[:24],
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": content}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": estimate_tokens(prompt),
                  "output_tokens": estimate_tokens(content)},
    }


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream(model: str, events: Iterator[tuple[str, str | None]], prompt: str) -> Iterator[str]:
    mid = "msg_" + uuid.uuid4().hex[:24]
    yield _event("message_start", {
        "type": "message_start",
        "message": {"id": mid, "type": "message", "role": "assistant", "model": model,
                    "content": [], "stop_reason": None, "stop_sequence": None,
                    "usage": {"input_tokens": estimate_tokens(prompt), "output_tokens": 0}},
    })
    yield _event("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""},
    })
    yield _event("ping", {"type": "ping"})

    full = ""
    produced = False
    try:
        for kind, text in events:
            if kind == "delta" and text:
                produced = True
                full += text
                yield _event("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": text},
                })
            elif kind == "done" and text and not produced:
                produced = True
                full = text
                yield _event("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": text},
                })
        yield _event("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield _event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": estimate_tokens(full)},
        })
        yield _event("message_stop", {"type": "message_stop"})
    except Exception as exc:
        yield _event("error", {"type": "error",
                               "error": {"type": "api_error", "message": str(exc)}})
