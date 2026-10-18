#!/usr/bin/env python3
"""分层诊断：一眼看出卡在凭证、网络、边缘拦截还是鉴权。

    python tools/doctor.py            # 用项目 config.yaml / 默认路径
    python tools/doctor.py --deep     # 额外做一次 feed 发现（会消耗一次真实请求）
"""
from __future__ import annotations

import argparse
import socket

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.coolapk import auth  # noqa: E402
from app.coolapk.client import CoolapkClient  # noqa: E402
from app.coolapk.errors import CoolapkError  # noqa: E402

OK, BAD, WARN = "[✓]", "[✗]", "[!]"


def line(mark: str, title: str, detail: str = "") -> None:
    print(f"{mark} {title}" + (f" — {detail}" if detail else ""))


def check_credentials(settings) -> bool:
    ok = True
    cookie, device, blob = settings.cookie_path, settings.device_path, settings.blob_path
    for path, label in ((cookie, "cookie"), (device, "device_code"), (blob, "auth blob")):
        if path.exists() and path.stat().st_size > 0:
            line(OK, f"{label:11} {path} ({path.stat().st_size} B)")
        else:
            line(BAD, f"{label:11} 缺失 {path}")
            ok = False
    if device.exists():
        try:
            dev = auth.decode_device_spec(device.read_text().strip())
            line(OK, "X-App-Device", f"len={len(dev)} {dev[:16]}…")
        except Exception as exc:
            line(BAD, "X-App-Device 解析", str(exc))
            ok = False
    if blob.exists():
        try:
            b = auth.load_blob(blob)
            token = auth.get_app_token("doctor-probe-device", b)
            line(OK, "Token V3", f"{token[:14]}… len={len(token)}")
        except Exception as exc:
            line(BAD, "Token V3 生成", str(exc))
            ok = False
    return ok


def check_network(base: str) -> bool:
    """按 upstream.base 真实的 scheme/端口探测；**失败不短路**后续分层判定。

    历史缺陷：这里曾硬编码 https:443 且失败就 return False —— 于是把 upstream 指向
    http / 非 443 的国内中转时，诊断器会误报 TLS 失败并直接结束，永远走不到 stage 判定，
    等于把 README 里"指个中转"这条排障路径废掉。
    """
    u = urlparse(base)
    host = u.hostname or ""
    scheme = (u.scheme or "https").lower()
    port = u.port or (443 if scheme == "https" else 80)
    probe_url = base.rstrip("/") + "/"

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        line(OK, "DNS", f"{host}:{port} -> {', '.join(ips[:3])}")
    except Exception as exc:
        line(BAD, "DNS", f"{host}: {exc} —— 仍然继续做分层判定")

    label = f"{scheme.upper()} 可达"
    try:
        resp = requests.get(probe_url, timeout=10)
        line(OK, label, f"{probe_url} -> HTTP {resp.status_code} {len(resp.content)}B")
    except requests.exceptions.SSLError as exc:
        line(WARN, "TLS 校验", f"{exc}（继续判定，不阻断）")
    except requests.exceptions.RequestException as exc:
        # 连接被拒/超时同样不阻断：check_api 会给出 stage=network/blocked_path 的明确结论
        line(WARN, label, f"{exc}（继续判定，不阻断）")
    return True


def check_api(client: CoolapkClient, deep: bool) -> int:
    info = client.health(deep=deep)
    if info.get("ok"):
        line(OK, "上游 /v6 通路", f"feed_id={info.get('feed_id', '(未探测)')}")
        return 0

    stage, err = info.get("stage"), info.get("error", "")
    if stage == "blocked_path":
        line(BAD, "上游 /v6 通路", "该路径被拒（403 + 0 字节响应体）")
        print("      判据：403 且响应体为 0 字节 = 路径/凭据被拒。")
        print("      不能用 Server / EO-LOG-UUID 归因 —— 这两个头在 200 响应上同样出现，")
        print("      区分不了「边缘拦截」与「源站拒绝」。")
        print("      优先怀疑 cookie 失效；「换出口」只是待验证的假设（本机为美国出口，")
        print("      是否按地域下发尚未验证）。可选处置：")
        print("        1) 先在 App 内重新登录并导出新 cookie 后重跑本诊断；")
        print("        2) 换到国内网络出口后重跑（用于验证「按地域」这一假设）；")
        print("        3) 把 upstream.base 指向国内中转（见 docs/ARCHITECTURE.md）；")
        print("        4) 用 tools/mock_upstream.py 验证网关自身逻辑。")
    elif stage == "auth":
        line(BAD, "上游鉴权", err)
        print("      cookie 已失效：需在 App 内重新登录并导出 cookie。")
    elif stage == "credentials":
        line(BAD, "本地凭证", err)
    elif stage == "network":
        line(BAD, "网络", err)
    else:
        line(BAD, "上游", f"{stage}: {err}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="coolapk2api 诊断")
    parser.add_argument("--deep", action="store_true", help="额外做一次 feed 发现")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    settings = load_settings(args.config)
    print(f"project  {Path(__file__).resolve().parent.parent}")
    print(f"upstream {settings.upstream.base}")
    print("-" * 60)

    creds_ok = check_credentials(settings)
    check_network(settings.upstream.base)   # 只报信息，不拦截后续分层判定
    if not creds_ok:
        print("-" * 60)
        print("结论：先修好上面标 ✗ 的凭证项，再做上游探测。")
        return 1

    client = CoolapkClient(settings)
    code = check_api(client, args.deep)
    print("-" * 60)
    print("结论：" + ("全链路可用。" if code == 0 else "网关侧代码无问题，上游通路待解决（见上）。"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
