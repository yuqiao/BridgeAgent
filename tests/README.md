# 测试

功能测试放在此目录，文件名使用 `test_*.py`。
通过 `uv run --locked pytest` 运行，使用 `importlib` 模式导入已安装的项目包，
无需手动修改 `PYTHONPATH`。

## 已确认的测试边界

沿用用户在阶段 1 实施设计 P1-Q4 中确认的验收边界：

- `PluginCatalog.load(path)`：真实 YAML 到已验证插件配置。
- `PluginHost.start/resolve/close`：装配、服务消费与生命周期结果。
- 真实插件收到的 `PluginContext`：声明约束、资源所有权与清理结果。
- 演示模块命令：退出码、标准输出及错误定位。
- 类型契约：插件作者经 mypy 使用服务键注册、读取服务，验证输入限制和返回类型。

测试不直接调用内部 planner、注册表、OwnedContext 或私有清理方法，
不 mock 宿主内部协作。测试插件是接口的真实消费者。

用户要求 TDD 前已存在一版实现；其既有行为补回归验证，后续行为修改
先写失败测试、确认失败原因，再实现最小修复。每次围绕一个行为推进，
不将后来补写的回归测试称为最初的 red → green 证据。

## 已执行的 red → green

| 行为 | 先观察到的失败 | 最小修正 |
| --- | --- | --- |
| 服务键拒绝错误类型的注册值 | `ServiceKey[str]` 注册整数仍通过 mypy | 显式不变泛型，禁止推断为共同父类型 |
| YAML 重复键可定位 | 只有笼统的 YAML 错误 | 受控诊断保留重复键类别与行列 |
| 非法显式 YAML 标量属于配置错误 | PyYAML 抛出 ValueError / IndexError / KeyError，可能包含配置值 | 在解析边界转为不带输入值的配置错误 |

`test_config.py` 验证配置边界，`test_host.py` 验证生命周期，
`test_demo.py` 运行真实命令，`test_typing.py` 验证公开类型契约。
异步测试使用真实 asyncio 事件门，不依赖网络、模型密钥或固定等待时长。
并发测试的 `sleep(0)` 仅让调度器交付已经请求的取消，不模拟耗时。

## 阶段 2

用户已确认四个公开测试边界：AgentRuntime.run、YAML + 插件宿主装配、真实 CLI、显式外部端点验收，详见 `docs/stages/02-agent-loop.md`。

- `test_agent_runtime.py`：真实 LangChain 循环，仅替换外部模型响应；覆盖工具回传、多轮、隔离、上限、取消、失败与日志。
- `test_agent_plugins.py`：真实 YAML 与插件宿主，验证替换整个运行时及扩展模型/工具集。
- `test_agent_config.py`：显式环境读取、优先级、不修改进程环境、严格配置。
- `test_openai_adapter.py`：真实 ChatOpenAI SDK 调用本地 HTTP 端点，验证协议与客户端关闭。
- `test_agent_cli.py`：子进程验证单次、多轮、EOF、取消和退出码。`BRIDGE_AGENT_CLI_PYTHON` 可指定独立 wheel 环境的解释器。
- `test_agent_live.py`：只有显式 `--run-live` 才读取项目 `.env` 并调用真实端点；文件值优先，避免全局凭据干扰。

阶段 2 逐个行为执行 red → green。公开替换接口和框架本身已支持的行为直接通过时，作为验收证据，不制造虚假的失败。真实服务未通过时保留失败，不将其改为跳过或使用模拟结果替代。

## 阶段 3

边界已统一确认于 `docs/stages/03-07-delivery-plan.md`。`test_workspace_files.py` 经 WorkspaceFiles 验证真实文件、统一策略和限额；`test_workspace_agent.py` 经 YAML/宿主/AgentRuntime 验证装配、替换与实际工具闭环；`test_workspace_cli.py` 使用真实子进程与本地模型端点。`test_workspace_live.py` 显式启用真实模型，仅发送临时生成的代码。

## 阶段 3.5

`test_skills.py` 在 SkillCatalog 公开方法验证发现、加载、资料范围与错误；`test_skill_agent.py` 用实际宿主和循环验证仅选中正文进入模型上下文；`test_workspace_cli.py` 新增 Skill 参数用例验证独立 CLI 进程。

## 阶段 4

`test_persistent_sessions.py` 通过公开 Agent/SessionControl 与插件宿主验证持久恢复、隔离、兼容与资源归属；`test_persistent_cli.py` 使用独立 CLI 进程验证重启、数据库占用和 SIGKILL 后拒绝重放。状态与历史均来自 checkpoint，测试不直接查询数据库表。

阶段 5 在已确认的 preview/apply、run/cancel、审批、YAML、AgentRuntime、CLI 边界逐条 TDD。命令使用实际子进程，审批 CLI 使用 PTY；仅模型与原子替换故障注入使用外部边界替身。具体证据见 docs/stages/05-edit-and-test.md。
