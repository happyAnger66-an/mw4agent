# OpenClaw：`web_search` Tool 实现原理

本文总结 OpenClaw 中 `web_search` 工具的实现链路：工具如何被注册给 LLM、如何选择 provider 与读取鉴权、如何通过网络安全护栏访问外部搜索 API、如何缓存与格式化输出，以及如何对外部内容做防 prompt-injection 包装。

> 说明：本文描述的是 OpenClaw 仓库内的实现（TypeScript），便于 `mw4agent` 对齐设计与实现。

## 总览：关键文件与职责

- **工具实现与注册**：`src/agents/tools/web-search.ts`
  - 定义 tool schema（参数）、provider 分支、缓存、执行逻辑
  - `createWebSearchTool(...) -> AnyAgentTool | null`
- **网络请求安全护栏（SSRF/代理/超时）**：`src/agents/tools/web-guarded-fetch.ts`
  - 封装 `fetchWithSsrFGuard` 的受控使用方式
  - 分“trusted endpoint”（允许 env proxy）与 “strict endpoint”（更严格）两种调用口
- **外部内容安全包装（防 prompt injection）**：`src/security/external-content.ts`
  - `wrapWebContent(text, "web_search")`：把 web snippet 当作“不可信外部内容”封装后再交给 LLM
- **引用链接重定向解析**：`src/agents/tools/web-search-citation-redirect.ts`
  - 通过 HEAD 请求把 citation 的 redirect URL 解析为最终落地 URL（失败则原样返回）
- **通用缓存与工具参数读写**：`src/agents/tools/web-shared.js`、`src/agents/tools/common.ts`
  - 缓存 TTL、cache key normalize、response 读取上限等

## 1）工具如何暴露给 LLM

OpenClaw 将 `web_search` 作为一个标准 Agent Tool 暴露给模型（函数调用工具）：

- tool name：`web_search`
- 参数 schema：`WebSearchSchema`（TypeBox）
- 执行函数：`execute(...)` 返回结构化 JSON（通过 `jsonResult(...)`）

入口在 `src/agents/tools/web-search.ts` 的：

- `export function createWebSearchTool(options?)`
  - 返回 `AnyAgentTool` 对象（或 `null` 表示不启用）

## 2）启用开关、provider 选择与鉴权来源

### 2.1 是否启用

`createWebSearchTool` 内部会读取 `tools.web.search` 配置，并决定是否启用（返回 tool 或 `null`）。

此外，OpenClaw 的安全审计/暴露工具列表也会基于：

- `tools.web.search.enabled`
- 是否存在可用 API key
- 全局工具 allow/deny policy（例如禁用 `group:web`）

（相关检查可在 `src/security/audit-extra.sync.ts`、`src/config/zod-schema.agent-runtime.ts` 等处看到。）

### 2.2 provider 选择

OpenClaw 支持多 provider（在 `src/agents/tools/web-search.ts`）：

- `brave`
- `perplexity`
- `grok`
- `gemini`
- `kimi`

provider 选择规则：

- 如果 `tools.web.search.provider` 显式指定，则按指定走
- 如果未指定，则按“当前可用 API key”自动探测并选择（有一套优先级顺序）

### 2.3 API key 来源

不同 provider 的 key 来源不同，通常支持：

- **config**：`tools.web.search.<provider>.apiKey` 或 `tools.web.search.apiKey`
- **env**：例如 `BRAVE_API_KEY`、`GEMINI_API_KEY`、`XAI_API_KEY`、`PERPLEXITY_API_KEY`、`OPENROUTER_API_KEY`、`KIMI_API_KEY/MOONSHOT_API_KEY`

如果缺 key，`execute(...)` 会返回结构化错误 payload（包含 docs 指引），而不是直接抛异常：

- `missingSearchKeyPayload(provider)`（`src/agents/tools/web-search.ts`）

## 3）网络请求如何做到“可用且安全”

OpenClaw 不直接裸用 `fetch` 调外部 API，而是通过受控的网络 guard 层：

- `src/agents/tools/web-guarded-fetch.ts`
  - `withTrustedWebToolsEndpoint(...)`
    - 允许 env proxy（`useEnvProxy: true`）
    - 使用一个“trusted network”的 SSRF policy（在代码里显式声明）
  - `withStrictWebToolsEndpoint(...)`
    - 更严格的策略（不走 env proxy）
  - `timeoutSeconds` 会被统一折算为 `timeoutMs`

这层 guard 最终基于 OpenClaw 的 `fetchWithSsrFGuard`（`src/infra/net/fetch-guard.js`）执行，避免常见 SSRF/内网探测风险并统一超时/释放资源。

另外，citation redirect 解析（HEAD）走 strict endpoint：

- `src/agents/tools/web-search-citation-redirect.ts`

## 4）缓存：减少重复外部请求

`web_search` 内置结果缓存（内存 Map）：

- `SEARCH_CACHE = new Map<string, CacheEntry<...>>()`
- 通过 `normalizeCacheKey` 规范化 key
- TTL 可配置（分钟），读取/写入使用 `readCache/writeCache/resolveCacheTtlMs`

缓存命中时直接返回之前的 payload（避免再次访问外部 provider）。

## 5）外部内容防 prompt-injection：`wrapWebContent`

OpenClaw 把 web 搜索返回的 snippet/title/content 等视为 **外部不可信内容**，在进入模型上下文前都会包装：

- `wrapWebContent(text, "web_search")`（`src/security/external-content.ts`）

包装的核心意图：

- 明确标记“这是外部不可信内容”，不能当系统指令执行
- 使用随机 id 的边界标记，防止内容伪造边界
- 检测一些常见注入模式（记录/监控用），但仍返回包装后的内容

因此 `web_search` 的返回 payload 通常会附带类似字段，提示下游（UI/LLM）这是外部不可信内容：

- `externalContent: { untrusted: true, source: "web_search", provider: ..., wrapped: true }`

## 6）不同 provider 的返回结构（高层）

OpenClaw 根据 provider 走不同的执行分支，并返回不同形状的 payload（都经 `jsonResult(...)` 返回给 LLM）：

### 6.1 Brave（传统搜索 API）

- 请求：GET `https://api.search.brave.com/res/v1/web/search`
- 鉴权：`X-Subscription-Token: <apiKey>`
- 返回：`results[]`
  - `title`（wrap 后）
  - `url`（原始 URL，便于 tool chaining）
  - `description`（wrap 后）
  - `published/age/siteName`（可选）

### 6.2 Perplexity / Grok / Gemini / Kimi（搜索增强 LLM）

这几类 provider 的返回更偏“合成回答 + 引用”：

- `content`：合成后的答案文本（wrap 后）
- `citations`：引用 URL 列表
- Grok 可能还有 `inlineCitations` 等（与 xAI Responses API 格式有关）

Gemini 分支里还会涉及 grounding/citations 的抽取与（部分场景）redirect 解析。

## 7）对齐 `mw4agent` 的实现建议（要点）

若在 `mw4agent` 实现类似的 `web_search`，建议按 OpenClaw 的分层拆解：

- **Tool 层**：参数 schema + provider 分支 + 返回 payload shape 统一
- **网络 guard 层**：统一 SSRF/代理/超时/response size 上限
- **外部内容包装层**：对所有 web 返回内容做 “untrusted wrapper”
- **缓存层**：按 query+参数组合 cache key；TTL 配置化
- **配置/密钥解析层**：config > env；缺 key 返回结构化错误（带 docs）

