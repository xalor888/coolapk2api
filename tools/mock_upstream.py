#!/usr/bin/env python3
"""本地 mock 上游：不依赖酷安，用标准库复刻 feedAsk 的 SSE 行为。

用途：在没有可用出口 / 凭证过期时，仍能端到端验证网关自身的协议转换。

    python tools/mock_upstream.py --port 8899
    COOLAPK2API_UPSTREAM=http://127.0.0.1:8899 python -m app.main --port 8321

支持 `--mode` 复刻三种上游状态：
    ok    正常返回（默认）
    edge  复刻 TencentEdgeOne 拦截：403 + 空体 + EO-LOG-UUID 头
    auth  复刻源站鉴权失败：200 + JSON {"status":403,"message":"API unsupported."}
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ANSWER = ("这是 mock 上游的流式回答：网关的 OpenAI / Anthropic 双向协议转换正常。"
          "第一段。第二段。第三段。")

CHUNKS = [ANSWER[i:i + 8] for i in range(0, len(ANSWER), 8)]

MODE = "ok"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 安静一点
        print(f"[mock] {fmt % args}")

    # ---------------------------------------------------------------- helpers
    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _edge_block(self) -> None:
        self.send_response(403)
        self.send_header("Content-Type", "text/html; charset=UTF-8")
        self.send_header("Server", "TencentEdgeOne")
        self.send_header("EO-LOG-UUID", "mock-0000000000000000")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------------------------------------------------------------- routes
    def do_GET(self):
        path = urlparse(self.path).path
        if MODE == "edge" and path.startswith("/v6/"):
            return self._edge_block()
        if path == "/v6/main/init":
            if MODE == "auth":   # 复刻源站对失效接口的 200+JSON 拒绝
                return self._send_json({"status": 403, "message": "API unsupported."})
            return self._send_json({"status": 200, "message": "mock ok"})
        self._send_json({"status": 404, "message": "mock: no such path"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        if MODE == "edge" and path.startswith("/v6/"):
            return self._edge_block()
        if MODE == "auth":
            return self._send_json({"status": 403, "message": "API unsupported."})

        if path == "/v6/search":
            return self._send_json({"data": [
                {"entityType": "feed", "id": "1001"},
                {"entityType": "feed", "id": "1002"},
            ]})
        if path == "/v6/feed/detail":
            fid = (query.get("id") or ["1001"])[0]
            return self._send_json({"data": {"id": fid, "aiAccessKey": "mock-access-key"}})
        if path == "/v6/ai/feedAsk":
            return self._feed_ask()
        self._send_json({"status": 404, "message": "mock: no such path"}, 404)

    # ---------------------------------------------------------------- SSE
    def _feed_ask(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def frame(obj: dict) -> None:
            line = "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"
            data = line.encode()
            self.wfile.write(b"%X\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

        frame({"data": {"entityType": "ai_prompt"}})
        for piece in CHUNKS:
            frame({"data": {"entityType": "ai_prompt_answer_stream", "text": piece}})
            time.sleep(0.01)
        frame({"data": {"entityType": "ai_prompt_answer", "text": ANSWER,
                        "finish_reason": "finish"}})
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def main() -> int:
    global MODE
    parser = argparse.ArgumentParser(description="coolapk2api mock 上游")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--mode", choices=["ok", "edge", "auth"], default="ok")
    args = parser.parse_args()
    MODE = args.mode

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[*] mock upstream on http://{args.host}:{args.port}  mode={MODE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
