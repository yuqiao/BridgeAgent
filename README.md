# BridgeAgent

目标：构建一个参考 DeepSeek Harness 的可插件化 Agent。

**当前阶段：阶段 1 已完成；阶段 2/3 已实现 Agent 闭环和只读仓库工具，真实端点验收受限流阻塞。**
支持 YAML 插件装配、LangChain 运行时、OpenAI 兼容模型、加法工具和内存多轮会话。
真实端点当前返回限流，验收状态见 [阶段 2](docs/stages/02-agent-loop.md)。完整 Cordis 风格动态插件机制在阶段 7 实现。

阶段 4 已支持 SQLite 持久会话与跨进程恢复，见 [会话指南](docs/guides/sessions.md)。

架构讨论、技术栈与阶段路线图见 [设计文档](docs/README.md)。
只读仓库问答见 [使用指南](docs/guides/workspace.md)，配置为 `examples/workspace.yaml`。基础 Skill 已接入，见 [Skill 指南](docs/guides/skills.md) 与 `examples/skills.yaml`。
面向编码 Agent 的仓库指南见 [AGENTS.md](AGENTS.md)。

## 开发环境

- Python 3.13+，本地开发和 CI 使用 `.python-version` 指定的 3.13。
- [uv](https://docs.astral.sh/uv/getting-started/installation/)：统一管理 Python、虚拟环境和依赖。
  初始化与 CI 使用 uv 0.8.17。

```bash
uv sync --locked
```

此命令创建 `.venv`，以可编辑方式安装 `bridge-agent` 及开发依赖。
缺少对应 Python 时，uv 会自动下载。
发行包名为 `bridge-agent`，Python 导入名为 `bridge_agent`。

锁文件使用官方 PyPI 源。如果本机通过 `UV_DEFAULT_INDEX` 配置了镜像源，
请先在当前终端执行 `export UV_DEFAULT_INDEX=https://pypi.org/simple`，
让安装源与锁文件一致。

## 常用命令

```bash
# 代码检查、格式检查、类型检查
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest

# 自动格式化
uv run ruff format .

# 构建 wheel 和源码分发包，输出到 dist/
uv build
```

CI 执行静态检查、行为测试、构建，以及独立 wheel 环境中的示例运行。
测试边界与开发方式见 [tests/README.md](tests/README.md)。

## 运行插件示例

无需模型和密钥，在仓库根目录执行：

```bash
uv run --locked python -m bridge_agent.interfaces.plugin_demo --config examples/plugins.upper.yaml --text Hello
# result: HELLO
uv run --locked python -m bridge_agent.interfaces.plugin_demo --config examples/plugins.lower.yaml --text Hello
# result: hello
```

两份 YAML 为相同消费者选择不同服务实现；消费者位于配置列表前面，宿主仍按依赖启动。
失败会返回非零退出码。接口、生命周期和验收证据见 [阶段 1](docs/stages/01-plugin-host.md)。

开发自己的插件请看 [插件使用与开发指南](docs/guides/plugins.md)，其中的反转文本示例已可运行：

```bash
uv run --locked python -m bridge_agent.interfaces.plugin_demo --config examples/plugins.reverse.yaml --text Hello
# result: olleH!
```

## 运行 Agent

在本地 `.env` 配置 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL`（见 [.env.example](.env.example)），然后执行：

```bash
uv run --locked python -m bridge_agent.interfaces.agent \
  --config examples/agent.yaml --env-file .env --env-override \
  --workspace . --prompt "请调用 add 工具计算 1847 + 2965。" --show-tools
```

省略 `--prompt` 进入串行多轮，支持 `/new`、`/exit` 和 Ctrl+C。
`--env-override` 使指定文件中的配置优先，避免已有进程环境覆盖项目凭据。
当前工具只做加法，不读取工作区文件。配置、限制、Python 接口与真实验收命令见 [Agent 使用指南](docs/guides/agent.md)。

默认 `pytest` 不调用真实模型；显式验收使用 `uv run --locked pytest tests/test_agent_live.py --run-live -v --tb=short`。

## 依赖管理

```bash
uv add <package>             # 添加运行时依赖
uv add --dev <package>       # 添加开发依赖
uv remove <package>          # 移除运行时依赖
uv lock --upgrade           # 主动升级锁定依赖，需检查变更并重新验证
```

将 `pyproject.toml` 和 `uv.lock` 一起提交到版本控制。
日常安装使用 `uv sync --locked`，避免无意更新锁文件。

## 目录结构

```text
BridgeAgent/
├── .github/workflows/ci.yml  # 静态检查、行为测试、构建、安装验证
├── .editorconfig            # 编辑器基础格式约定
├── .python-version          # 开发 Python 版本
├── AGENTS.md                # 编码 Agent 的仓库指南
├── docs/                    # 架构、路线图、术语与决策
├── examples/                # 可直接运行的 YAML 装配示例
├── pyproject.toml           # 包元数据、依赖和工具配置
├── uv.lock                  # uv 生成的依赖锁文件
├── src/bridge_agent/
│   ├── __init__.py          # 最小包入口
│   ├── contracts/           # 插件协议与能力定义
│   ├── kernel/              # 依赖计划、宿主与资源所有权
│   ├── bootstrap/           # YAML 校验与显式插件清单
│   ├── plugins/             # 能力实现与消费者插件
│   ├── application/         # 只依赖能力接口的应用用例
│   ├── interfaces/          # 插件演示命令
│   └── py.typed             # 类型信息标记
├── tests/                   # 配置、宿主、类型与命令验收
├── LICENSE
└── README.md
```

## 工程约定与依据

- 使用标准 `pyproject.toml` 管理元数据，Hatchling 构建分发包：
  [PyPA 项目配置指南](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)。
- 使用 `src/` 布局，将可导入代码与仓库根目录分开，便于验证安装后的包：
  [PyPA 对两种布局的说明](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)。
- 使用 uv 的本地环境和锁文件管理依赖：
  [uv 项目结构](https://docs.astral.sh/uv/concepts/projects/layout/)。
- 使用独立测试目录和 `importlib` 导入模式：
  [pytest 集成实践](https://docs.pytest.org/en/stable/explanation/goodpractices.html)。
- Ruff 负责 lint、导入排序和格式化，mypy 使用严格模式。

采用 MIT 许可证，详见 [LICENSE](LICENSE)。

修改代码和运行测试见 [修改指南](docs/guides/coding.md)，默认逐次确认。
