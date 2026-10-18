"""酷安上游的异常类型。

每种异常对应一种**可操作的**处置方式，不要笼统抛 RuntimeError。
"""
from __future__ import annotations


class CoolapkError(Exception):
    """上游相关错误的基类。"""


class CredentialMissing(CoolapkError):
    """本地缺凭证文件（cookie / device / auth blob）。"""


class PathRejected(CoolapkError):
    """上游拒绝了该路径/凭据：`403` 且响应体 `0 字节`。

    判据（能与不能）：
    - 能：`403` + 响应体 0 字节，说明"这次请求的路径或凭据被拒"，换 cookie 也不一定解决。
    - **不能归因**到 CDN 边缘节点：`Server: …EdgeOne` / `EO-LOG-UUID` 在 `200`
      响应上同样出现，区分不了「边缘拦截」与「源站拒绝路径/凭据」。当前实现把它们当作
      进入本分支的必要条件，那只是**观测到的伴随特征**，不是归因依据。
    - 「换国内出口」「指向国内中转」**都是未验证假设**，不是处置结论；可验证的对照办法见
      `docs/ARCHITECTURE.md` 第 5 节（在国内机器上三行 curl 即可对照，本机只有一个出口的样本）。
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
