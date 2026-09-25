# 阶段 5：修改与测试

基线：stage4 / 8ad08bd。实现经本地 review；真实模型工具端点的既有 429 限制仍在阶段 2/3 单独保留，以下是确定性验收，不冒充真实模型通过。

## 交付与验收

| 目标 | 证据 |
| --- | --- |
| 预览无副作用、批准后原子替换、新文件、冲突检测、失败保留原文件 | tests/test_workspace_changes.py |
| 拒绝不写、审批期间外部修改、重复及并发同 ID 不重复写 | tests/test_workspace_changes.py |
| 保护/忽略/符号链接边界、新旧内容限额 | tests/test_workspace_changes.py、tests/test_workspace_files.py |
| 实际子进程退出码、环境、输出、超时、取消与去重 | tests/test_commands.py |
| YAML 预检、宿主关闭后拒绝操作 | tests/test_action_plugins.py |
| 人工同意/拒绝/EOF、展示实际作用范围 | tests/test_console_approval.py |
| 实际 Agent 完成修改、执行真正验证命令；Skill 驱动相同流程 | tests/test_coding_agent.py |
| 真正 CLI + 本地 HTTP 模型 + PTY 审批；非交互默认拒绝 | tests/test_coding_cli.py |

TDD 按能力逐条 red → green：预览与应用、审批/冲突/重复、真实命令执行、超时/取消、Agent 闭环、宿主清理、配置校验、命令名可见、Skill 工具集、终端审批。首次失败包括缺少接口、未拒绝非法配置、未接入终端审批等；保护路径是共享策略的回归验收，未虚构新的失败。

## Review

规范：contracts 不依赖 LangChain；文件策略与能力实现位于 plugins；工具适配消费协议；bootstrap 显式装配，CLI 提供审批。review 将读写共有路径策略抽到内部 FilePolicy，避免写能力依赖读能力私有方法。

目标：默认拒绝，精确配置授权，模型不可自批，Skill 不自动授权。发现命令异常缺少领域错误、关闭后仍可尝试运行、工具描述未列出模板、旧文件造成大 diff；均补测试后修复。命令资源登记到宿主，输入共用一个 reader，避免聊天/审批争用。复核了单文件替换失败与取消的清理路径。

使用与边界见 [修改指南](../guides/coding.md)。不承诺跨文件事务、跨重启恰好一次、操作系统沙箱或 Windows 进程组支持。完整本地回归、静态检查、wheel 构建和独立安装 CLI 验收纳入发布检查。

发布检查实际结果：193 passed、2 skipped；Ruff 检查/格式、mypy（45 个源码文件）、uv build 通过。独立安装 wheel 后运行 tests/test_coding_cli.py：3 passed。
