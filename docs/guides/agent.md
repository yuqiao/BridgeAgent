# Agent 运行与验证指南（阶段 2）

阶段 2 已实现可替换 AgentRuntime、LangChain 执行循环、OpenAI 兼容模型、加法工具和进程内多轮会话。本地确定性验收与真实服务验收分别记录在 [阶段 2 验收表](../stages/02-agent-loop.md)。本阶段尚不读取仓库文件或修改代码。

## 1. 准备模型配置

在仓库根目录执行 `uv sync --locked`，使用 Python 3.13。锁文件基于官方 PyPI；若本机配置了不同的索引，先设置 `export UV_DEFAULT_INDEX=https://pypi.org/simple`。

在本地 `.env` 填入三个变量，格式可参考 [无凭据模板](../../.env.example)：

```dotenv
OPENAI_BASE_URL=https://your-service.example/v1
OPENAI_API_KEY=replace-with-your-key
OPENAI_MODEL=your-model-name
```

上述值均为占位符。`OPENAI_BASE_URL` 是兼容 Chat Completions 的服务基础地址；最终请求路径为该地址下的 `/chat/completions`。模型必须实际支持标准工具调用。`.env` 已被 Git 忽略，配置 YAML 只保存环境变量名称。

配置读取规则：

- 默认只读取进程环境，不自动扫描 `.env`。
- `--env-file .env` 显式读取文件，默认进程环境中的同名变量优先。
- 同时加 `--env-override` 时，指定文件中的值优先。这适合机器已全局导出其他服务凭据、但本次希望使用项目 `.env` 的情形。
- 读取形成独立配置快照，不修改进程环境，不执行 shell 或 `${...}` 插值。显式文件不存在时报告错误。

## 2. 执行一个任务

```bash
uv run --locked python -m bridge_agent.interfaces.agent \
  --config examples/agent.yaml \
  --env-file .env --env-override \
  --workspace . \
  --prompt "请调用 add 工具计算 1847 + 2965，并给出结果。" \
  --show-tools
```

正常情况下，stderr 显示 `add: 4812`，stdout 显示模型生成的最终回答，其中应包含 `4812`。回答措辞由模型决定；只有工具记录与最终回答共同成立，才算闭环通过。

`--show-tools` 是显式的结果观察选项，会输出工具返回内容；默认只输出最终回答。它不输出 API Key 或模型连接参数。当前只有无副作用加法工具，后续接入其他工具时需考虑工具内容本身的敏感性。

`--workspace` 绑定已有目录，省略时使用当前目录。这里的工作区身份不会自动把目录文件发送给模型，也不授予文件读取工具。

## 3. 串行多轮对话

省略 `--prompt`：

```bash
uv run --locked python -m bridge_agent.interfaces.agent \
  --config examples/agent.yaml \
  --env-file .env --env-override \
  --workspace . --show-tools
```

然后逐行输入：

```text
请调用 add 计算 1847 + 2965，记住结果。
上一轮工具返回的数字是多少？不要重新调用工具。
/new
你好
/exit
```

- 同一会话使用内存 checkpoint 保存历史；应用层不另存消息列表。
- `/new` 创建一个新的会话 ID，新会话不共享前一个会话的模型上下文。旧 checkpoint 在当前进程内保留到宿主释放，阶段 2 不提供旧会话管理命令。
- `/exit` 或 EOF 正常退出。空行忽略。输入处理为串行，前一轮返回后才处理下一行。
- Ctrl+C 取消正在执行的请求并退出，等待插件资源释放，返回 `130`。
- 交互输入基于 asyncio 标准输入管道，在当前 macOS 开发环境与 Linux CI 验证；Windows 控制台未验证。
- 进程退出后会话丢失，持久化与续接属于阶段 4。

## 4. YAML 装配与限制

完整配置见 [examples/agent.yaml](../../examples/agent.yaml)。四项插件通过阶段 1 宿主装配：

| 插件 | 提供 | 依赖 |
| --- | --- | --- |
| `model.openai` | LangChain Chat Model | 配置引用的环境变量 |
| `tools.arithmetic` | 工具元组，包含 `add(a, b)` | 无 |
| `checkpoint.memory` | InMemorySaver | 无 |
| `runtime.langchain` | 框架无关的 AgentRuntime | 模型、工具集、checkpoint |

运行时写在 YAML 前面仍会在依赖就绪后激活。与阶段 1 一样，每项服务只能有一个提供者。

| 参数 | 默认值 | 示例文件值 / 语义 |
| --- | --- | --- |
| runtime `max_model_calls` | 8 | 每次 run 最多 8 次模型节点调用，包含最终回答调用 |
| runtime `timeout_seconds` | 60 | 示例设为 600，涵盖整轮模型与工具执行 |
| runtime `system_prompt` | 通用助手、算术使用工具 | 示例要求整数加法使用 add |
| model `timeout_seconds` | 30 | 示例设为 600，HTTP 请求超时配置 |
| model `max_retries` | 1 | SDK 对可重试请求最多重试 1 次；这不等于增加一次模型节点调用 |
| model `max_tokens` | 4096 | 经 ChatOpenAI 的 `max_completion_tokens` 发送，兼容性需端点支持 |
| model `base_url_env` | `OPENAI_BASE_URL` | 基础地址的环境变量名称 |
| model `api_key_env` | `OPENAI_API_KEY` | 凭据的环境变量名称 |
| model `model_env` | `OPENAI_MODEL` | 模型名的环境变量名称 |

运行时另外设置图步数保护，避免模型调用次数之外的图循环失控。工具输入校验或未知工具由 LangChain 形成错误工具结果供模型处理，仍受调用预算约束；未处理的模型或工具异常使本轮失败。

超时、取消、调用预算耗尽、模型/工具异常或无有效最终文本后，该会话被标记为不可继续。交互模式可输入 `/new`；Python 调用方创建新会话 ID。阶段 2 不会自动续跑未完成 checkpoint 或重放工具副作用。

示例的整轮 600 秒期限也包含 HTTP 重试与工具执行时间；模型的 600 秒配置不会延长整轮期限。两项配置均允许最大 600 秒。

## 5. Python 接口

```python
import asyncio
from pathlib import Path

from bridge_agent.application.agent import AgentSession
from bridge_agent.bootstrap.agent import agent_catalog, read_environment
from bridge_agent.contracts.agent import AGENT_RUNTIME
from bridge_agent.kernel.host import PluginHost


async def main() -> None:
    environment = read_environment(Path(".env"), override=True)
    catalog = agent_catalog(environment=environment)
    async with PluginHost(catalog.load(Path("examples/agent.yaml"))) as host:
        session = AgentSession(host.resolve(AGENT_RUNTIME), Path.cwd())
        result = await session.ask("请使用 add 计算 19 + 23。")
        print(result.text)
        for item in result.tools:
            print(item.name, item.status, item.content)


asyncio.run(main())
```

`AgentSession` 仅持有运行时、工作区与会话 ID。公开运行请求为 `RunRequest(session_id, text, workspace)`；返回 `RunResult(text, tools)`。`ToolResult` 包含 name、call_id、content 与 success/error 状态，只包含本轮的工具结果，不返回一份可被应用改写的完整历史。

应用只依赖 [AgentRuntime 协议](../../src/bridge_agent/contracts/agent.py)。默认实现的模型、工具和 checkpoint 类型集中在 [LangChain 适配层](../../src/bridge_agent/plugins/langchain/services.py)，kernel 和 application 不导入框架类型。

更换模型服务或工具集，提供对应适配层服务键；整个运行时可替换为任意满足 `AgentRuntime.run()` 的实现。组合入口可通过 `agent_catalog(extra=(definition, ...))` 显式登记额外插件，再由 YAML 选择。测试中的替代运行时与新增 multiply 工具见 [替换性验收](../../tests/test_agent_plugins.py)。

## 6. 错误与观察

| 情况 | 行为 / 处理 |
| --- | --- |
| 缺少配置或模型变量 | 非零退出，检查显式文件和 YAML 中变量名称 |
| API 返回限流或错误 | 非零退出，公共错误不回显供应商响应；Python 异常原因保留原错误供本地诊断 |
| `AgentLimitError` | 达到执行上限，检查任务或调整有界配置，然后使用新会话 |
| `AgentTimeoutError` | 整轮期限或底层超时，使用新会话重试 |
| `AgentBusyError` | 同一运行时已有请求，调用方应串行提交 |
| `AgentSessionError` | 上一轮未完成，使用 `/new` 或新 session_id |
| `AgentInputError` | 空输入/会话 ID、目录无效或切换工作区 |

CLI：成功为 `0`，执行/配置失败为 `1`，命令参数错误为 `2`，Ctrl+C 为 `130`。交互模式出现领域错误后允许继续输入，但本进程最终返回 `1`，即使随后新会话成功也保留失败信号。

运行时通过标准 logging 的 `bridge_agent.runtime` logger 发出 INFO 事件：run_started、tool_completed、run_completed、run_failed、run_cancelled。附带运行 ID、工具名称/状态或失败类别，不记录密钥、用户消息、工具参数与结果正文。默认不配置输出 handler，Python 应用可接入自己的日志处理器。工具完成事件在成功返回本轮结果时汇总发出，失败运行不保证提供逐工具完整审计。

日志用于观察，checkpoint 才管理执行状态。Python 原始异常可能含服务响应，不应直接写入对外日志。

## 7. 验收命令

```bash
# 不访问外部模型；live 测试默认跳过
uv run --locked pytest -q

# 逐项检查阶段 2 行为
uv run --locked pytest tests/test_agent_runtime.py tests/test_agent_plugins.py -v
uv run --locked pytest tests/test_agent_cli.py tests/test_openai_adapter.py -v

# 明确启用真实端点验收：项目 .env 值优先
uv run --locked pytest tests/test_agent_live.py --run-live -v --tb=short
```

真实测试验证 `1847 + 2965 = 4812` 的实际工具结果、模型最终回答，以及同会话后续回答。默认 CI 不执行它；本地 HTTP 模拟端点测试验证 SDK 请求协议和工具结果回传，不能替代真实端点兼容性验收。

测试与文档不保存真实凭据。遇到 429、超时或不支持工具调用时，如实记录未通过，不自动换模型、改密钥或无限重试。
