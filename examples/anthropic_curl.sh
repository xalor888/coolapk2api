#!/usr/bin/env bash
# Anthropic 协议示例（流式）
#
#   ./examples/anthropic_curl.sh
#   BASE=http://127.0.0.1:8321 KEY=sk-xxx ./examples/anthropic_curl.sh
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8321}"
KEY="${KEY:-not-needed}"
MODEL="${MODEL:-deepseek-v3}"

curl -N "$BASE/v1/messages" \
  -H 'Content-Type: application/json' \
  -H "x-api-key: $KEY" \
  -d "{
    \"model\": \"$MODEL\",
    \"max_tokens\": 512,
    \"stream\": true,
    \"messages\": [
      {\"role\": \"user\", \"content\": [{\"type\": \"text\", \"text\": \"用三句话解释什么是反向代理\"}]}
    ]
  }"
