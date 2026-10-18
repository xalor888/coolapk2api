# 逆向笔记（CoolApk 16.6.4）

> 来源：本地项目 `coolapk反代/ANALYSIS.md`（2026-10-07），为可公开的技术过程记录；
> 不包含样本二进制。此处保留是为了说明 Token V3 与请求头的由来。

# CoolApk 16.6.4 APK 逆向分析报告

**样本**: `CoolApk-16.6.4-2609291-coolapk-arm64-sign.apk` (117 MB)
**包名**: `com.coolapk.market` | versionCode 2609291 | versionName 16.6.4
**目标**: minSdk 24 / targetSdk 34 / compileSdk 37 | arm64-v8a only | 单 dex 文件 (102,655,212 B)

---

## 1. 加固层：网易易盾 (NetEase Yidun)

APK 被 **网易易盾加固**，特征是：

| 信号 | 值 |
|---|---|
| Application | `com.netease.nis.wrapper.MyApplication` |
| 解壳库 | `lib/arm64-v8a/libnesec.so` + `libnesec-x86.so` |
| classes.dex | 30 个类 / 980 个字符串——**壳**。真业务 dex 被加密后拼在壳 dex 尾部(头声明 file_size=102MB，实际 30 类) |
| assets | `zlsioh.dat` (56 KB 加密附属配置), `nedata.db` |

壳 dex 头部(class_defs=30, string_ids=980)之后 ~102MB payload 在磁盘上是**打块 + 洗牌**存储：
- 真 dex 的头部(魔数/map)被抹掉，body(ids 表 + data)保留但**表被拆散交错**；
- 启动后由 libnesec.so 解密并注册给 ART。

### 1.1 脱壳方法(动态)

模拟器(Android 16, arm64, root)上最有效的一步——

> **Hook `libart.so` 的 `ClassLinker::DefineClass`**（符号 `_ZN3art11ClassLinker11DefineClassEPNS_6ThreadEPKcmmNS_6HandleINS_6mirror11ClassLoaderEEERKNS_7DexFileERKNS_3dex8ClassDefE` 在 libart 符号表里，非导出表）。

每个类定义时，args[6] = `DexFile&`(**注意 Android 16 上指针带 PAC 高位标记，需 `& 0xFFFFFFFFFFFF` 剥掉**)，从 DexFile 对象 `+8` 读 `begin_`、dex 头 `+0x20` 读 size，按 `begin_` 去重后整包取回。

关键坑：
- frida 17 的 JS 运行时**没有内置 Java bridge**（hook MD5 输入那条路走不通）；Java bridge 要用 frida 16，但 16.x **不支持 Android 16**（spawn 报 `system_server not found`）；
- Yidun 反 frida：spawn 后 ~60–110s 会被杀并自重启；**纯 root + SIGSTOP 冻结**可行；
- 懒加载：dex 不是启动全量解密，**monkey 驱动 UI 4 分钟后再冻结 dump** 才能拿到全部。

### 1.2 静态重建(备选路线)

`multi_rebuild.py` / `decompiled 流程`：对固化的进程内存(或原文件)，用 numpy 扫"递增 u32 游程 + delta 投票"定位每个子 dex 的 string_ids 表，向前/向后走 type/proto/field/method/class_defs 边界，合成 dex header + map_list。dex1(16,707 类，Compose 图标库)完全成功；后续 dex 的字符串表被拆分交错，需要各自解洞。

---

## 2. 脱壳产物

| 项 | 数量 |
|---|---|
| 真 dex (运行时 dump) | **24 个** |
| 类总数 | ~91K(静)/ 更大（含懒加载） |
| jadx 反编译源码 | **77,837 个 .java**（`jadx_all/`） |
| 壳 dex 重建 | 1 个（16,707 类） |

关键 dex:
- `kd/dump_001_9857970.dex` — 含 `AppConfig`(C4874) 与 **X-App-Token** 逻辑
- `kd/dump_003_10947436.dex` — `com.coolapk.market.model/view/goods` 主业务代码
- `kd/dump_008_8314924.dex` — **`com.coolapk.market.util.AuthUtils`** (native token 入口)

---

## 3. 网络层 / 签名逻辑

### 3.1 请求头（`AppConfig.m33220` → `ImageCoolapkHeaderInterceptor`）

对 `*.coolapk.com` 的请求注入：

```
User-Agent, X-Requested-With=XMLHttpRequest, X-Sdk-Int, X-Sdk-Locale,
X-App-Id=com.coolapk.market, X-App-Token=<v3开头的token>,
X-App-Version=16.6.4, X-App-Code=2609291, X-Api-Version=16,
X-App-Device=<base64(反向)(设备串)>,
X-Dark-Mode, X-App-Channel, X-App-Mode, X-App-Supported
```

API host: `api.coolapk.com`（`/v6/` 前缀），部分接口走 `api-live.coolapk.com`。
`X-App-Device` = Base64(`{deviceId}; ; ; ; {manufacturer}; {brand}; {model}; {display}; {oaid}`) 反转、去换行/`=`。

### 3.2 X-App-Token = native `AuthUtils.getToken(Context, deviceId)`

```java
// com.coolapk.market.util.AuthUtils
System.loadLibrary(Context.AUTH_SERVICE);          // libauth.so (915 KB, arm64)
public final native String getToken(Context context, String deviceId);
```

**Token V3 算法**（源自 libauth.so `getToken@0xd3a44` arm64 反汇编，并与第三方客户端
coolapk-desktop 的 Rust 实现逐字节对齐验证；该实现对战线上 API 有效）：

```
device_id ← 数盟 DUID（见 §3.4），auth_blob ← libauth.so[0x4086..]: xor(b64decode(blob), 0x5A)
                                             （930 B，按 base64 字母表的选择表）

idx      = ((timestamp + versionCode) % 100) * 4 + 0x80
segment  = base64_decode(auth_blob[idx : idx+0x80])
plain    = pkg + "&" + segment(utf8) + "&" + md5(device_id) + "&" + str(ts) + "&" + str(versionCode)
password = md5_hex(base64(plain))
salt_src = base64( ts_hex + "/" + md5_hex(plain) ).rstrip("=")
salt22   = salt_src[:22]，最后一字符按 std-b64 字母表 **-5 位移**（规避 '+'/字母表不一致）
hash     = bcrypt(password, cost=4, salt=salt22) → "$2y$04$…"（60 字符）
TOKEN    = "v3" + base64(hash).rstrip("=")
```

**等价的 Python 实现在 `coolapk_token.py`**（自测：170/200 的秒位可直生，
其余秒位命中盐里的 `'+'` 字符，按 upstream 同样处理——向前探测 ≤15 秒）。

误差来源与取舍：
- `versionCode` 参与 seed 与 plain；本生成器默认 `2609291`(16.6.4)。
  coolapk-desktop 用的是 `2604201`(16.2.0)，指出 server 不严格校验版本自洽；
  代理场景跟随所冒充版本的 `X-App-Code` 即可。
- bcrypt 字母表与 std-base64 的 `+` 冲突是**已知且有意的机制**：那一秒的 token
  本来就“不可生成”，客户端会等下一秒——不是 bug。

### 3.3 (过时说明) 我最初的 asm 直读

初版直读时曾误判两处，Rust 实现予以纠正：plain 的完整格式是
`pkg & segment & md5(device) & ts & vc`（我最初漏了 pkg/segment 前缀），
salt 来源是 `hex(ts)/md5(plain)` 而非 segment。修正版即上面的最终算法。

### 3.4 设备码（device_id）= 数字联盟 DUID

设备码不是随便造的——它就是**数盟 DUID**(shuzilm)，由 `libdu.so` 向
`auni.telecome.cn/a/mdna/report` 注册获得。这条链路已被完整逆向
（**[kongwufang/szlm_res](https://github.com/kongwufang/szlm_res)**）：

- body = zlib.compress(JSON) XOR POOL_KEY（libdu.so 常量，如 `0x110DC8`）
- 服务端只强制校验 6 个字段（`DUv/3mS/2cO/AAA/75c/URL.v`），设备识别只看三元组
  `(sfOo, foO, 2cO)`，**协议开放**：完整 body + 新三元组即可注册新 DUID
- 结论：逆向代理的设备码 = 直接注册一个 DUID（`tools/getduid.py -r`），
  无需真机

即反代完整链路：**注册 DUID（数盟协议）→ 拼 device_code → Token V3（bcrypt）**。

---

## 4. AI 助手反代(重点)

### 4.1 功能形态

酷安 AI 助手 = **基于 DeepSeek 的 feed 上下文问答**：在动态/文章详情页的悬浮
「问问 AI」按钮进入(`FeedDetailFragmentV8.setupFloatingAiButton` →
`ChatToAiViewModel`)，后端是 DeepSeek(dex 内有 `deepseek-v3` / `deepseek-r1` /
"Powered by DeepSeek")。

### 4.2 接口(Retrofit 接口 `ym1`,baseUrl = `https://api.coolapk.com/v6/`)

```
POST /v6/ai/feedAsk
     ?feed_id={id}&model=deepseek-v3&incremental_output=1
     &stream_output=1&is_retry=0&aiAccessKey={key}
     body(prompt): prompt={问题}                @Streaming → SSE 流
GET  /v6/ai/feedAskHistoryList?feed_id={id}    历史记录
```

- **SSE 事件**：`{type, content, id, recommendPrompt}`，type ∈
  `LOADING / THINKING / THINKING_DONE(深度思考) / ANSWER / DONE / ERROR`
- **aiAccessKey**：来自 feed 详情接口 `POST /v6/feed/detail?id=` 的
  `aiAccessKey` 字段(`Feed.aiAccessKey`,SerializedName 同名)；首页列表不带
- **model**：`deepseek-v3`(另见 `deepseek-r1`)

### 4.3 鉴权(已实测跑通)

三层，缺一不可：

1. **X-App-Token(V3)**：token 的 md5 输入 = **X-App-Device 头本身**
   (逆序 base64 设备串)，不是裸设备 ID
2. **登录 Cookie**：`uid + token + username`(token 在 App 的
   `shared_prefs/coolapk_preferences_v7.xml`,非 WebView store)
3. **设备一致性**(风控关键)：`X-App-Device` 必须与账号的
   **DUID**(数盟，`app_webview.../Cookies` 的 DID cookie /
   `com.coolapk.market_dna.xml` 的 device_id)一致，否则 feed/detail
   返回验证码(`err_request_captcha_v2`)。用 App 真身 DUID + Build 属性
   拼出设备串后**验证码消失、详情返回真实数据**

登录获取(实测路径，YiDun 会杀无头浏览器)：**模拟器里用 App 自己的
WebView 登录**(手机号 + 短信码)→ root 读 `coolapk_preferences_v7.xml`
拿 API token → 组 `uid/token/username` cookie。

### 4.4 SSE 真实格式(实测)

```
{"data":{"entityId":"334065","entityType":"ai_prompt","text":"","is_answer":false,...}}
{"data":{"entityId":334065,"entityType":"ai_prompt_answer_stream","text":"<增量>",...}}
...
{"data":{"entityId":334065,"entityType":"ai_prompt_answer","text":"<全文>","finish_reason":"finish"}}
```
注意行本身就是 JSON(包装在 `data` 键里)，不是 `data:` 前缀；
finish 由 `finish_reason":"finish"` 标记。

### 4.4b 反代客户端(coolapk_ai.py)

`coolapk_ai.py`(依赖 `coolapk_token.py` + `requests`):

```sh
# 凭证(登录流程产出):coolapk_cookie.txt + device_code.txt
python3 coolapk_ai.py                    # 自动找开启 AI 的 feed 并流式回答
python3 coolapk_ai.py 74203436 "三句话总结"  # 指定 feed
```

**已完整端到端验证**：indexV8 200 → feed/detail 200(真设备串过风控)
→ ai/feedAsk 200 `text/event-stream` → DeepSeek 增量流 + finish 事件。

### 4.5 OpenAI / Anthropic 兼容网关
