# BridgeAgent 设计文档

本轮目标：参考 DeepSeek Harness，确定原生 Python 插件化 Agent 的架构和分阶段路线。当前仅开展设计，不实现功能。

## 阅读顺序

1. [技术栈](tech-stack.md)：已确定的技术约束、版本核实与待定职责。
2. [参考架构](reference-architecture.md)：DeepSeek Harness 的实际机制及与本项目的区别。
3. [目标架构](architecture.md)：分层、依赖方向与状态归属。
4. [阶段路线图](roadmap.md)：阶段依赖和验收方式。
5. [领域术语](CONTEXT.md)：已确定概念的统一名称。
6. [设计讨论](design-session.md)：决策树、已确认事项和当前问题。
7. 架构决策：[分层优先](adr/0001-layered-plugin-architecture-first.md)、[LangChain 运行时](adr/0002-langchain-runtime-provider.md)、[checkpoint 恢复状态](adr/0003-checkpoints-own-recovery-state.md)。

## 文档维护

- 区分参考项目事实、候选建议和用户已确认决策；草案不代表实现授权。
- 每轮讨论及时更新决策树，并调整受影响的阶段、范围和验收条件。
- 领域术语明确后写入 `docs/CONTEXT.md`，仅记录术语定义，不混入实现细节。
- 有实际取舍且难以逆转的架构决策才写入 `docs/adr/`；普通工具配置记录在技术栈文档。
- 每阶段实现完成后，按验收证据更新状态；文档中的计划能力不计作已实现能力。

设计日期：2026-09-25。当前状态：设计基线已定稿，应用配置采用 YAML 显式装配；尚未开始功能实现。
