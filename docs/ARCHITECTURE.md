# 架构说明

## 1. 一次请求的完整链路

```
POST /v1/chat/completions
  │
  ├─ main.py::authorized()          可选 API key 校验
  ├─ adapters/prompt.flatten()      多轮 messages → 单条 prompt
  ├─ coolapk/client.ask(prompt)     ┐
  │    ├─ context()                 │ feed_id + aiAccessKey，缓存 1h
  │    │    ├─ POST /v6/search       │ 找热门帖
  │    │    └─ POST /v6/feed/detail  │ 逐个探 aiAccessKey
  │    ├─ headers()                 │ X-App-Token（每 20s 刷新）+ Cookie + 16 个 App 头
  │    └─ POST /v6/ai/feedAsk       │ SSE: {"data":{"entityType":…,"text":…}}
  └─ adapters/{openai,anthropic}    把 (kind,text) 流转成目标协议的 SSE / JSON
```

关键点：**上游 SSE 与两个对外协议都不是一一对应的**。

| 上游事件 | 含义 | 网关动作 |
|---|---|---|
| `ai_prompt` | 开始（占位） | 忽略；OpenAI 侧在第一个 delta 时才发 `role` 首块 |
| `ai_prompt_answer_stream` | 增量文本 | 直接作为 delta 下发 |
| `ai_prompt_answer` | 全量文本 | 仅在**没有任何增量**时兜底（防止上游退化成非流式） |
| `finish_reason=finish` | 结束 | 结束流：OpenAI 发 `finish_reason:stop` + `[DONE]`；Anthropic 发 `content_block_stop` → `message_delta` → `message_stop` |

## 2. X-App-Token（Token V3）

```
blob = libauth.so[0x408e6 : 0x408e6+0x4d8]   # base64 文本
blob = base64decode(blob) XOR 0x5A           # → 930 字节选择表

idx     = ((t + 2609291) % 100) * 4 + 0x80
segment = base64decode(blob[idx : idx+0x80] + "===")

plain    = f"com.coolapk.market&{segment}&{md5(device)}&{t}&2609291"
password = md5(base64(plain))

salt22 = base64(f"{t:x}/{md5(plain)}")[:22]  # 末位左移 5，落进 bcrypt 字母表
hash   = bcrypt(password, "$2y$04$" + bcrypt_encode16(bcrypt_decode22(salt22)))
token  = "v3" + base64(hash)                 # 82 字符
```

- 选择表有 100 个槽位（按秒轮转），**每 100 秒复用一轮**。
- 盐位有约 1/64 概率落到 `+`（bcrypt 字母表外），此时向前试下一秒，最多 16 次。
- 只需 930 字节表，**不需要 `.so` 或 APK**：`tools/extract_auth_blob.py` 一次性提取后落 `secrets/`。

## 3. 请求头

16 个头部，其中三个是硬门槛：`X-App-Token`（上面）、`X-App-Device`（设备串 base64 反转）、
`Cookie`（登录态）。其余是版本/渠道标识，见 `client.headers()`。

`X-App-Device = reverse(base64("{duid}; ; ; ; {manu}; {brand}; {model}; {display}; {oaid}"))`
去掉 `=` 与换行，长度通常 200 左右。

## 4. 错误分类（为什么值得单独设计）

上游的失败形态至少有四种，混成一个 500 会让排查无从下手：

| 现象 | 判定 | 对外 |
|---|---|---|
| `403` + 空体 + `Server: TencentEdgeOne`（带 `EO-LOG-UUID`） | `EdgeBlocked` | 502 `upstream_unreachable` |
| `200` + `{"status":403,"message":"API unsupported."}` | `AuthExpired` | 502 `upstream_auth` |
| 其他非 200 | `UpstreamHTTP` | 502 `upstream_error` |
| 搜不到带 `aiAccessKey` 的帖子 | `FeedUnavailable` | 503 `upstream_unavailable` |
| 本地缺 cookie/blob | `CredentialMissing` | 500 `configuration_error` |

第一条尤其重要：**403 空响应不是 cookie 过期**，换 cookie 也没用，只能换出口 IP。
诊断器 `tools/doctor.py` 会把这一条单独报出来。

## 5. 出口 IP 与「国内中转」方案

`api.coolapk.com/v6/*` 由 TencentEdgeOne 承载。**在本机这个美国出口（38.146.28.52）上实测被拦**
（403 + 0 字节响应体）；**是否按地域下发尚未验证** —— 本轮没有任何其他出口的对照数据。
`Server: TencentEdgeOne` 与 `EO-LOG-UUID` 在 200 响应上同样出现，不能用于归因。
验证方式见 `state/research/coolapk-route-options.md` 第四节（在国内机器上三行 curl 即可对照）。
如果你的机器不在国内网络里，有两种做法（都不需要改网关代码）：

### 方案 A：本机挂国内出口

把系统代理指向一台国内节点即可。网关走 `requests`，会读 `HTTPS_PROXY`/`ALL_PROXY`：

```bash
HTTPS_PROXY=http://127.0.0.1:7890 python -m app.main
```

### 方案 B：设 `upstream.base` 指向国内中转

在一台国内 VPS 上放一个只做透传的反代（保留原始 Host、头部、SSE 流式）：

```nginx
# 国内 VPS: nginx 透传
server {
    listen 443 ssl;
    server_name relay.example.com;
    location / {
        proxy_pass https://api.coolapk.com;
        proxy_set_header Host api.coolapk.com;
        proxy_ssl_server_name on;
        proxy_buffering off;          # 关键：SSE 必须关缓冲
        proxy_read_timeout 300s;
    }
}
```

然后：

```bash
COOLAPK2API_UPSTREAM=https://relay.example.com python -m app.main
```

注意：网关会拼接 `upstream.base + /v6/...`，所以中转必须保留路径前缀。

## 6. 为什么有 mock 上游

上游可能因为出口、灰度、风控临时不可用。`tools/mock_upstream.py` 用标准库复刻了三个真实行为
（`ok` / `blocked_path` / `auth`），让**网关自身的协议转换永远可回归**——这样"上游挂了"和"代码坏了"
在测试层面就能分开。`tools/smoke_test.sh` 的断言全部基于它（条数以脚本末尾 `通过 n` 为唯一出处）。
