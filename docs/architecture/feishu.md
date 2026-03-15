# Feishu 通道问题定位与修复记录

本文记录「飞书机器人收到用户消息并完成 LLM 处理，但用户未收到飞书回复」的定位过程与修复方案，便于后续排查类似问题。

---

## 现象

- 用户在飞书侧发消息，机器人侧能收到事件并进入 LLM 流程，处理正常完成。
- 飞书会话中**没有收到机器人回复**。

---

## 定位过程

### 1. 确认数据流

端到端路径为：

```text
飞书事件 → FeishuChannel（webhook/websocket）→ InboundContext
  → ChannelDispatcher.dispatch_inbound()
  → AgentRunner.run() → LLM 回复
  → dispatcher 调用 plugin.deliver(OutboundPayload(...))
  → FeishuChannel.deliver() → feishu_outbound.send_text() → 飞书 Open API
```

现象说明入站与 LLM 段正常，问题应在 **dispatcher → deliver → 飞书 API** 这一段。

### 2. 查看 FeishuChannel.deliver 的约定

在 `mw4agent/channels/plugins/feishu.py` 的 `deliver()` 中：

- 从 **`payload.extra["inbound"]["extra"]`** 读取 **`chat_id`**、**`message_id`**、**`thread_id`**。
- 若 **`chat_id` 为空**，则不会调飞书接口，仅执行 `print(f"[feishu:AI] {payload.text}")` 到 stdout，即**静默不发给飞书**。

因此，若 `OutboundPayload` 的 `extra` 里没有带好入站上下文，就会出现「LLM 有回复、飞书无回复」的现象。

### 3. 查看 dispatcher 如何构造 OutboundPayload

在 `mw4agent/channels/dispatcher.py` 的 `dispatch_inbound()` 中，拿到 LLM 的 `result_text` 后调用：

```python
await plugin.deliver(
    OutboundPayload(
        text=result_text,
        is_error=False,
        extra={},   # 此处为空
    )
)
```

即 **`extra` 始终传的是空字典**，没有把当前入站的 `InboundContext` 信息（含 `chat_id` 等）传给 `deliver`。

### 4. 根因结论

- **根因**：dispatcher 调用 `deliver` 时传入 **`extra={}`**，Feishu 的 `deliver` 无法从 `payload.extra` 中拿到 **`chat_id`**，因此走「无 chat_id 仅打印」分支，回复不会通过飞书 API 发回用户。
- **设计预期**：设计文档（如 `docs/architecture/channels/feishu.md`）中约定 `OutboundPayload` 的 `extra` 应包含 `{"inbound": {"extra": {...}}}`，以便 channel 回发到正确会话，但实现时 dispatcher 未把入站上下文写入 `extra`。

---

## 修复方案

在 **`mw4agent/channels/dispatcher.py`** 中，调用 `plugin.deliver()` 时，将当前入站的 **`ctx.extra`** 带入 `OutboundPayload.extra`，格式与 Feishu `deliver` 的约定一致：

```python
# 将入站上下文的 extra（含 chat_id、message_id 等）传给 deliver，供 Feishu 等 channel 回发到正确会话
extra = {"inbound": {"extra": ctx.extra}} if ctx.extra else {}
await plugin.deliver(
    OutboundPayload(
        text=result_text,
        is_error=False,
        extra=extra,
    )
)
```

这样 Feishu 的 `deliver()` 能正确读到 `chat_id` 等字段，调用飞书 Open API 将回复发回对应会话。

### 与 feishu-openclaw-plugin 对齐的后续改动（仍收不到回复时）

在保持上述 `extra` 传递的前提下，做了以下对齐与加固：

1. **发送格式**  
   `mw4agent/feishu/client.py` 中发送消息改为与 OpenClaw 一致：**`msg_type: "post"`**，**content** 为 `zh_cn.content` 的 JSON 字符串（`[{"tag":"md","text": "..."}]`），不再使用 `msg_type: "text"`。部分环境或权限下 text 与 post 行为可能不同，统一用 post 便于排查。

2. **session_id 回退**  
   dispatcher 传入 `extra["inbound"]["session_id"]`（即 `ctx.session_id`）。Feishu 下 session 即会话 chat_id，deliver 在拿不到 `extra.chat_id` 时用 **`session_id`** 作为 chat_id 回退，避免因字段缺失导致只打印不请求 API。

3. **入站字段兼容**  
   Webhook 解析 `event.message` 时同时兼容 **snake_case** 与 **camelCase**（如 `chat_id`/`chatId`、`message_id`/`messageId`、`thread_id`/`threadId`），与 OpenClaw 侧事件结构一致，减少漏解析。

4. **日志与异常**  
   deliver 中：无 chat_id 时打 **warning**（并写出 session_id）；真正调用 API 前打 **info**（chat_id、reply_to、thread）；调用失败时 **logger.exception** 记录异常，便于定位接口或权限问题。

---

## 相关代码位置

| 位置 | 说明 |
|------|------|
| `mw4agent/channels/dispatcher.py` | `dispatch_inbound()` 中构造 `OutboundPayload` 时传入 `extra={"inbound": {"extra": ctx.extra, "session_id": ctx.session_id}}` |
| `mw4agent/channels/plugins/feishu.py` | `deliver()` 从 `payload.extra["inbound"]["extra"]` 取 `chat_id`、`message_id`、`thread_id`，缺省时用 `inbound.session_id` 作 chat_id；无 chat_id 时打 warning 并仅打印；调用 API 前后有 info/exception 日志 |
| `mw4agent/feishu/client.py` | 使用 `msg_type: "post"` 与 `zh_cn` 富文本 content，与 feishu-openclaw-plugin 的 send.js 一致 |

---

## 延伸说明

- 其他 channel（如 console、webhook）若需根据入站上下文回发（例如回复到原会话），也应约定 `OutboundPayload.extra` 的结构，并由 dispatcher 统一传入入站上下文。
- 设计文档见 [channels/feishu.md](channels/feishu.md)。

---

# WebSocket 模式启动报错：this event loop is already running

本文记录 `connection_mode='websocket'` 时启动出现的 `RuntimeError: this event loop is already running` 的定位与修复。

---

## 现象

启动时日志中出现（功能上可能仍可用，但报错会持续出现）：

```text
asyncio: Task exception was never retrieved
future: <Task ... coro=<FeishuChannel._run_ws_monitor() ...> exception=RuntimeError('this event loop is already running.')>
Traceback (most recent call last):
  ...
  File ".../lark_oapi/ws/client.py", line 114, in start
    loop.run_until_complete(self._connect())
RuntimeError: this event loop is already running.
```

---

## 定位过程

### 1. 调用链

- `FeishuChannel._run_ws_monitor()` 里为不阻塞主流程，把 lark-oapi 的 WebSocket 客户端放到线程池里跑：`await loop.run_in_executor(None, _run_ws)`，其中 `_run_ws()` 内部调用 `ws_client.start()`。
- 报错发生在 `lark_oapi/ws/client.py` 的 `Client.start()` 里，其内部调用了 `loop.run_until_complete(self._connect())` 等。

### 2. 查看 lark_oapi 的 loop 从哪来

打开 `lark_oapi/ws/client.py` 可见：

- **模块加载时**（文件顶部）就固定了全局 loop：
  - `try: loop = asyncio.get_event_loop()`
  - `except RuntimeError: loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)`
- `Client.start()` 里使用的正是这个**模块级全局** `loop`，会执行：
  - `loop.run_until_complete(self._connect())`
  - 异常时 `loop.run_until_complete(self._disconnect())`、`loop.run_until_complete(self._reconnect())`
  - 以及 `loop.create_task(...)`、`loop.run_until_complete(_select())` 等。

### 3. 根因

- 主进程（如 FastAPI/uvicorn）启动后，**主线程的 event loop 已经在运行**。
- lark_oapi 是在**主线程**里 import 的，因此模块顶部的 `get_event_loop()` 拿到的是**主线程正在运行的那个 loop**。
- `_run_ws()` 在 **`run_in_executor` 的线程**里执行，但 `Client.start()` 用的仍是 lark_oapi 里保存的**主线程 loop**。
- 在该线程里对「已在主线程运行的 loop」调用 `run_until_complete()`，就会触发 **RuntimeError: this event loop is already running**。

---

## 修复方案

让 WebSocket 客户端在**专用线程内**使用**该线程自己的 event loop**，并让 lark_oapi 的 `Client.start()` 使用这个 loop，而不是主线程的 loop：

1. **在 WS 线程内新建并绑定该线程的 loop**  
   `ws_loop = asyncio.new_event_loop()`，`asyncio.set_event_loop(ws_loop)`，这样该线程后续的 asyncio 调用都走 `ws_loop`。

2. **让 lark_oapi 使用该 loop**  
   lark_oapi 的 `Client.start()` 用的是模块全局变量 `loop`，因此在该线程内 patch：  
   `import lark_oapi.ws.client as ws_client_mod` 后执行 `ws_client_mod.loop = ws_loop`，这样 `start()` 里的 `run_until_complete` 等都会跑在 `ws_loop` 上。

3. **在该线程内创建 Client 再 start()**  
   Client 的 `__init__` 里会创建 `asyncio.Lock()` 等，这些会绑定到「当前线程的当前 loop」。因此要在**已经设置好 `ws_loop` 并 patch 完模块**之后，再在该线程里 `Client(...)` 并 `start()`，保证 Lock 等绑定的是 `ws_loop`，避免与主 loop 混用。

4. **收尾**  
   在 `_run_ws()` 的 `finally` 里对 `ws_loop.close()`，以便在线程退出或异常时释放资源（若 `start()` 一直阻塞不返回，则不会执行到 close，属预期）。

实现上，将「创建 `lark.ws.Client` 并调用 `start()`」从主线程挪到 `_run_ws()` 内部，在 `_run_ws()` 开头完成上述 1、2 步后再创建 Client 并 start。相关代码见 `mw4agent/channels/plugins/feishu.py` 中 `_run_ws_monitor` 的 `_run_ws()` 定义。
