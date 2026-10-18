"""酷安上游的异常类型。

每种异常对应一种**可操作的**处置方式，不要笼统抛 RuntimeError。
"""
from __future__ import annotations


class CoolapkError(Exception):
    """上游相关错误的基类。"""


class CredentialMissing(CoolapkError):
    """本地缺凭证文件（cookie / device / auth blob）。"""


class EdgeBlocked(CoolapkError):
    """被 CDN 边缘节点（TencentEdgeOne）拦截：403 + 空响应体。

    通常是出口 IP 被路径级规则拦掉，与 cookie 是否过期无关。
    处置：换国内出口，或把 upstream.base 指向国内中转。
    """


class AuthExpired(CoolapkError):
    """源站明确返回鉴权失败（JSON 里的 status/message），需要重新登录拿 cookie。"""


class UpstreamHTTP(CoolapkError):
    """上游返回了非 200 且不属于以上两类。"""

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body[:400]
        super().__init__(f"upstream HTTP {status}: {self.body}")


class FeedUnavailable(CoolapkError):
    """找不到带 AI 入口的 feed（灰度/下线/风控）。"""
