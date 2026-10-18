#!/usr/bin/env python3
"""从 CoolApk 的 libauth.so（或 APK）里提取 X-App-Token 需要的 1240 字节选择表。

用法::

    python tools/extract_auth_blob.py --so apktool_out/lib/arm64-v8a/libauth.so
    python tools/extract_auth_blob.py --apk CoolApk-16.6.4-*.apk
    python tools/extract_auth_blob.py --so libauth.so --out secrets/auth_blob.b64

只把这张表（约 1.6KB base64）落到本地 `secrets/`，**不需要也不应该**把 .so 或 APK 放进仓库。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.coolapk import auth  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="提取 auth blob")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--so", help="libauthor.so 路径")
    src.add_argument("--apk", help="CoolApk APK 路径")
    parser.add_argument("--out", default="secrets/auth_blob.b64",
                        help="输出路径（默认 secrets/auth_blob.b64）")
    args = parser.parse_args()

    blob = auth.blob_from_so(args.so) if args.so else auth.blob_from_apk(args.apk)
    # 0x4D8 是 .so 里那段 base64 文本的长度；解码后的二进制通常不足 1024 字节
    # （选择表只用到前 652 字节），所以只校验下界。
    if len(blob) < 0x80 + 100 * 4 + 0x80:
        print(f"[!] 选择表长度异常：{len(blob)} 字节（期望 >= {0x80 + 400 + 0x80}），"
              f"提取位置可能与你的版本不匹配", file=sys.stderr)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(auth.dump_blob(blob), encoding="utf-8")
    print(f"[✓] 已写入 {out}（{len(blob)} 字节，base64 {len(auth.dump_blob(blob))} 字符）")

    # 立刻做一次可用性抽查：能生成 token 才算提取正确
    try:
        token = auth.get_app_token("sample-device", blob)
        print(f"[✓] token 自检通过：{token[:16]}…（长度 {len(token)}）")
    except Exception as exc:  # pragma: no cover - 仅诊断
        print(f"[!] token 自检失败：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
