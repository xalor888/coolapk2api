"""X-App-Token（Token V3）生成与 auth blob 加载。

算法来源：coolapk-desktop 的 Rust signer（auth.rs，已对线上接口验证），
并用 CoolApk 16.6.4 arm64 `libauth.so` 的反汇编交叉验证（getToken @ 0xd3a44）。

要点：
1. `libauth.so` 偏移 0x408e6 起 0x4d8 字节是一张选择表，base64 存储、逐字节 XOR 0x5A。
   我们只需要这张表（1240 字节约 1.6KB base64），**不需要分发整个 .so 或 APK**。
2. 每个时间戳 t 取表中 `((t + app_code) % 100) * 4 + 0x80` 处 0x80 字节，base64 解码后
   拼进明文，再走 md5 / bcrypt 得到 token。
3. 盐值有 1/64 概率落在 bcrypt 字母表外（字符 '+'），此时向前试下一秒即可。
"""
from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path

from .errors import CredentialMissing

APP_ID = "com.coolapk.market"
APP_CODE = 2_609_291  # 16.6.4

# libauth.so 中的选择表位置（CoolApk 16.6.4 arm64）
BLOB_OFFSET = 0x408E6
BLOB_LENGTH = 0x4D8
XOR_KEY = 0x5A

STD64 = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
BC64 = b"./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


# ---------------------------------------------------------------- blob
def blob_from_so(path: str | Path) -> bytes:
    """从 `libauth.so` 原文提取选择表（XOR 后）。"""
    data = Path(path).read_bytes()
    raw = data[BLOB_OFFSET:BLOB_OFFSET + BLOB_LENGTH]
    if not raw:
        raise CredentialMissing(f"{path} 太小，读不到 0x{BLOB_OFFSET:x} 处数据")
    encoded = raw.split(b"\x00")[0] + b"==="
    return bytes(b ^ XOR_KEY for b in base64.b64decode(encoded))


def blob_from_apk(path: str | Path) -> bytes:
    """从 APK 的 `lib/arm64-v8a/libauth.so` 提取选择表。"""
    import zipfile

    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.endswith("libauth.so")]
        names.sort(key=lambda n: ("arm64" not in n, n))
        if not names:
            raise CredentialMissing(f"{path} 里没有 libauth.so")
        raw = zf.read(names[0])
    encoded = raw[BLOB_OFFSET:BLOB_OFFSET + BLOB_LENGTH].split(b"\x00")[0] + b"==="
    return bytes(b ^ XOR_KEY for b in base64.b64decode(encoded))


def dump_blob(blob: bytes) -> str:
    return base64.b64encode(blob).decode()


def load_blob(path: str | Path) -> bytes:
    """读取 base64 保存的选择表。"""
    p = Path(path)
    if not p.exists():
        raise CredentialMissing(
            f"缺少 auth blob：{p}\n"
            "  生成方式：python tools/extract_auth_blob.py --so <libauth.so> "
            "或 --apk <CoolApk.apk>"
        )
    text = p.read_text(encoding="utf-8").strip()
    return base64.b64decode(text)


# ---------------------------------------------------------------- token
def _md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _decode22(s: str) -> bytes:
    bits = 0
    for ch in s[:22].encode():
        try:
            v = BC64.index(ch)
        except ValueError as exc:
            raise ValueError(f"盐字符 {chr(ch)!r} 不在 bcrypt 字母表内") from exc
        bits = (bits << 6) | v
    return (bits >> (22 * 6 - 128)).to_bytes(16, "big")


def _encode16(salt: bytes) -> bytes:
    n = int.from_bytes(salt, "big")
    out = bytearray()
    for i in range(21):
        out.append(BC64[(n >> (122 - 6 * i)) & 0x3F])
    out.append(BC64[(n & 0x3) << 4])
    return bytes(out)


def get_token(device_code: str, blob: bytes, ts: int | None = None,
              app_code: int = APP_CODE) -> str:
    """按指定时间戳生成一个 Token V3。盐位非法时抛 ValueError。"""
    import bcrypt

    t = int(time.time()) if ts is None else int(ts)
    idx = ((t + app_code) % 100) * 4 + 0x80
    segment = base64.b64decode(blob[idx:idx + 0x80] + b"===")

    plain = (f"{APP_ID}&{segment.decode('utf-8', 'replace')}&"
             f"{_md5_hex(device_code.encode())}&{t}&{app_code}")
    password = _md5_hex(base64.b64encode(plain.encode()))

    salt_src = base64.b64encode(f"{t:x}/{_md5_hex(plain.encode())}".encode())
    salt22 = salt_src.rstrip(b"=").decode()[:22]
    # 末位左移 5 位，使 22 字符按 bcrypt 字母表解码后正好得到目标 16 字节盐
    i = STD64.index(ord(salt22[21]))
    salt22 = salt22[:21] + chr(STD64[(i - 5) % 64])

    salt = b"$2y$04$" + _encode16(_decode22(salt22))
    hashed = bcrypt.hashpw(password.encode(), salt)
    return "v3" + base64.b64encode(hashed).rstrip(b"=").decode()


def get_app_token(device_code: str, blob: bytes, now: int | None = None,
                  attempts: int = 16) -> str:
    """运行时入口：盐位非法就向前试下一秒。"""
    base = int(time.time()) if now is None else int(now)
    last: Exception | None = None
    for off in range(attempts):
        try:
            return get_token(device_code, blob, base + off)
        except ValueError as exc:  # 仅盐位问题才值得重试
            last = exc
            continue
    raise ValueError(f"token 生成失败：{last}")


# ---------------------------------------------------------------- device
def decode_device_spec(spec: str) -> str:
    """设备串 -> X-App-Device。

    输入：``<duid>;<manu>;<brand>;<model>;<display>;<oaid>``（也接受带前导字段的旧格式）
    输出：base64(``{duid}; ; ; ; {manu}; {brand}; {model}; {display}; {oaid}``) 反转去 '='
    """
    parts = [p.strip() for p in spec.strip().split(":")]
    # 兼容两种写法：
    #   7 段：<标签>:<duid>:<厂商>:<品牌>:<机型>:<显示>:<oaid>   （App 抓包里的原样）
    #   6 段：<duid>:<厂商>:<品牌>:<机型>:<显示>:<oaid>         （手工精简）
    if len(parts) >= 7:
        _, duid, manu, brand, model, display, oaid = parts[:7]
    elif len(parts) == 6:
        duid, manu, brand, model, display, oaid = parts
    else:
        raise CredentialMissing(
            "device_code 格式不对：需要 6 段（duid:厂商:品牌:机型:显示:oaid）"
            f"或 7 段（前缀标签 + 上述 6 段），实际 {len(parts)} 段"
        )
    plain = f"{duid}; ; ; ; {manu}; {brand}; {model}; {display}; {oaid}"
    return base64.b64encode(plain.encode()).decode()[::-1].replace("=", "")
