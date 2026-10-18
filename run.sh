#!/usr/bin/env bash
# 一键启动：装依赖（缺失时）→ 检查凭证 → 拉起网关 → 打印用法
#
#   ./run.sh              # 默认 127.0.0.1:8321
#   ./run.sh --port 9000
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8321}"
for arg in "$@"; do
  case "$arg" in
    --port=*) PORT="${arg#*=}" ;;
    --port) shift; PORT="${1:-$PORT}" ;;
  esac
done

echo "== coolapk2api =="

python3 -c "import fastapi, uvicorn, requests, bcrypt" 2>/dev/null || {
  echo "[*] 安装依赖…"
  python3 -m pip install -q -r requirements.txt
}

# 凭证检查（缺哪个说哪个，别让人猜）
missing=0
for f in secrets/coolapk_cookie.txt secrets/device_code.txt secrets/auth_blob.b64; do
  [ -s "$f" ] || { echo "[!] 缺少 $f"; missing=1; }
done
if [ "$missing" -eq 1 ]; then
  echo "    见 README「快速开始」第 3 步；blob 用 tools/extract_auth_blob.py 生成。"
  exit 1
fi

# 清掉占用端口的旧实例
if command -v lsof >/dev/null; then
  PID=$(lsof -ti:"$PORT" 2>/dev/null || true)
  [ -n "$PID" ] && { echo "[*] 结束旧实例 PID $PID"; kill "$PID" 2>/dev/null || true; sleep 1; }
fi

echo "[*] 启动于 http://127.0.0.1:$PORT"
command -v open >/dev/null && open "http://127.0.0.1:$PORT/" 2>/dev/null || true

exec python3 -m app.main --port "$PORT"
