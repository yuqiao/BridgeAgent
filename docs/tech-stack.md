# 技术栈

## 已确定

| 项目 | 决策 | 当前状态 |
| --- | --- | --- |
| Python | 3.13 | `.python-version` 与 CI 已使用 3.13；包元数据当前允许 `>=3.13` |
| 项目与依赖管理 | uv | 已初始化，依赖由 `uv.lock` 锁定 |
| Agent 框架 | 最新稳定版 LangChain | 阶段 2 已锁定 1.4.2；langchain-openai 1.6.6、LangGraph 1.2.12 |
| 应用配置 | YAML + 显式插件清单 | 已实现：PyYAML 安全解析、Pydantic v2 严格配置校验 |
| 生命周期 | asyncio、graphlib、AsyncExitStack | 已实现：串行依赖激活、资源逆序清理、错误汇总 |
| 工程工具 | Ruff、mypy、pytest、Hatchling | 已启用静态检查、行为测试及独立安装验收 |

截至 2026-09-25，PyPI 的 LangChain 最新稳定版为 **1.4.2**，发布于 2026-09-18；其 Python 要求为 `>=3.10.0,<4.0.0`，支持 Python 3.13。来源：[PyPI 元数据](https://pypi.org/project/langchain/1.4.2/)。

“最新版”用于选择接入时的稳定版本，不能作为每次启动时自动升级的要求。实际接入时重新核实最新稳定版及配套依赖，并将解析结果写入 `uv.lock`；升级通过显式依赖变更和验证完成。

阶段 1 运行时依赖为 `pydantic>=2,<3` 和 `pyyaml>=6,<7`，开发依赖增加 `types-pyyaml`。
本次锁定 Pydantic 2.13.5、PyYAML 6.0.3；精确解析结果以 `uv.lock` 为准。阶段 2 新增 LangChain、langchain-openai、LangGraph、python-dotenv 与 HTTPX；模型 HTTP 客户端由插件上下文管理释放。

## 框架职责

LangChain 的 `create_agent` 提供基于 LangGraph 的 Agent 执行循环，支持通过 middleware 在模型和工具执行等环节扩展行为。LangGraph 提供更底层的状态与执行能力。来源：[Agents](https://docs.langchain.com/oss/python/langchain/agents)、[Middleware](https://docs.langchain.com/oss/python/langchain/middleware/overview)、[LangGraph 概览](https://docs.langchain.com/oss/python/langgraph/overview)。

已确认：BridgeAgent 管理插件装配、配置、依赖与资源生命周期，通过可替换运行时接口接入 LangChain `create_agent`。首个入口为 CLI，首期使用可信插件的显式装配。详见 [目标架构](architecture.md)。

恢复状态由默认运行时的 LangGraph checkpoint 管理，执行日志独立记录；先使用内存实现，后续增加持久化。具体后端及配套依赖尚未确定，见 [ADR-0003](adr/0003-checkpoints-own-recovery-state.md)。

首个模型插件已确定接入 OpenAI 或兼容 OpenAI 的服务；具体模型名称、服务地址与凭据通过运行配置提供，文档不绑定用户账号或密钥。

## 首期模型适配方案

使用 `langchain-openai.ChatOpenAI` 接入 OpenAI 官方服务及通过验收的 Chat Completions 兼容端点，显式配置 `model`、`base_url` 和凭据环境变量引用。兼容基线显式使用 `use_responses_api=False`，避免依赖自动 API 路由。参考：[官方集成文档](https://docs.langchain.com/oss/python/integrations/chat/openai)、[ChatOpenAI API](https://reference.langchain.com/python/langchain-openai/langchain_openai/chat_models/base/ChatOpenAI)。

首期协议验收必须包含文本生成、标准工具调用、工具结果回传与最终回答；流式响应、usage、结构化输出和 Responses API 分别验证，不根据“OpenAI 兼容”的名称推定可用。供应商非标准响应字段需要专用适配器，参考：[兼容端点的能力范围](https://docs.langchain.com/oss/python/integrations/chat#chat-completions-api)。

`langchain-openai` 已在阶段 2 与 LangChain 一起解析并锁定。真实端点工具兼容性需独立通过验收；提供自定义端点时只声明经过验证的能力，当前结果见 [阶段 2](stages/02-agent-loop.md)。

## 按阶段细化的事项

- 实际服务地址与模型名称、端点能力验证及适配包版本。
- 第三方独立插件包的发现与兼容约定，放到外部插件阶段；首期已确定 YAML 配置与显式插件清单。
- checkpoint 的持久化后端、恢复点范围和执行日志的具体字段。

已接入的模型适配使用 OpenAI SDK；Web 框架、数据库和插件发现库待对应阶段再引入。

## 阶段 3 文件规则

使用 PathSpec 1.1.1 的 GitIgnoreSpec 处理 .gitignore 模式，已通过 uv 加为直接依赖并锁定。父目录排除、嵌套规则与固定敏感路径策略由文件 provider 统一应用；范围和限制见 [只读仓库指南](guides/workspace.md)。
