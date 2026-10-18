"""OpenAI Chat Completions 协议适配。"""
from __future__ import annotations

import json
import time
import uuid
from typing import Iterator

from .prompt import estimate_tokens

OWNED_BY = "coolapk-deepseek"


def error(message: str, code: int = 400, err_type: str = "invalid_request_error") -> dict:
    return {"error": {"message": message, "type": err_type, "code": code}}


def model_list(models: list[str]) -> dict:
    now = int(time.time())
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": now, "owned_by": OWNED_BY}
                 for m in models],
    }


def completion(model: str, content: str, reasoning: str, prompt: str) -> dict:
    message = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    pid, cid = estimate_tokens(prompt), estimate_tokens(content)
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": pid, "completion_tokens": cid,
                  "total_tokens": pid + cid},
    }


def _chunk(cid: str, model: str, delta: dict, finish: str | None = None) -> str:
    payload = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def stream(model: str, events: Iterator[tuple[str, str | None]]) -> Iterator[str]:
    """把上游 ``(kind, text)`` 流转成 OpenAI SSE。"""
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    started = False
    produced = False
    try:
        for kind, text in events:
            if kind == "delta" and text:
                if not started:
                    yield _chunk(cid, model, {"role": "assistant", "content": ""})
                    started = True
                produced = True
                yield _chunk(cid, model, {"content": text})
            elif kind == "done" and text and not produced:
                if not started:
                    yield _chunk(cid, model, {"role": "assistant", "content": ""})
                    started = True
                produced = True
                yield _chunk(cid, model, {"content": text})
        yield _chunk(cid, model, {}, "stop")
    except Exception as exc:  # 上游中途失败：把错误塞进流里，保持连接合法
        yield "data: " + json.dumps({"error": {"message": str(exc)}}, ensure_ascii=False) + "\n\n"
    finally:
        yield "data: [DONE]\n\n"
