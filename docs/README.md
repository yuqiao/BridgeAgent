# BridgeAgent 设计文档

项目目标：参考 DeepSeek Harness，分阶段实现原生 Python 插件化 Agent。阶段 1 已完成，阶段 2 Agent 闭环已实现，真实端点验收进行中。

阶段 3 已实现只读仓库问答，本地验收通过，真实端点仍返回限流；见 [使用指南](guides/workspace.md) 与 [阶段验收](stages/03-workspace-files.md)。

实际使用与扩展插件，请先阅读 [插件使用与开发指南](guides/plugins.md)：配置运行、完整开发示例、依赖协作、资源生命周期和排错。

运行模型、工具和多轮对话，请阅读 [Agent 使用指南](guides/agent.md)，验收结果见 [阶段 2](stages/02-agent-loop.md)。

## 阅读顺序

1. [技术栈](tech-stack.md)：已确定的技术约束、版本核实与待定职责。
2. [参考架构](reference-architecture.md)：DeepSeek Harness 的实际机制及与本项目的区别。
3. [目标架构](architecture.md)：分层、依赖方向与状态归属。
4. [阶段路线图](roadmap.md)：阶段依赖和验收方式。
5. [领域术语](CONTEXT.md)：已确定概念的统一名称。
6. [设计讨论](design-session.md)：决策树、已确认事项和当前问题。
7. 架构决策：[分层优先](adr/0001-layered-plugin-architecture-first.md)、[LangChain 运行时](adr/0002-langchain-runtime-provider.md)、[checkpoint 恢复状态](adr/0003-checkpoints-own-recovery-state.md)、[异步生命周期与清理错误](adr/0004-async-plugin-lifecycle-and-cleanup.md)。
8. [阶段 1 交付文档](stages/01-plugin-host.md)：已实现功能、概念、接口与验收证据。
9. [阶段 3–7 实施计划](stages/03-07-delivery-plan.md)：后续全部已编号阶段的测试边界提案、review 与逐阶段发布约定。

## 文档维护

- 区分参考项目事实、候选建议和用户已确认决策；草案不代表实现授权。
- 每轮讨论及时更新决策树，并调整受影响的阶段、范围和验收条件。
- 领域术语明确后写入 `docs/CONTEXT.md`，仅记录术语定义，不混入实现细节。
- 有实际取舍且难以逆转的架构决策才写入 `docs/adr/`；普通工具配置记录在技术栈文档。
- 每阶段实现完成后，按验收证据更新状态；文档中的计划能力不计作已实现能力。

设计日期：2026-09-25。当前状态：阶段 0、1 完成，应用配置采用 YAML 显式装配；后续阶段 3.5 提供基础 Skills，阶段 5 扩展修改与脚本执行，阶段 7 实现完整 Cordis 风格插件机制。

阶段 3.5 的 Skill 使用见 [指南](guides/skills.md)，验收与 review 见 [阶段文档](stages/03.5-skills.md)。
