# DeepSeek Harness 参考架构

## 参考基线

- 本地仓库：`~/Projects/deepseek-harness`，调研时工作区干净。
- 提交：`46a7f68b0922371ce7144b668b90e377d8e799f4`。
- 主要依据：[架构文档](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/docs/architecture.md)、[Cordis 入门](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/docs/cordis-primer.md)。

以下为参考项目事实，不表示 BridgeAgent 已采纳或实现。

## 核心机制

| 机制 | 参考项目的设计 | 对 BridgeAgent 设计的启发 |
| --- | --- | --- |
| 一切皆插件 | 模型适配器、工具注册表、会话日志和 Agent 循环都通过 Cordis 插件提供 | 先明确哪些能力可替换，避免把插件简单等同于工具函数 |
| 组合启动 | profile 组合 bundle，再按顺序应用配置补丁，生成插件树 | 将能力装配与业务执行分开；多层配置是否必要需单独判断 |
| 生命周期 | 服务、事件监听和其他注册归属插件上下文，卸载时清理相关 effect | 插件应明确资源所有权、激活失败和退出清理语义 |
| 能力接口 | Service Definition、Service Provider、Consumer 共同形成可替换能力 | 接口、实现和使用者需要一起验证，不能只写一个空协议 |
| 执行扩展 | 模型请求、工具执行和 turn 等位置提供扩展事件 | 插件通过明确的扩展点参与执行；应对照 LangChain middleware 能力 |
| 会话事实 | 持久化 Session events、实时 Agent events、能力事件各有职责 | 持久化记录与实时通知应明确区分 |
| 模型上下文 | 从 Session 日志投影消息；模型可见输入必须可从日志重建 | checkpoint、对话记录和完整请求审计不能直接视为同一个东西 |

参考位置：架构文档的 `Cordis`、`Profiles and bundles`、`Events`、`Turn flow`、`Session log`、`Capability seams` 和 `Where new behavior goes` 各节。

## 源码交叉验证

- 插件 effect 返回 disposer，资源按逆序清理：[Cordis fiber](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/vendor/cordis/src/fiber.ts#L402)。依赖实现变化还会影响激活状态：[依赖刷新](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/vendor/cordis/src/fiber.ts#L611)。BridgeAgent 首期仅采纳明确生命周期，不承诺这一整套动态依赖机制。
- 默认循环通过声明依赖并安装 Agent factory 接入系统：[agent-loop](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/core/agent-loop/src/index.ts#L330)。这支持将 Agent 接口与默认执行器分开的设计。
- 请求构建通过 `session.deriveMessages()` 得到模型历史：[buildRequest](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/core/agent-loop/src/agent.ts#L580)。这比附带写一份运行日志更强。
- 文件工具依赖文件能力服务，而不是直接耦合本地实现：[tool-fs](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/fs/tool-fs/src/index.ts#L18)、[read 工具](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/fs/tool-fs/src/read.ts#L146)。这是“接口、实现、使用者”分离的具体例子。

## 执行流程

```text
装配插件树 → 服务就绪 → 创建 Agent
  → 接收输入 → 组装上下文与工具定义
  → 模型请求 → 执行工具 → 将结果加入后续模型输入
  → 继续下一步或结束本轮
关闭应用 → 按资源归属清理
```

参考项目中，一个 step 包含一次模型请求及其工具调用；一个 turn 可以包含零个或多个 step。此处用于解读参考项目，BridgeAgent 的对应术语待讨论后收录到术语表。

## Python 实现的区别

DeepSeek Harness 自带的 Python SDK 会启动打包的 Node `dsh` runtime，并使用 SDK profile；它并非原生 Python 的 Cordis 或 Agent 循环实现。参见架构文档 `Application launch` 一节。

BridgeAgent 已选 Python 3.13 与 LangChain，因此不能直接把该 Python SDK 当作内部架构模板。尤其需要决定：LangChain 管理运行中的 Agent，BridgeAgent 管理可装配能力时，两者分别拥有什么状态、扩展点和资源。

## 候选借鉴范围

建议优先讨论：显式能力接口、组合入口、插件生命周期、工具执行扩展、可验证的会话行为。

建议后置评估：热重载、多层 profile 补丁、远程执行、浏览器与桌面 UI、复杂历史会话迁移、多 Agent 协作。具体延期范围要根据首个任务场景确认。
