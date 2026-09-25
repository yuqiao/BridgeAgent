# BridgeAgent 开发指南

## 项目目标与当前状态

BridgeAgent 是参考 DeepSeek Harness 架构、使用 Python 独立实现的插件化本地编码助手。

阶段 0、1 已完成；阶段 2 已实现 LangChain Agent 闭环、OpenAI 兼容模型、加法工具、内存 checkpoint 与串行 CLI，真实端点验收尚待通过。使用见 `docs/guides/agent.md`，逐项验收见 `docs/stages/02-agent-loop.md`。以 [阶段路线图](docs/roadmap.md) 和实际代码判断进度，不把计划能力当作已有功能。

## 开始工作前

- 先查看 `git status --short`，保留已有修改。
- 阅读 [目标架构](docs/architecture.md)、[阶段路线图](docs/roadmap.md) 和 [领域术语](docs/CONTEXT.md)。技术选择见 [技术栈](docs/tech-stack.md)，决策理由见 [文档索引](docs/README.md) 中的 ADR。
- 按用户当前授权的阶段推进。初始化或文档任务不隐含实施全部路线图；已确定的选择直接沿用，常规实现细节在当前范围内解决。
- 默认用中文沟通与维护设计文档，代码标识符使用英文。

## 代码组织与依赖方向

发行包名为 `bridge-agent`，导入包名为 `bridge_agent`，源码位于 `src/bridge_agent/`。以下模块已有阶段 1 实现；后续仅按真实消费需求扩展接口。

| 目标模块 | 职责与限制 |
| --- | --- |
| `contracts/` | 能力与插件协议；不导入具体实现或交互入口 |
| `kernel/` | 插件依赖、注册、激活、关闭与资源归属；不实现 Agent 循环 |
| `application/` | 应用用例；通过能力接口工作，不构造具体模型或 LangChain Agent |
| `plugins/` | 运行时、模型、工具、存储等能力实现及框架适配 |
| `interfaces/` | CLI 等输入输出；调用应用用例 |
| `bootstrap/` | 读取配置、选择实现、装配与启动；业务模块不反向导入它 |

从第一个可运行示例就遵守分层。初期保持单一 Python 发行包，接口只覆盖真实消费需求。参考项目使用 Cordis 与 Node；其 Python SDK 不是本项目的原生 Python 实现模板。参考基线和源码依据见 [参考架构](docs/reference-architecture.md)。

## 已确定的设计约束

- Python 开发与 CI 使用 3.13，uv 管理环境和依赖。LangChain 已在阶段 2 接入并锁定，版本见 `uv.lock`，不在运行时自动升级。
- 默认 Agent 运行时封装 LangChain `create_agent`，通过可替换接口暴露给应用层。BridgeAgent 管理插件宿主，不再维护第二套竞争的执行循环。
- 应用配置使用 **YAML + 显式插件清单**。YAML 仅承载数据，不执行代码或构造任意对象；密钥引用环境变量。`pyproject.toml` 仍负责 Python 工程配置。
- 首期为可信同进程插件，启动时装配；验证缺失依赖、循环依赖和重复注册，支持激活失败回滚与退出清理。阶段 6 增加外部插件包，阶段 7 实现完整 Cordis 风格插件机制；不把动态依赖、Context 作用域和热重载提前塞入阶段 1。不可信插件隔离另行评估。
- 首期为单工作区、单 Agent、串行多轮 CLI。先实现只读仓库问答，再增加修改与测试；工作区读取约束以目标架构为准。
- 默认运行时以 checkpoint 管理恢复状态，执行日志用于观察与排障。应用层不维护第二份权威消息历史，不承诺从日志重建所有模型请求。
- 模型适配以 OpenAI Chat Completions 兼容服务为起点，验证工具调用与工具结果回传形成闭环；其他能力单独验证。

## 开发命令

在仓库根目录运行：

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
uv build
```

格式化使用 `uv run ruff format .`。新增运行时依赖使用 `uv add <package>`，开发依赖使用 `uv add --dev <package>`，同时保留 `pyproject.toml` 与 `uv.lock` 的一致变更，不手工编辑锁文件。

锁文件使用官方 PyPI。若本机设置了不同的 `UV_DEFAULT_INDEX`，可在当前终端执行 `export UV_DEFAULT_INDEX=https://pypi.org/simple` 后再运行上述命令。

## 验证与测试

- Python 代码使用 Ruff 格式与检查规则、mypy 严格模式，具体配置以 `pyproject.toml` 为准。
- 测试放在 `tests/`，文件命名为 `test_*.py`，通过 `uv run --locked pytest` 运行。已配置 `importlib` 导入模式，不用手动修改 `PYTHONPATH` 掩盖安装问题。
- CI 已启用 pytest，并验证 wheel 独立安装后的三份 YAML 示例及无效配置退出码。测试边界与 TDD 记录见 `tests/README.md`，插件开发见 `docs/guides/plugins.md`。
- 依据改动运行相关检查。行为测试验证能力替换、生命周期、失败路径和阶段验收，不为占位接口制造测试。
- 模型确定性测试只使用替代模型或本地 HTTP 端点；真实验证需显式 `--run-live`，项目 `.env` 值优先，默认测试跳过。报告实际结果，不把模拟调用描述为真实 API 验证。不得输出凭据或将 `.env` 加入版本控制。
- Agent Runtime 公开请求与结果位于 contracts；LangChain 模型、工具、checkpoint 服务键位于适配层，application/kernel 不导入框架类型。未完成会话要求新 session，不自动重放。
- 打包改动验证 wheel 安装后能导入；仅文档变更检查链接、内容一致性与 `git diff --check`，无需运行模型或全套测试。

## 文档与交付

- 文档集中维护在 `docs/`，术语表为 `docs/CONTEXT.md`，仅记录领域定义。
- 修改能力、配置或阶段范围时同步相关文档。阶段完成需有可运行结果和验收证据，再更新路线图状态。
- 只有存在实际取舍、未来难以逆转且需要解释原因的决策才新增 ADR，使用 `docs/adr/` 的顺序编号。
- 不提交凭据、虚拟环境、缓存或构建产物。交付时说明修改、已执行的验证及未完成事项；提交和推送遵循用户当前指令。
