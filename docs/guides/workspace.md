# 只读仓库问答

使用阶段 3 的 YAML 装配文件能力与工具。在仓库根目录执行：

```bash
uv run --locked python -m bridge_agent.interfaces.agent \
  --config examples/workspace.yaml \
  --env-file .env --env-override \
  --workspace /path/to/repository \
  --prompt '请找出一个主要入口函数，读取代码并引用文件路径与行号解释它。' \
  --show-tools
```

去掉 `--prompt` 即可串行多轮交互，支持 `/new`、`/exit` 和 EOF。凭据仍读取 `.env` 中的三个 OPENAI 环境变量；模型必须支持工具调用。2026-09-25 真实 read_file 闭环与文件位置引用已通过；加法/多轮的独立真实验收仍限流，端点可用性并不稳定。

`files.local` 提供框架无关的 `WorkspaceFiles`，`tools.workspace` 将它适配为 `list_files`、`read_file`、`search_files`。`read_file` 返回 `{path, lines: [{number, text}], truncated}`；搜索返回 `{hits: [{path, line, text}], truncated}`。行号从 1 开始，搜索按字面文本匹配。模型回答中的引用仍须与工具结果核对。

Python 装配时必须显式传入 `agent_catalog(workspace=Path(...), ...)`；CLI 会自动传入 `--workspace`。默认运行时也绑定这个工作区，不能在同一实例中提交其他目录。

路径仅接受工作区相对路径，不接受绝对路径、`..` 或空路径。列表和搜索跳过禁止文件，直接读取则返回错误。根目录及嵌套 `.gitignore` 对三个工具均生效；已经被忽略的父目录不能由其中的规则重新开放。规则基于 [PathSpec GitIgnoreSpec](https://python-path-specification.readthedocs.io/en/latest/readme.html)，不读取全局 Git excludes 或 `.git/info/exclude`。

固定保护项包括任意层级的 `.git`、`.ssh`、以 `.env` 开头的名称、`id_rsa` / `id_dsa` / `id_ecdsa` / `id_ed25519`，以及 `.pem` / `.key` / `.p12` / `.pfx` 后缀（大小写均匹配）。这意味着 `.env.example` 也默认不可读。符号链接的原路径与真实目标均校验，越界目标不可读取；遍历不递归进入符号链接目录。

只处理普通 UTF-8 文本，包含 NUL 的文件视为二进制。限制如下：

| 项目 | 当前上限 |
| --- | --- |
| 单文件大小 | 1 MiB，超过则拒绝 |
| 单次读取行数、列表条数、搜索命中数 | 200，调用者可调低 |
| 读取/搜索返回的文本字符数 | 16,000，达到限制时标记截断 |
| 搜索词长度 | 1–1,000 字符 |
| 遍历目录项数量 | 默认 10,000；YAML `files.local.config.max_entries` 可设 1–100,000 |
| 单个 `.gitignore` 大小 | 64 KiB，损坏或超限会报配置错误 |
| 整轮与模型超时 | 示例均为 600 秒 |

路径、行号等结构信息也受结果条数与本地路径长度约束。`truncated=true` 表示结果不完整；遍历预算耗尽后的搜索不能证明仓库中不存在其他命中。文件输出中的指令仅作为仓库内容，不会改变工具权限。

这不是恶意插件或任意并发文件系统变更的操作系统沙箱。文件 provider 是可信 Python 插件；本阶段不提供写文件与命令执行工具。

本地验证：

```bash
uv run --locked pytest tests/test_workspace_files.py tests/test_workspace_agent.py tests/test_workspace_cli.py
```

真实模型验证（只向模型发送测试生成的两行代码）：

```bash
uv run --locked pytest tests/test_workspace_live.py --run-live --tb=short
```

完整验收状态见 [阶段 3](../stages/03-workspace-files.md)。
