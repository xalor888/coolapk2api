# coolapk2api

把**酷安 App 内置的 AI 助手**（DeepSeek）包装成一个标准的 **OpenAI / Anthropic 兼容 API 网关**，
让你能在任意支持自定义 base_url 的客户端里用它：OpenAI SDK、Anthropic SDK、Claude Code、
各类 Chat 前端、自己的脚本。

- 一个进程，两个协议：`POST /v1/chat/completions` 与 `POST /v1/messages`
- 流式（SSE）与非流式都支持
- 配置全走 `config.yaml` / 环境变量，敏感信息隔离在 `secrets/`
- 自带诊断器与本地 mock，上游不通时也能验证网关本身

> ⚠️ 仅供**学习与个人测试**。酷安 AI 属于官方服务，请遵守其用户协议；本项目不提供任何破解内容，
> 也不绕过任何付费。仓库内**不含**任何 APK、`.so`、cookie、token 或密钥。

---

## 1. 它解决什么问题

酷安 App 里有一个 AI 助手（`/v6/ai/feedAsk`，SSE 流式），但它绑在 App 内、需要 App 的签名头
（`X-App-Token`）和登录 cookie。本项目做三件事：

1. **签名与鉴权**：本地复刻 `X-App-Token`（Token V3）生成算法，读取你自己的 cookie。
2. **协议翻译**：把 OpenAI / Anthropic 的多轮 messages 压平成一条 prompt（上游只收单条），
   再把上游的 SSE 事件翻译回两个协议各自的流式格式。
3. **上下文复用**：自动找一个带 `aiAccessKey` 的帖子作为「对话容器」，并缓存复用。

## 2. 架构

```
客户端(OpenAI SDK / Claude Code / curl)
        │  /v1/chat/completions 或 /v1/messages
        ▼
┌───────────────────────────────────────────────┐
│ app/main.py        FastAPI 路由 + 鉴权        │
│   ├─ adapters/openai.py      协议翻译(OpenAI) │
│   ├─ adapters/anthropic.py   协议翻译(Claude) │
│   └─ adapters/prompt.py      messages→prompt  │
│ app/coolapk/client.py   头部/发现/SSE 解析    │
│ app/coolapk/auth.py     Token V3 + blob       │
│ app/config.py           yaml + env            │
└───────────────────────────────────────────────┘
        │  /v6/search, /v6/feed/detail, /v6/ai/feedAsk
        ▼
   api.coolapk.com（TencentEdgeOne 边缘）
```

细节见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，逆向由来见 [`docs/reverse-notes.md`](docs/reverse-notes.md)。

## 3. 快速开始

```bash
# 1) 依赖（Python >= 3.10）
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) 配置
cp config.example.yaml config.yaml      # 已有默认值，可先不改

# 3) 三份本地凭证（都在 secrets/ 下，已被 .gitignore 排除）
mkdir -p secrets
cp /path/to/coolapk_cookie.txt  secrets/    # 登录后的 cookie
cp /path/to/device_code.txt     secrets/    # 设备串
python tools/extract_auth_blob.py --so /path/to/libauth.so   # 生成 secrets/auth_blob.b64

# 4) 自检（会告诉你卡在哪一层）
python tools/doctor.py

# 5) 启动
python -m app.main                # 或 ./run.sh
# -> http://127.0.0.1:8321
```

浏览器打开 `http://127.0.0.1:8321` 会看到一个内置的聊天面板。

### 三份凭证分别是什么

| 文件 | 内容 | 怎么来 |
|---|---|---|
| `secrets/coolapk_cookie.txt` | 登录后的 Cookie 头（`uid`/`token`/`SESSID` 等） | 在酷安 App / 网页登录后抓包或导出 |
| `secrets/device_code.txt` | `duid:厂商:品牌:机型:显示:oaid` 一行 | 从你自己设备的抓包头 `X-App-Device` 反推，或复用 App 的同等值 |
| `secrets/auth_blob.b64` | 1240 字符 base64（XOR 后的选择表，930 字节） | `tools/extract_auth_blob.py` 从 `libauth.so` 提取 |

`auth_blob` 只是 `X-App-Token` 算法里的**一张选择表**，不含账号信息；提取脚本支持直接读 APK
（`--apk CoolApk-16.6.4-*.apk`），但**不要把 APK 提交进仓库**。

## 4. 配置

`config.yaml`（可选，缺省值即可跑）与**环境变量**都支持，环境变量优先。

| 配置项 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| `server.host` | `COOLAPK2API_HOST` | `127.0.0.1` | 监听地址，默认只本机 |
| `server.port` | `COOLAPK2API_PORT` | `8321` | 端口 |
| `server.api_key` | `COOLAPK2API_API_KEY` | 空 | 设置后请求必须带 `Authorization: Bearer <key>` 或 `x-api-key` |
| `upstream.base` | `COOLAPK2API_UPSTREAM` | `https://api.coolapk.com` | 上游根地址，可指向国内中转 |
| `upstream.discovery_query` | `COOLAPK2API_DISCOVERY_QUERY` | `MIUI` | 找 AI 入口帖子的搜索词 |
| `upstream.context_ttl` | `COOLAPK2API_CONTEXT_TTL` | `3600` | feed/accessKey 缓存秒数 |
| `upstream.request_timeout` | `COOLAPK2API_REQUEST_TIMEOUT` | `15` | 单次请求超时 |
| `upstream.stream_timeout` | `COOLAPK2API_STREAM_TIMEOUT` | `90` | 流式读取超时 |
| `models.available` | — | `deepseek-v3`,`deepseek-r1` | 对外暴露的模型名 |
| `models.default` | `COOLAPK2API_DEFAULT_MODEL` | `deepseek-v3` | 未知模型回落到它 |
| `credentials.*_file` | `COOLAPK2API_COOKIE_FILE` 等 | `secrets/...` | 凭证路径 |
| `logging.level` | `COOLAPK2API_LOG_LEVEL` | `info` | 日志级别 |

## 5. 用法示例

### OpenAI SDK（Python）

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8321/v1", api_key="任意非空或你的 PROXY_API_KEY")
resp = client.chat.completions.create(
    model="deepseek-v3",
    messages=[{"role": "user", "content": "用三句话解释什么是反向代理"}],
)
print(resp.choices[0].message.content)
```

### Anthropic SDK（Python）

```python
import anthropic

client = anthropic.Anthropic(base_url="http://127.0.0.1:8321", api_key="任意非空")
msg = client.messages.create(
    model="deepseek-v3", max_tokens=512,
    messages=[{"role": "user", "content": "用三句话解释什么是反向代理"}],
)
print(msg.content[0].text)
```

### curl

```bash
curl -N http://127.0.0.1:8321/v1/chat/completions \
  -H 'Content-Type: application/json' -H 'Authorization: Bearer your-key' \
  -d '{"model":"deepseek-v3","stream":true,
       "messages":[{"role":"user","content":"你好"}]}'
```

更多见 [`examples/`](examples/)：`openai_curl.sh`、`anthropic_curl.sh`、`openai_python.py`、`claude_config.json`。

### 接到 Claude Code / Claude Desktop

把 `examples/claude_config.json` 里的 `ANTHROPIC_BASE_URL` 指向网关即可（Anthropic 协议已适配）。

## 6. 验证

```bash
# 不依赖酷安的完整回归：拉起 mock 上游 + 网关，跑 15 项断言
bash tools/smoke_test.sh
```

**不需要先有酷安账号**：脚本发现 `secrets/` 不全会自造一套临时测试凭证（mock 上游不校验登录态），
所以 `git clone` 后直接跑就能看到 15/15。

覆盖：healthz、模型列表、鉴权 401/200、OpenAI 非流式/流式、Anthropic 非流式/流式、面板。

```bash
# 真实上游分层诊断
python tools/doctor.py --deep
```

诊断分四层：凭证 → DNS/TLS → `/v6` 通路 → feed 发现，并区分三种失败：
边缘拦截（403 空响应）、鉴权失效（200 + `status:403`）、网络错误。

## 7. 已知限制

1. **出口 IP 可能被边缘节点拦**。酷安 `api.coolapk.com/v6/*` 走 TencentEdgeOne，
   海外/机房 IP 常被路径级规则 403（空响应），与 cookie 新旧无关。
   本机实测就是这种情况；**换国内出口**或把 `upstream.base` 指向一台国内中转即可，网关代码无需改。
   诊断器会明确报 `stage: edge`。
2. **上游只收单条 prompt**。多轮对话是压平成 `[user]/[assistant]` 文本段传过去的，
   所以模型没有真正的多轮记忆，只是"读到了历史文本"。
3. **usage 是估算值**（按字符数折算），上游不给 token 数。
4. **`deepseek-r1` 的推理过程**在 OpenAI 协议里塞进 `reasoning_content`；
   Anthropic 协议里目前不透出（会作为普通文本流式输出）。
5. **AI 入口是灰度的**。`feedAsk` 依赖帖子上的 `aiAccessKey`；如果搜不到，会自动换帖子重试，
   但全部失败时会返回 503 `upstream_unavailable`。
6. **模型名只做前缀白名单**（`deepseek*`），不会因未知模型报错，而是回落默认值。

## 8. 目录结构

```
coolapk2api/
├── app/
│   ├── main.py            FastAPI 路由、鉴权、错误映射
│   ├── config.py          config.yaml + 环境变量
│   ├── coolapk/
│   │   ├── auth.py        Token V3、blob 提取、设备串编码
│   │   ├── client.py      请求头、feed 发现、feedAsk SSE 解析、自检
│   │   └── errors.py      可操作的异常分类
│   ├── adapters/
│   │   ├── openai.py      OpenAI 协议出/入
│   │   ├── anthropic.py   Anthropic 协议出/入
│   │   └── prompt.py      messages → 单条 prompt
│   └── static/panel.html  内置聊天面板
├── tools/
│   ├── doctor.py          分层诊断
│   ├── extract_auth_blob.py  blob 提取
│   ├── mock_upstream.py   本地 mock 上游（ok / edge / auth 三种模式）
│   └── smoke_test.sh      15 项端到端断言
├── examples/              curl / python / Claude 配置
├── docs/                  架构与逆向说明
├── config.example.yaml
├── requirements.txt
└── run.sh
```

## 9. 安全与合规

- `.gitignore` 已排除：`secrets/`、`*.apk`、`*.so`、`*.dex`、`auth/`、`build/`、`*.cookie.txt`。
- 网关默认只监听 `127.0.0.1`；要暴露到局域网请同时设置 `server.api_key`。
- 本项目不含、也不分发酷安的任何二进制文件；`auth_blob` 由用户从自己持有的安装包中提取。
- 由本项目产生的任何使用后果由使用者自负。
