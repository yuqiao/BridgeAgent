# BridgeAgent

目标：构建一个参考 DeepSeek Harness 的可插件化 Agent。

**当前阶段：工程初始化与架构设计已完成。** 尚未实现 Agent 循环、模型调用、
工具执行或插件发现机制，运行时依赖为空。后续按已确定的分层和路线图逐阶段实现。

架构讨论、技术栈与阶段路线图见 [设计文档](docs/README.md)。
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

# 自动格式化
uv run ruff format .

# 构建 wheel 和源码分发包，输出到 dist/
uv build
```

pytest 已配置，测试放在 `tests/`，添加测试后使用 `uv run pytest`。
目前没有测试用例，pytest 会返回退出码 5；CI 暂时验证静态检查、构建和 wheel 安装导入。
添加首个功能测试时应同步在 CI 中启用 pytest。

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
├── .github/workflows/ci.yml  # 静态检查、构建、安装验证
├── .editorconfig            # 编辑器基础格式约定
├── .python-version          # 开发 Python 版本
├── AGENTS.md                # 编码 Agent 的仓库指南
├── docs/                    # 架构、路线图、术语与决策
├── pyproject.toml           # 包元数据、依赖和工具配置
├── uv.lock                  # uv 生成的依赖锁文件
├── src/bridge_agent/
│   ├── __init__.py          # 最小包入口
│   └── py.typed             # 类型信息标记
├── tests/                   # 后续测试
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
