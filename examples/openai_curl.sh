#!/usr/bin/env bash
# OpenAI 协议示例（流式）
#
#   ./examples/openai_curl.sh                       # 用默认端口与无鉴权
#   BASE=http://127.0.0.1:8321 KEY=sk-xxx ./examples/openai_curl.sh
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8321}"
KEY="${KEY:-}"
MODEL="${MODEL:-deepseek-v3}"

AUTH=()
[ -n "$KEY" ] && AUTH=(-H "Authorization: Bearer $KEY")

curl -N "$BASE/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  "${AUTH[@]}" \
  -d "{
    \"model\": \"$MODEL\",
    \"stream\": true,
    \"messages\": [
      {\"role\": \"system\", \"content\": \"你是一个简洁的助手\"},
      {\"role\": \"user\", \"content\": \"用三句话解释什么是反向代理\"}
    ]
  }"
