"""酷安上游客户端：凭证、请求头、feed 发现、feedAsk 流式解析。

对外只有三件事：``health()``、``context()``、``ask()``。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests

from ..config import Settings
from . import auth
from .errors import (AuthExpired, CoolapkError, CredentialMissing, EdgeBlocked,
                     FeedUnavailable, UpstreamHTTP)

VERSION_NAME = "16.6.4"
VERSION_CODE = 2_609_291
UA = "CoolMarket/16.6.4-2609291-universal (Android 16; arm64-v8a; Pixel 6)"


@dataclass
class FeedContext:
    feed_id: str
    access_key: str
    fetched_at: float

    @property
    def age(self) -> float:
        return time.time() - self.fetched_at


class CoolapkClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings = settings
        self.up = settings.upstream
        self.http = session or requests.Session()
        self._device: str | None = None
        self._blob: bytes | None = None
        self._cookie: str | None = None
        self._token: str | None = None
        self._token_ts: float = 0.0
        self._context: FeedContext | None = None

    # ------------------------------------------------------------ credentials
    def _read(self, path: Path, what: str) -> str:
        if not path.exists():
            raise CredentialMissing(f"缺少{what}：{path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise CredentialMissing(f"{what} 为空：{path}")
        return text

    @property
    def cookie(self) -> str:
        if self._cookie is None:
            self._cookie = self._read(self.settings.cookie_path, " cookie")
        return self._cookie

    @property
    def device(self) -> str:
        if self._device is None:
            self._device = auth.decode_device_spec(
                self._read(self.settings.device_path, " device_code"))
        return self._device

    @property
    def blob(self) -> bytes:
        if self._blob is None:
            self._blob = auth.load_blob(self.settings.blob_path)
        return self._blob

    def token(self, force: bool = False) -> str:
        """token 与时间戳绑定，缓存 20 秒足够（上游允许一定漂移）。"""
        if force or self._token is None or time.time() - self._token_ts > 20:
            self._token = auth.get_app_token(self.device, self.blob)
            self._token_ts = time.time()
        return self._token

    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "X-Sdk-Int": "36",
            "X-Sdk-Locale": "zh-CN",
            "X-App-Id": auth.APP_ID,
            "X-App-Token": self.token(),
            "X-App-Version": VERSION_NAME,
            "X-App-Code": str(VERSION_CODE),
            "X-Api-Version": "16",
            "X-App-Device": self.device,
            "X-Dark-Mode": "0",
            "X-App-Channel": "coolapk",
            "X-App-Mode": "universal",
            "X-App-Supported": str(VERSION_CODE),
            "Cookie": self.cookie,
        }

    # ------------------------------------------------------------ transport
    @property
    def base(self) -> str:
        return self.up.base.rstrip("/")

    def _classify(self, resp: requests.Response, body: str | None = None) -> None:
        """把上游的各种拒绝形态拆成可操作的异常。

        注意：酷安对「鉴权失效 / 接口不支持」是 **HTTP 200 + JSON {"status":403,...}**
        （实测 /v5/main/init），所以 200 也要看响应体。
        """
        text = resp.text if body is None else body
        server = resp.headers.get("server", "")

        if resp.status_code == 403 and not text.strip() and (
                "EdgeOne" in server or "EO-LOG-UUID" in resp.headers):
            raise EdgeBlocked(
                "被 TencentEdgeOne 边缘节点拦截（403 空响应）。"
                "常见原因：出口 IP 不在允许范围。可换国内出口，"
                "或把 upstream.base 指向国内中转。"
            )

        payload = None
        if text and text.lstrip().startswith("{"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None

        if isinstance(payload, dict) and "status" in payload:
            status = payload.get("status")
            message = str(payload.get("message") or "")
            if status in (401, 403) or "unsupported" in message.lower():
                raise AuthExpired(
                    f"源站拒绝（status={status}，message={message!r}）。"
                    "cookie 可能已过期，或该接口/版本不再被接受；需重新登录并更新 cookie。"
                )

        if resp.status_code == 200:
            return
        raise UpstreamHTTP(resp.status_code, text)

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base}{path}"
        kwargs.setdefault("timeout", self.up.request_timeout)
        resp = self.http.request(method, url, headers=self.headers(), **kwargs)
        self._classify(resp)
        return resp

    # ------------------------------------------------------------ discovery
    def discover_feed(self) -> tuple[str, str]:
        """搜一批帖子，挑第一个带 aiAccessKey 的。"""
        q = self.up.discovery_query
        resp = self._request(
            "POST", "/v6/search",
            params={"q": q, "type": "feed", "page": 1, "firstItem": "", "lastItem": ""},
        )
        payload = resp.json()
        ids: list[str] = []
        for row in payload.get("data") or []:
            entities = row.get("entities") if isinstance(row, dict) else None
            for ent in entities or [row]:
                if isinstance(ent, dict) and ent.get("entityType") == "feed" and ent.get("id"):
                    ids.append(str(ent["id"]))
        for fid in ids[:10]:
            detail = self._request("POST", "/v6/feed/detail",
                                   params={"id": fid}, data={"trace": ""}).json()
            ent = detail.get("data") or {}
            key = ent.get("aiAccessKey") if isinstance(ent, dict) else None
            if key:
                return fid, str(key)
        raise FeedUnavailable(
            f"在「{q}」的搜索结果里没找到开启 AI 入口的帖子（功能可能灰度或已下线）")

    def context(self, force: bool = False) -> FeedContext:
        if (force or self._context is None
                or self._context.age > self.up.context_ttl):
            fid, key = self.discover_feed()
            self._context = FeedContext(fid, key, time.time())
        return self._context

    # ------------------------------------------------------------ ask
    def ask(self, prompt: str, model: str, feed: FeedContext | None = None) -> Iterator[tuple[str, str | None]]:
        """向 feedAsk 发请求，逐块产出 ``(kind, text)``。

        kind: ``thinking``（仅信号） / ``delta``（增量） / ``done``（全量兜底）。
        """
        ctx = feed or self.context()
        params = {
            "feed_id": ctx.feed_id,
            "model": model,
            "incremental_output": 1,
            "stream_output": 1,
            "is_retry": 0,
            "aiAccessKey": ctx.access_key,
        }
        resp = self.http.post(
            f"{self.base}/v6/ai/feedAsk",
            params=params,
            data={"prompt": prompt},
            headers=self.headers(),
            stream=True,
            timeout=self.up.stream_timeout,
        )
        if resp.status_code != 200:
            try:
                self._classify(resp)
            finally:
                resp.close()
        try:
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                line = raw.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    return
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev = obj.get("data", obj) if isinstance(obj, dict) else {}
                if not isinstance(ev, dict):
                    continue
                et = ev.get("entityType", "")
                if et == "ai_prompt_answer_stream":
                    yield "delta", ev.get("text") or ""
                elif et == "ai_prompt_answer":
                    yield "done", ev.get("text") or ""
                elif et == "ai_prompt":
                    yield "thinking", None
                if ev.get("finish_reason") == "finish":
                    return
        finally:
            resp.close()

    # ------------------------------------------------------------ health
    def health(self, deep: bool = False) -> dict:
        """分层自检。

        deep=False：只探一次 ``/v6/main/init``，足以区分「边缘拦截 / 鉴权失效 / 通」。
        deep=True ：额外做一次 feed 发现，拿到 feed_id。
        """
        info: dict = {"ok": False, "upstream": self.base}
        try:
            self.cookie, self.device, self.blob  # noqa: B018 - 触发校验
        except CoolapkError as exc:
            info["error"] = str(exc)
            info["stage"] = "credentials"
            return info
        try:
            info["token_ok"] = bool(self.token())
            info["stage"] = "token"
        except CoolapkError as exc:
            info.update(stage="credentials", error=str(exc))
            return info
        try:
            # 轻量探针：走 /v6 前缀，能暴露边缘拦截与 cookie 过期
            self._request("GET", "/v6/main/init")
            info["stage"] = "probe"
            if deep:
                ctx = self.context(force=True)
                info["feed_id"] = ctx.feed_id
            info["ok"] = True
        except EdgeBlocked as exc:
            info.update(stage="edge", error=str(exc))
        except AuthExpired as exc:
            info.update(stage="auth", error=str(exc))
        except CoolapkError as exc:
            info.update(stage="upstream", error=str(exc))
        except requests.RequestException as exc:
            info.update(stage="network", error=f"网络错误：{exc}")
        return info
