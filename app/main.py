"""FastAPI 应用：路由 + 双协议适配接线。

启动：``python -m app.main`` 或 ``uvicorn app.main:app --port 8321``
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from . import __version__
from .adapters import anthropic, openai, prompt as prompt_adapter
from .config import Settings, load_settings
from .coolapk import CoolapkClient
from .coolapk.errors import (AuthExpired, CoolapkError, CredentialMissing,
                             EdgeBlocked, FeedUnavailable, UpstreamHTTP)

log = logging.getLogger("coolapk2api")
STATIC = Path(__file__).resolve().parent / "static"


def _select_model(requested: str | None, settings: Settings) -> str:
    """非 deepseek* 或不在白名单内的一律回落到默认模型。"""
    if requested and str(requested).startswith("deepseek"):
        return str(requested)
    return settings.models.default


def create_app(settings: Settings | None = None,
               client: CoolapkClient | None = None) -> FastAPI:
    settings = settings or load_settings()
    client = client or CoolapkClient(settings)
    app = FastAPI(title="coolapk2api", version=__version__)
    app.state.settings = settings
    app.state.client = client

    def authorized(request: Request) -> bool:
        key = settings.server.api_key
        if not key:
            return True
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and auth[7:] == key:
            return True
        return request.headers.get("x-api-key", "") == key

    # ---------------------------------------------------------------- 页面
    @app.get("/", response_class=HTMLResponse)
    async def panel() -> str:
        return (STATIC / "panel.html").read_text(encoding="utf-8")

    @app.get("/healthz")
    async def healthz() -> dict:
        info = client.health(deep=False)
        info["version"] = __version__
        return info

    @app.get("/v1/panel/status")
    async def panel_status() -> dict:
        info = client.health(deep=False)
        return {
            "ok": info.get("ok", False),
            "stage": info.get("stage"),
            "error": info.get("error"),
            "feed_id": info.get("feed_id"),
            "auth": "PROXY_API_KEY" if settings.server.api_key else "off",
            "upstream": info.get("upstream"),
        }

    # ---------------------------------------------------------------- OpenAI
    @app.get("/v1/models")
    async def list_models(request: Request):
        if not authorized(request):
            return JSONResponse(openai.error("unauthorized", 401, "authentication_error"),
                                status_code=401)
        return openai.model_list(settings.models.available)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        if not authorized(request):
            return JSONResponse(openai.error("unauthorized", 401, "authentication_error"),
                                status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(openai.error("invalid json body"), status_code=400)

        messages = body.get("messages") or []
        if not messages:
            return JSONResponse(openai.error("messages required"), status_code=400)

        model = _select_model(body.get("model"), settings)
        text = prompt_adapter.flatten(messages, body.get("system"))
        want_stream = bool(body.get("stream"))

        try:
            if want_stream:
                return StreamingResponse(
                    openai.stream(model, client.ask(text, model)),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            content, reasoning = "", ""
            for kind, chunk in client.ask(text, model):
                if kind == "delta" and chunk:
                    content += chunk
                elif kind == "thinking":
                    reasoning = reasoning or "…"
                elif kind == "done" and chunk and not content:
                    content = chunk
            return openai.completion(model, content, reasoning, text)
        except Exception as exc:  # 统一映射成 OpenAI 错误体
            return _error_response(exc, openai.error, request)

    # ---------------------------------------------------------------- Anthropic
    @app.post("/v1/messages")
    async def anthropic_messages(request: Request):
        if not authorized(request):
            return JSONResponse(anthropic.error("unauthorized", 401, "authentication_error"),
                                status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(anthropic.error("invalid json body"), status_code=400)

        messages = body.get("messages") or []
        if not messages:
            return JSONResponse(anthropic.error("messages required"), status_code=400)

        model = _select_model(body.get("model"), settings)
        text = prompt_adapter.flatten(messages, body.get("system"))
        want_stream = bool(body.get("stream"))

        try:
            if want_stream:
                return StreamingResponse(
                    anthropic.stream(model, client.ask(text, model), text),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            content = ""
            for kind, chunk in client.ask(text, model):
                if kind == "delta" and chunk:
                    content += chunk
                elif kind == "done" and chunk and not content:
                    content = chunk
            return anthropic.message(model, content, text)
        except Exception as exc:
            return _error_response(exc, anthropic.error, request)

    return app


def _error_response(exc: Exception, builder, request: Request) -> JSONResponse:
    """异常 -> 合适的 HTTP 状态码 + 协议错误体。"""
    if isinstance(exc, CredentialMissing):
        return JSONResponse(builder(str(exc), 500, "configuration_error"), status_code=500)
    if isinstance(exc, EdgeBlocked):
        return JSONResponse(builder(str(exc), 502, "upstream_unreachable"), status_code=502)
    if isinstance(exc, AuthExpired):
        return JSONResponse(builder(str(exc), 502, "upstream_auth"), status_code=502)
    if isinstance(exc, FeedUnavailable):
        return JSONResponse(builder(str(exc), 503, "upstream_unavailable"), status_code=503)
    if isinstance(exc, UpstreamHTTP):
        return JSONResponse(builder(str(exc), 502, "upstream_error"), status_code=502)
    if isinstance(exc, CoolapkError):
        return JSONResponse(builder(str(exc), 502, "upstream_error"), status_code=502)
    import requests

    if isinstance(exc, requests.RequestException):
        return JSONResponse(builder(f"网络错误：{exc}", 504, "network_error"), status_code=504)
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(builder(f"内部错误：{exc}", 500, "internal_error"), status_code=500)


# 供 `uvicorn app.main:app` 使用
app = create_app()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="coolapk2api", description="酷安 AI 反代网关")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--check", action="store_true", help="只做自检，不启动服务")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    if args.host:
        settings.server.host = args.host
    if args.port:
        settings.server.port = args.port

    logging.basicConfig(
        level=getattr(logging, settings.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if args.check:
        info = CoolapkClient(settings).health(deep=True)
        for k, v in info.items():
            print(f"{k:12} {v}")
        return 0 if info.get("ok") else 1

    import uvicorn

    print(f"[*] coolapk2api {__version__} -> http://{settings.server.host}:{settings.server.port}"
          f"  (auth: {'on' if settings.server.api_key else 'off'}, upstream: {settings.upstream.base})")
    uvicorn.run(create_app(settings), host=settings.server.host, port=settings.server.port,
                log_level=settings.logging.level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
