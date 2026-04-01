# `web_search`：MW4Agent 实现与 OpenClaw 对比

本文梳理 `mw4agent` 当前 `web_search` tool 的实现方式，并对照 OpenClaw 的 `web_search` 设计，指出差异与仍缺失的能力点，作为后续对齐参考。

---

## 1. MW4Agent 当前实现（现状）

### 1.1 关键文件

- 工具实现：`mw4agent/agents/tools/web_search_tool.py`
- 工具注册：`mw4agent/agents/tools/__init__.py`
- 单测：`tests/test_web_search_tool.py`

### 1.2 Provider 与请求方式

当前实现支持 **Brave / Perplexity / Serper** 三种 provider：

- **brave**
  - endpoint：`https://api.search.brave.com/res/v1/web/search`
  - header：`X-Subscription-Token: <api_key>`
- **perplexity**
  - endpoint：`https://api.perplexity.ai/chat/completions`
  - header：`Authorization: Bearer <api_key>`
- **serper**（google.serper.dev）
  - endpoint：`https://google.serper.dev/search`
  - header：`X-API-KEY: <api_key>`

网络实现：Python `urllib.request.urlopen`（同步 I/O；在 async tool 中直接调用）。

### 1.3 配置与开关

从 root config 的 `tools.web.search` 读取：

- `enabled`：bool（默认 false，需要显式开启）
- `provider`：`brave/perplexity/serper`（可选；不填则按可用 key 自动选择）
- `apiKey/api_key`：通用 key（可选）
- `<provider>.apiKey/api_key`：按 provider 的 key（可选，优先级更高）
- `timeoutSeconds/timeout_seconds`：默认 10s
- `cacheTtlMinutes/cache_ttl_minutes`：默认 5min
- `maxResults`：默认 5（并限制最大 10）

API Key 解析优先级（高→低）：

1. `tools.web.search.<provider>.apiKey`（或 `api_key`）
2. `tools.web.search.apiKey`（或 `api_key`）
3. 环境变量：`PERPLEXITY_API_KEY` / `BRAVE_API_KEY` / `SERPER_API_KEY`

### 1.4 参数 schema（对 LLM 暴露）

对 LLM 的参数为：

- `query`（必填）
- `count`（1-10）
- `country/search_lang/ui_lang/freshness`（Brave 特定参数）

### 1.5 返回结构（ToolResult.result）

成功时返回结构化 JSON，大体形状：

- `provider: "brave"`
- `query`
- `count`
- `results[]`: `{ title, url, description, published? }`
- `cache`: `{ hit, ttlSeconds? }`
- `externalContent`: `{ untrusted: true, source: "web_search", provider: "brave", wrapped: true }`

其中 `title/description` 会通过 `_wrap_untrusted(...)` 包装为“外部不可信内容边界块”，用于降低 prompt-injection 风险。

### 1.6 缓存策略

- 进程内全局字典 `_CACHE`（key 由 query + 参数序列化而成）
- TTL 到期淘汰
- 不做最大容量限制、也不做跨进程/跨实例共享

### 1.7 错误/异常处理

- 缺 API key：**`success=True`**，返回 `{ error: "missing_brave_api_key", message: ... }`
- 其他异常（网络/解析）：**`success=True`**，返回 `{ error: "web_search_failed", message: ... }`
- 参数缺失（query 为空）：`success=False`

> 现状上，“外部请求失败”被建模为成功调用但返回 error 字段（而不是 tool-level failure），这会影响上层对失败的重试/展示策略。

---

## 2. OpenClaw 的 `web_search`（能力基线）

OpenClaw 的实现要点可参考：

- `openclaw/src/agents/tools/web-search.ts`
- `openclaw/src/agents/tools/web-guarded-fetch.ts`
- `openclaw/src/agents/tools/web-shared.ts`
- `openclaw/src/security/external-content.ts`
- `openclaw/src/agents/tools/web-search-citation-redirect.ts`

其核心特征：

### 2.1 多 provider 支持

除 Brave 外，还支持 **Perplexity/Grok/Gemini/Kimi** 等“搜索增强 LLM”或搜索 API，并可按配置/可用 key 自动选择 provider。

### 2.2 受控网络访问（SSRF/代理/超时/大小）

OpenClaw 不直接裸用 fetch，而是经 `web-guarded-fetch` 走受控网络策略：

- 可区分 “trusted endpoint” 与 “strict endpoint”
- 统一超时、（通常还会）限制响应体读取大小/处理重定向策略
- 降低 SSRF/内网探测/代理滥用风险

### 2.3 外部内容安全包装更强

OpenClaw 的 `wrapExternalContent/wrapWebContent` 具备更多防护：

- 随机 id 边界标记（防伪造）
- **检测可疑注入模式**（用于审计/监控）
- **marker 伪造/同形异体/空白变体** 的清洗（避免外部内容伪造边界标记）

### 2.4 Citation redirect 解析

对 citation URL 的跳转通过 HEAD 解析到最终 URL（失败回退原 URL），用于提升引用可读性/可点击性并减少“跳转链接污染”。

### 2.5 更丰富的参数与输出形态

OpenClaw 的 schema 支持更通用的过滤维度（language/date range 等），并且不同 provider 会返回：

- `results[]`（传统搜索）
- 或 `content + citations`（搜索增强 LLM 合成回答）

同时配套更完整的错误 payload、文档指引、以及工具展示/审计配置（tool catalog / display overrides / security audit）。

---

## 3. 对比差异：MW4Agent 目前还缺哪些

### 3.1 Provider/能力覆盖

- **缺**：多 provider（Perplexity/Grok/Gemini/Kimi）与自动 provider 选择策略
- **缺**：合成回答（`content + citations`）形态的 web search（目前只返回 snippet 列表）

### 3.2 网络安全与可控性

- **缺**：OpenClaw 风格的“受控网络访问层”（SSRF guard、proxy 策略、redirect 策略、响应体大小上限）
- **潜在问题**：当前用 `urllib` 直连外网，缺少统一的网络策略封装与审计点

### 3.3 外部内容安全包装差距

MW4Agent 目前只有“边界块 + warning”，缺少 OpenClaw 的：

- **marker 伪造/同形异体变体** 清洗（外部内容可能伪造 `<<<EXTERNAL_UNTRUSTED_CONTENT...>>>`）
- **可疑注入模式检测**（用于日志/审计）

### 3.4 缓存与资源控制

- **缺**：缓存最大容量、LRU/随机淘汰等策略
- **缺**：跨进程/多实例共享缓存（可选）
- **缺**：对单次响应的内容大小/条目字段大小的限制与截断策略（避免超大 snippet 撑爆上下文）

### 3.5 错误语义与上层协作

- **差异**：MW4Agent 将外部请求失败视为 `success=True` 的 error payload；OpenClaw 通常会在更高层统一错误分类（并结合 failover/重试/告警）。
- **缺**：更细粒度的错误分类（auth/rate-limit/timeout/provider-specific）与可重试提示。

### 3.6 工具策略/审计/展示集成

- **缺**：类似 OpenClaw 的 tool catalog / display overrides / security audit 对 web 工具的统一治理（启用条件、密钥存在性检查、暴露工具列表的审计输出等）。

---

## 4. 建议的对齐路线（按优先级）

### 4.1 P0（安全与稳定性）

- 增加“web tools 网络访问封装层”：统一 timeout、重定向策略、响应体大小上限；为后续 SSRF guard 预留接口。
- 升级外部内容包装：增加 marker 伪造清洗 + 可疑注入模式检测（至少日志记录）。
- 将 `urllib` 阻塞 I/O 改为异步 HTTP 客户端（或放到线程池），避免阻塞 event loop。

### 4.2 P1（能力对齐）

- provider 抽象：支持 `provider` 配置与自动选择；逐步加入 Perplexity/Gemini 等。
- 支持 `content + citations` 结果形态；并实现 citation redirect 解析（可选）。

### 4.3 P2（体验与治理）

- 缓存策略完善（容量上限/LRU/可观测性指标）。
- 与 tools policy / dashboard / docs 更好集成：明确开关、缺 key 提示、成本提示、审计输出。

