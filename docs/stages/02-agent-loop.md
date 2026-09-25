# 阶段 2：分层 Agent 闭环

状态：代码与本地确定性验收已完成；B11 真实模型闭环尚未通过，当前端点返回限流或超时。用户已授权阶段 2 与本地 `.env` 验证，并确认下列 TDD 测试边界。使用方式见 [Agent 指南](../guides/agent.md)。

## 范围与公开测试边界

沿用 [路线图](../roadmap.md) 和 ADR：[运行时可替换](../adr/0002-langchain-runtime-provider.md)、[checkpoint 状态归属](../adr/0003-checkpoints-own-recovery-state.md)。

已确认的公开测试边界：

1. `AgentRuntime.run(request) -> RunResult`：模型、工具、最终回答的闭环；串行多轮、会话隔离、停止上限、取消和错误。
2. YAML + PluginCatalog + PluginHost：替换模型、工具集和整个运行时，应用保持不变。
3. CLI：单次任务、串行多轮输入、退出码及脱敏错误。
4. 显式启用的真实端点集成：读取 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL`，检查真实工具调用和结果回传；默认测试与 CI 不访问外部模型。

每次只推进一个失败行为测试与对应最小实现。不得提前堆叠全部测试，也不以 mock 本项目内部模块替代真实装配。确定性模型响应仅替换模型系统边界，宿主、工具和 LangChain 循环实际运行。

## 模块职责

- `contracts`：框架无关的运行请求、结果与 AgentRuntime 协议，应用无需接触 LangChain 消息或 LangGraph 对象。
- `kernel`：复用阶段 1 插件宿主，继续只处理依赖与资源生命周期。
- `plugins`：LangChain 运行时、OpenAI 兼容模型、无副作用工具集、内存 checkpoint。
- `application`：通过 AgentRuntime 执行用户任务，不维护另一份权威会话历史。
- `bootstrap`：显式插件清单、YAML 装配和指定 `.env` 的配置读取。
- `interfaces`：CLI 输入输出和进程生命周期。

默认运行时适配层内部使用 LangChain 原生模型、工具和 checkpoint 类型，避免重复实现完整框架协议。这些类型留在插件适配层；替代运行时只需满足框架无关的 AgentRuntime。

阶段 1 每项服务单一提供者的约束继续适用。工具集提供者负责聚合本阶段工具；增加工具或替换工具集不修改应用和运行循环。工具贡献注册表按后续真实需求再引入。

## 首个运行闭环

首个工具为整数加法，验证用户输入 → 模型产生工具调用 → 执行工具 → 回传工具结果 → 模型最终回答。工具不读取文件、执行命令或修改工作区。

CLI 一次绑定一个已有工作区目录、一个 Agent，同一会话串行提交。工作区身份用于确定本次运行范围，本阶段不将仓库内容自动发送给模型。

通过内存 checkpoint 支持当前进程的多轮上下文与会话隔离。进程退出后状态丢失，持久恢复在阶段 4 实现。执行日志只承担观察职责，不用于重放恢复。

## 模型与配置

显式使用 Chat Completions（`use_responses_api=False`），模型名、地址与凭据从配置指定的环境变量读取。`.env` 只作为本地输入，不提交，不回显其内容，默认进程环境优先于文件值；`--env-override` 显式选择文件优先。真实验收使用文件优先，防止全局凭据覆盖用户指定配置。

真实验证以工具结果与后续模型回答为证据；普通文本回答成功不能代表工具调用兼容。设置调用与整轮期限、有限模型调用次数，避免失控运行。供应商不支持的能力如实记录，不假定所有兼容端点都支持流式或结构化输出。

## 验收标准与证据

| 编号 | 验收标准 | 证据入口 / 状态 |
| --- | --- | --- |
| B01 | 模型调用工具、真实执行工具、回传结果、输出最终回答 | `test_agent_runtime.py` 与 `test_openai_adapter.py` 已通过对应测试 |
| B02 | YAML 装配模型、工具集、checkpoint、运行时，依赖顺序自动计算 | `test_agent_plugins.py` 已通过基本装配 |
| B03 | 更换模型、增加工具、更换整个运行时，应用代码不变 | `test_agent_plugins.py`：替代运行时、替代模型与新增 multiply 工具集通过 |
| B04 | 同一会话记住历史，不同会话隔离；每轮只报告本轮工具结果 | `test_agent_runtime.py`：会话历史、隔离、本轮工具结果范围通过；CLI /new 隔离通过 |
| B05 | 工作区存在且运行时绑定一个工作区，拒绝并发请求与空输入 | `test_agent_runtime.py` 已通过对应测试 |
| B06 | 模型调用次数上限与整轮超时可停止执行 | 循环上限、超时、YAML 限制配置测试已通过 |
| B07 | 取消传播到正在执行的模型，清理完成后返回取消 | 运行时取消测试、CLI SIGINT 退出 130、模型 HTTP 资源关闭测试通过 |
| B08 | 失败/取消后的会话不可直接续接，使用新会话；错误内容不回显供应商敏感数据 | 模型失败、空最终回答、未完成会话拒绝续接、CLI 错误不回显凭据均通过 |
| B09 | CLI 单次输入、多轮、/new、/exit、EOF、退出码可观察 | `test_agent_cli.py`：单次、多轮、/new、/exit、EOF、SIGINT 和错误退出通过 |
| B10 | 日志用于观察，不另存权威历史；不默认输出凭据与消息正文 | `test_agent_runtime.py`：成功/失败元数据日志通过，不输出消息和异常正文 |
| B11 | `.env` 真实端点至少完成一次工具闭环和同会话后续回答 | 未通过：已使用 .env 原值实际调用，Agent 返回限流错误；curl 在延长至 600 秒后普通聊天返回 HTTP 200。默认 CI 不调用外部模型 |
| B12 | 全部确定性测试、Ruff、mypy、构建和 wheel 独立运行通过，使用指南及 CI 同步 | 84 个测试通过，1 项 live 默认跳过；Ruff/mypy、sdist/wheel 构建通过，独立 wheel 的 5 项 CLI 测试通过，CI 与使用指南已同步 |

## 运行接口与会话规则

- `RunRequest(session_id, text, workspace)` 是运行请求，`AgentRuntime.run()` 异步返回 `RunResult(text, tools)`。工具结果携带名称、调用 ID、内容与 success/error 状态，只覆盖当前轮次。
- `AgentSession` 持有运行时、规范化工作区路径及会话 ID，`ask(text)` 构造请求；不保存另一份消息历史。
- 一个运行时绑定一个已有工作区，拒绝并发运行。每轮配置模型调用上限、图步数保护与总期限。
- 成功后的同一会话可继续；失败、超时、取消或无有效最终回答后禁止续接，用户创建新会话。阶段 4 再定义未完成状态的恢复策略。
- 模型 HTTP 客户端在 activate 内由 PluginContext 接管；入口结束业务请求后关闭宿主。插件构造与配置验证不创建连接。
- 元数据日志用于观察，不提供完整事件重放。成功轮次返回工具结果；执行中失败不保证返回部分工具执行明细。

## TDD 与交付记录

每个新增行为先执行失败测试，再增加最小实现：最初运行接口缺失、YAML 装配入口缺失、无效请求被接受、循环预算缺失、取消后错误续接、并发请求未拒绝、超时未生效、模型错误直接暴露、CLI 入口及多轮缺失、日志缺失、空回答被误判成功、工具错误状态缺失，以及显式环境文件优先级缺失。

框架与已有接口天然支持的 checkpoint、多实现替换等行为直接补充验收，不制造虚假的 red 阶段。

本地最终检查使用 Python 3.13；84 个测试包含阶段 1 回归，默认跳过的 live 测试另行显式执行但未通过。文档本地链接、`git diff --check` 和凭据未进入交付文件的检查通过。CI 配置已更新，远端执行结果需提交后查看。

### 真实端点记录（2026-09-25）

1. 初次集成返回 429 / 供应商代码 1305（模型繁忙）。排查发现进程中存在另一项同名 API Key，默认环境优先覆盖了文件值；未输出密钥。
2. 增加并验证显式文件优先选项后，使用项目 `.env` 原值重新执行完整 live 测试，仍返回限流，闭环未验收通过。
3. 按用户要求用 curl 直接验证，凭据经标准输入配置传递，不置于命令参数：最小 Chat Completions 请求达到 60 秒上限（curl 28）；带密钥 `/models` 为 HTTP 200，不带密钥的同接口为 401。
4. 模型列表返回 11 项，当前配置的模型 ID 不在列表中。该证据说明密钥通过列表接口认证；不能据此确认当前模型的推理权限、别名支持或工具兼容性，也不能直接断言该模型不存在。
5. 按用户要求将示例模型请求与整轮期限均延长至 600 秒，并放宽模型配置上限至 600 秒。新增 YAML 配置验收先失败再通过；相关 26 项测试及 Ruff/mypy 通过。以原 `.env` 重跑真实 Agent 验收，约 2 秒返回限流错误，未达到超时期限，B11 仍未通过。
6. 回归测试 84 项通过、1 项 live 默认跳过；提交前按用户要求删除独立的 `tests/test_openai.py`，该文件未进入版本控制。
7. curl 的最小聊天请求也改为 600 秒上限，使用原 `.env` 配置，约 31 秒返回 HTTP 200，响应包含 choices 且无 error。由此确认当前凭据与模型可完成普通聊天；工具闭环仍需单独验收。
8. 普通聊天成功后再次执行真实 Agent 验收，约 3 秒仍返回限流错误；当前证据不能确定工具请求与最小聊天请求表现不同的原因，未判定为工具不兼容。

保留用户配置，不自动更换模型、服务地址或密钥。B11 保持未通过，端点恢复或用户调整配置后重跑 `uv run --locked pytest tests/test_agent_live.py --run-live -v --tb=short`。本地 HTTP 协议测试结果独立记录，不替代真实服务验收。

## 官方资料

- [LangChain Agents](https://docs.langchain.com/oss/python/langchain/agents)：`create_agent` 提供执行循环。
- [短期记忆](https://docs.langchain.com/oss/python/langchain/short-term-memory)：checkpoint 与会话线程。
- [ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai)：模型与兼容端点接入。
- [模型调用限制](https://github.com/langchain-ai/docs/blob/main/src/oss/langchain/middleware/built-in.mdx)：由运行时适配层设置有界执行。

2026-09-25 从 PyPI 核实稳定版：LangChain 1.4.2、langchain-openai 1.6.6、LangGraph 1.2.12、python-dotenv 1.2.3。实际安装结果以 uv.lock 为准。
