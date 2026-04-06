# Orbit 测试文档

## 规范

- **所有自动化测试必须放在本目录 `tests/` 下**（命名建议 `test_*.py`），**不要**在 `orbit/`、`frontend/` 等源码树中与实现文件并列放置 `*_test.py`。
- 从 `tests/` 内用 **`from orbit....` 绝对导入** 引用被测模块；路径由 `conftest.py` 在会话开始时把仓库根加入 `sys.path` 保证指向当前 checkout。

## 测试文件列表

### 端到端测试（E2E）

- **`e2e/test_config_manager_e2e.py`**：ConfigManager 的端到端测试
  - 加密读写配置
  - 明文回退
  - 配置列表和删除
  - 自定义目录

- **`e2e/test_skill_manager_e2e.py`**：SkillManager 的端到端测试
  - 加密读写技能
  - 明文回退
  - 技能列表和删除
  - 批量读取技能

- **`e2e/test_channels_dispatcher_e2e.py`**：ChannelDispatcher 的端到端测试
  - 直接调用模式（不经过 Gateway）
  - Gateway RPC 模式
  - Mention gating 测试
  - 私聊不需要 mention

- **`e2e/test_gateway_channels_e2e.py`**：Gateway + Agent + Channels 完整流程测试
  - 完整流程：Channel -> Gateway -> Agent -> Reply
  - Gateway agent.wait 返回 replyText
  - 多个 agent 调用

### 单元测试

- **`test_subagents_parse.py`**：`/subagents` 参数解析（`shlex`、spawn 拆分）
- **`test_subagents_logic.py`**：桌面子智能体纯逻辑（记录过滤、runId 解析、transcript 尾部）
- **`test_subagents_cmd_integration.py`**：`subagents_cmd` 与 `GatewayState` 集成（需安装 FastAPI，否则 skip）
- **`test_crypto_secure_io.py`**：加密框架基础测试
- **`test_gateway_agent_flow.py`**：Gateway Agent 交互测试（脚本式）
- **`test_gateway_tool_ls.py`**：Gateway 工具调用测试（脚本式）

## 运行测试

### Playwright `web_search` 实网（可选）

需已安装 `orbit[playwright]`、`playwright install chromium`，且本机 **127.0.0.1:7890** 有 HTTP 代理（可用环境变量覆盖）：

```bash
ORBIT_WEB_SEARCH_LIVE=1 pytest tests/test_web_search_playwright_live.py -v
# 可选：ORBIT_WEB_SEARCH_LIVE_PROXY=http://127.0.0.1:7890
```

未设置 `ORBIT_WEB_SEARCH_LIVE=1` 时该文件内用例会 **skip**，不影响默认 `pytest tests/`。

### 运行所有测试

```bash
pytest tests/ -v
```

### 运行特定测试文件

```bash
pytest tests/e2e/test_config_manager_e2e.py -v
pytest tests/e2e/test_skill_manager_e2e.py -v
pytest tests/e2e/test_channels_dispatcher_e2e.py -v
pytest tests/e2e/test_gateway_channels_e2e.py -v
```

### 运行特定测试用例

```bash
pytest tests/e2e/test_config_manager_e2e.py::test_config_manager_write_read_encrypted -v
```

## 测试依赖

- `pytest`：测试框架
- `pytest-asyncio`：异步测试支持

安装依赖：

```bash
pip install pytest pytest-asyncio
```

或使用项目依赖：

```bash
pip install -e .
```

## 测试环境变量

某些测试需要设置环境变量：

- `MW4AGENT_SECRET_KEY`：加密密钥（base64 编码的 32 字节随机数）

生成密钥：

```bash
python3 - << 'PY'
import os, base64
print(base64.b64encode(os.urandom(32)).decode())
PY
```

设置环境变量：

```bash
export MW4AGENT_SECRET_KEY="<生成的密钥>"
```

## 测试覆盖范围

### ConfigManager 测试覆盖

- ✅ 加密读写
- ✅ 明文回退
- ✅ 配置列表
- ✅ 配置删除
- ✅ 默认管理器
- ✅ 自定义目录

### SkillManager 测试覆盖

- ✅ 加密读写
- ✅ 明文回退
- ✅ 技能列表
- ✅ 批量读取
- ✅ 技能删除
- ✅ 默认管理器
- ✅ 自定义目录

### ChannelDispatcher 测试覆盖

- ✅ 直接调用模式
- ✅ Gateway RPC 模式
- ✅ Mention gating
- ✅ 私聊不需要 mention

### Gateway + Channels 测试覆盖

- ✅ 完整流程
- ✅ replyText 提取
- ✅ 多个并发调用

## 注意事项

1. **Gateway 测试**：某些测试需要启动 Gateway 子进程，会自动查找空闲端口
2. **异步测试**：使用 `@pytest.mark.asyncio` 标记异步测试函数
3. **临时文件**：测试使用 `tmp_path` fixture 创建临时文件，测试结束后自动清理
4. **环境隔离**：使用 `monkeypatch` fixture 隔离环境变量，避免测试间相互影响
