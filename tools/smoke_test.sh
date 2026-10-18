#!/usr/bin/env bash
# 端到端冒烟：拉起 mock 上游 + 网关，跑完 OpenAI/Anthropic × 流式/非流式 + 鉴权用例。
#
#   ./tools/smoke_test.sh              # 默认端口 8899(mock) / 8321(网关)
#   MOCK_PORT=9001 PORT=9002 ./tools/smoke_test.sh
#
# 退出码 0 = 全部通过。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MOCK_PORT="${MOCK_PORT:-8899}"
PORT="${PORT:-8321}"
API_KEY="${API_KEY:-test-key-123}"
BASE="http://127.0.0.1:$PORT"
PASS=0
FAIL=0
MOCK_PID=""
GW_PID=""

# 没有真实凭证时，造一套临时凭证（mock 上游不校验 cookie，token 只要有 blob 就能生成）。
# 这样"clone 下来直接跑冒烟"不需要先搞到酷安账号。
TMP_CRED=""
if [ ! -s secrets/coolapk_cookie.txt ] || [ ! -s secrets/device_code.txt ] || [ ! -s secrets/auth_blob.b64 ]; then
  TMP_CRED="$(mktemp -d)"
  printf 'uid=10000; token=dummy-cookie-for-mock; SESSID=dummy\n' > "$TMP_CRED/cookie.txt"
  printf 'label:testduid0123456789:Google:google:sdk_gphone64_arm64:sdk_gphone64_arm64-userdebug:dev-keys\n' > "$TMP_CRED/device.txt"
  python3 - "$TMP_CRED/blob.b64" <<'PYEOF'
import base64, os, sys
open(sys.argv[1], 'w').write(base64.b64encode(os.urandom(930)).decode())
PYEOF
  export COOLAPK2API_COOKIE_FILE="$TMP_CRED/cookie.txt"
  export COOLAPK2API_DEVICE_FILE="$TMP_CRED/device.txt"
  export COOLAPK2API_BLOB_FILE="$TMP_CRED/blob.b64"
  echo "[i] secrets/ 不全，已生成临时测试凭证于 $TMP_CRED"
fi

cleanup() {
  [ -n "$GW_PID" ] && kill "$GW_PID" 2>/dev/null
  [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null
  wait 2>/dev/null
  [ -n "$TMP_CRED" ] && rm -rf "$TMP_CRED"
}
trap cleanup EXIT

check() { # check <名称> <条件退出码> <详情>
  if [ "$2" -eq 0 ]; then
    printf '  [✓] %s\n' "$1"
    PASS=$((PASS + 1))
  else
    printf '  [✗] %s  %s\n' "$1" "$3"
    FAIL=$((FAIL + 1))
  fi
}

wait_for() { # wait_for <url> <秒>
  for _ in $(seq 1 $((10 * ${2:-15}))); do
    curl -s -o /dev/null -m 1 "$1" && return 0
    sleep 0.1
  done
  return 1
}

echo "== 启动 mock 上游 :$MOCK_PORT =="
python3 tools/mock_upstream.py --port "$MOCK_PORT" >/tmp/coolapk2api-mock.log 2>&1 &
MOCK_PID=$!
wait_for "http://127.0.0.1:$MOCK_PORT/v6/main/init" 15 || { echo "mock 启动失败"; cat /tmp/coolapk2api-mock.log; exit 1; }
echo "   pid=$MOCK_PID"

echo "== 启动网关 :$PORT =="
COOLAPK2API_UPSTREAM="http://127.0.0.1:$MOCK_PORT" \
COOLAPK2API_API_KEY="$API_KEY" \
python3 -m app.main --port "$PORT" >/tmp/coolapk2api-gw.log 2>&1 &
GW_PID=$!
wait_for "$BASE/healthz" 20 || { echo "网关启动失败"; tail -20 /tmp/coolapk2api-gw.log; exit 1; }
echo "   pid=$GW_PID"

echo
echo "== 1. 健康检查与模型列表 =="
OUT=$(curl -s -m 10 "$BASE/healthz"); echo "   $OUT"
echo "$OUT" | grep -q '"ok": *true'; check "healthz ok" $? "$OUT"
OUT=$(curl -s -m 10 -H "Authorization: Bearer $API_KEY" "$BASE/v1/models"); echo "   $OUT"
echo "$OUT" | grep -q 'deepseek-v3'; check "models 列表" $? "$OUT"

echo
echo "== 2. 鉴权 =="
CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST "$BASE/v1/chat/completions" \
  -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"hi"}]}')
[ "$CODE" = "401" ]; check "无 key 返回 401" $? "实际 $CODE"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST "$BASE/v1/chat/completions" \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $API_KEY" \
  -d '{"messages":[{"role":"user","content":"hi"}]}')
[ "$CODE" = "200" ]; check "带 key 返回 200" $? "实际 $CODE"

echo
echo "== 3. OpenAI 非流式 =="
OUT=$(curl -s -m 30 -X POST "$BASE/v1/chat/completions" \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $API_KEY" \
  -d '{"model":"deepseek-v3","messages":[{"role":"system","content":"你是助手"},{"role":"user","content":"你好"}]}')
echo "   ${OUT:0:220}"
echo "$OUT" | grep -q '"object": *"chat.completion"'; check "返回 chat.completion" $? "$OUT"
echo "$OUT" | grep -q 'mock 上游'; check "内容来自上游" $? "$OUT"

echo
echo "== 4. OpenAI 流式 =="
OUT=$(curl -s -N -m 30 -X POST "$BASE/v1/chat/completions" \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $API_KEY" \
  -d '{"model":"deepseek-v3","stream":true,"messages":[{"role":"user","content":"你好"}]}')
echo "$OUT" | head -4 | sed 's/^/   /'
echo "$OUT" | grep -q 'data: \[DONE\]'; check "流以 [DONE] 结束" $? ""
CHUNKS=$(echo "$OUT" | grep -c 'completion.chunk')
[ "$CHUNKS" -ge 3 ]; check "分块数 >= 3（实际 ${CHUNKS}）" $? ""

echo
echo "== 5. Anthropic 非流式 =="
OUT=$(curl -s -m 30 -X POST "$BASE/v1/messages" \
  -H 'Content-Type: application/json' -H "x-api-key: $API_KEY" \
  -d '{"model":"deepseek-v3","max_tokens":512,"messages":[{"role":"user","content":[{"type":"text","text":"你好"}]}]}')
echo "   ${OUT:0:220}"
echo "$OUT" | grep -q '"type": *"message"'; check "返回 message 对象" $? "$OUT"
echo "$OUT" | grep -q '"stop_reason": *"end_turn"'; check "stop_reason=end_turn" $? "$OUT"

echo
echo "== 6. Anthropic 流式 =="
OUT=$(curl -s -N -m 30 -X POST "$BASE/v1/messages" \
  -H 'Content-Type: application/json' -H "x-api-key: $API_KEY" \
  -d '{"model":"deepseek-v3","stream":true,"max_tokens":512,"messages":[{"role":"user","content":"你好"}]}')
echo "$OUT" | head -4 | sed 's/^/   /'
echo "$OUT" | grep -q 'event: message_start'; check "有 message_start" $? ""
echo "$OUT" | grep -q 'event: content_block_delta'; check "有 content_block_delta" $? ""
echo "$OUT" | grep -q 'event: message_stop'; check "有 message_stop" $? ""

echo
echo "== 7. 面板 =="
CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$BASE/")
[ "$CODE" = "200" ]; check "GET / 返回 200" $? "实际 $CODE"
OUT=$(curl -s -m 10 "$BASE/v1/panel/status"); echo "   $OUT"
echo "$OUT" | grep -q '"ok": *true'; check "panel/status ok" $? "$OUT"

echo
echo "== 8. 诊断器 stage 判定（真实 http mock）=="
EDGE_PORT=$((MOCK_PORT + 1))
python3 tools/mock_upstream.py --port "$EDGE_PORT" --mode edge >/tmp/coolapk2api-mock-edge.log 2>&1 &
EDGE_PID=$!
wait_for "http://127.0.0.1:$EDGE_PORT/v6/main/init" 10 || true

OUT=$(COOLAPK2API_UPSTREAM="http://127.0.0.1:$MOCK_PORT" python3 tools/doctor.py 2>&1)
echo "$OUT" | grep -E '可达|通路|结论' | sed 's/^/   /'
echo "$OUT" | grep -q '全链路可用'; check "doctor 指向 http mock → 全链路可用" $? "$OUT"
echo "$OUT" | grep -q 'HTTP 可达'; check "doctor 按实际 scheme/端口探测（非 https:443）" $? "$OUT"

OUT=$(COOLAPK2API_UPSTREAM="http://127.0.0.1:$EDGE_PORT" python3 tools/doctor.py 2>&1)
echo "$OUT" | grep -E '通路|结论' | sed 's/^/   /'
echo "$OUT" | grep -q '边缘拦截'; check "doctor 在 edge 上游 → 正确报边缘拦截（未被网络层短路）" $? "$OUT"
kill "$EDGE_PID" 2>/dev/null; wait "$EDGE_PID" 2>/dev/null

echo
echo "== 9. 鉴权状态如实反映 =="
OUT=$(curl -s -m 10 "$BASE/v1/panel/status")
echo "   $(printf '%s' "$OUT" | head -c 140)"
echo "$OUT" | grep -q '"auth": *"required"'; check "设了 key → panel.auth=required（不再是误导性的变量名）" $? "$OUT"

echo
echo "======================================"
printf '通过 %d / 失败 %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || { echo "--- 网关日志尾部 ---"; tail -30 /tmp/coolapk2api-gw.log; }
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
