# 阶段 3：只读仓库问答

状态：代码、本地验收与 review 已完成；C10 在阶段 7.5 最终复验时通过，使用原 .env 完成真实文件工具闭环。公开测试边界与 [阶段 3–7 计划](03-07-delivery-plan.md) 已获用户确认。使用见 [只读仓库指南](../guides/workspace.md)。

## 交付目标

用户通过现有 CLI 指定仓库并提问，Agent 调用列举、检索、读取工具，回答代码问题并引用实际文件路径与行号。文件能力可由插件替换，应用与 Agent 循环无需依赖本地文件系统实现。

参考基线沿用 [参考架构](../reference-architecture.md)。DeepSeek Harness 的 `packages/fs/tool-fs/src/index.ts` 通过 `ctx.fs` 消费文件能力；`read.ts` 将路径、行号和有界读取结果提供给模型。本阶段借鉴能力分离、读取窗口与输出上限，不复制其写入、图片或事件事实源功能。

## 模块与公开契约提案

| 模块 | 本阶段职责 |
| --- | --- |
| contracts | WorkspaceFiles 协议、文件列表、带行号读取结果、搜索命中和文件访问错误；无 LangChain 类型 |
| plugins/filesystem | 本地只读 provider，共享路径、忽略规则、文本类型和资源限额策略 |
| plugins/langchain | 将 WorkspaceFiles 方法适配为模型工具，并给出可引用的结果；工具不直接 open 文件 |
| bootstrap | 从 CLI 传入已确定的工作区，注册文件 provider 和只读工具集，验证 YAML |
| application | 复用 AgentSession，仍只依赖 AgentRuntime |
| interfaces | 保留现有单次与串行多轮入口，使用只读仓库示例配置 |

拟定三个 async 方法：`list(path, limit)`、`read(path, start_line, limit)`、`search(query, path, limit)`。路径为工作区相对路径，读取行号从 1 开始，搜索先支持字面文本。输入校验、输出限额、截断及拒绝原因属于调用者可见契约。

工作区在装配时绑定。请求中的工作区与文件 provider 绑定不一致时应在读文件前失败，不能先返回另一仓库的数据再诊断错误。

列表和搜索跳过被策略禁止的文件，直接读取禁止路径则返回明确拒绝；缺失文件与编码错误使用稳定的领域错误。模型工具将可恢复的文件错误呈现给模型，不泄露被拒绝文件的内容。进程级取消继续沿 AgentRuntime 传播。

文件策略对输入路径和符号链接目标都生效。特殊文件不进入普通读取，目录循环不导致无限遍历。只读策略约束工具权限，不承诺隔离恶意同进程插件或外部进程对目录的任意并发修改。

## 切片顺序和验收

每行按需要细分为单个行为的 red → green，不同时预写整张表的测试。

| 编号 | 行为 | 公开证据 |
| --- | --- | --- |
| C01 | 从真实临时仓库读取指定窗口，路径与行号准确 | WorkspaceFiles.read |
| C02 | 拒绝越界、敏感文件、越界符号链接、特殊文件及非文本 | WorkspaceFiles.read/list/search |
| C03 | 根目录和嵌套 .gitignore、否定规则在三个方法中一致 | WorkspaceFiles.read/list/search |
| C04 | 列表与字面搜索返回确定的路径和行号，结果完整性有明确标记 | WorkspaceFiles.list/search |
| C05 | 文件大小、读取窗口、遍历数量及输出上限有效，取消不会遗留继续运行的遍历 | WorkspaceFiles 与 AgentRuntime.run |
| C06 | YAML 装配文件能力和工具集，宿主自动满足依赖；错误配置在激活前失败 | PluginCatalog + PluginHost |
| C07 | 相同工具使用替代文件 provider，无需修改工具、应用或 Agent 循环 | PluginHost + AgentRuntime.run |
| C08 | Agent 实际执行检索和读取，回答包含工具返回的文件位置 | AgentRuntime.run，确定性外部模型替身 |
| C09 | 用户可从 CLI 对一个示例仓库提问，单次与串行交互继续有效 | 真实 CLI 子进程，本地 HTTP 模型端点 |
| C10 | 配置的真实模型完成文件工具闭环，回答引用的路径和行号可核对 | test_workspace_live.py 显式真实验收已通过，见后续记录 |

C08/C09 的模型响应可以在系统边界替代，文件、工具、宿主和循环使用真实实现。替代 provider 应是公开协议的独立实现，不能靠修改私有字段或 mock 内部函数制造替换成功。

## 完成前 review

- 对照 C01–C10 逐项记录实现位置、运行命令、结果和未完成事项。
- 检查三个文件方法是否共享策略、工作区是否唯一、输出是否有界、错误是否泄露禁止内容。
- 检查文件实现与 LangChain 工具的依赖方向，以及应用是否仍可替换整个运行时。
- 检查阶段 1/2 回归与 CLI 行为；更新使用示例、术语及实际进度。
- 对发现的问题增加已确认边界上的行为测试并修复，再提交、push 和发布 `stage3`；外部端点失败不能写成全部验收通过。

## 本阶段 review 与交付证据（2026-09-25）

规范 review：contracts/application/kernel 不依赖具体文件实现或 LangChain 工具。文件策略集中在 LocalWorkspaceFiles，工具经 WorkspaceFiles 协议消费，配置保持严格 YAML。CLI 注入唯一工作区，默认运行时拒绝其他工作区请求。

目标 review：C01–C09 对应 `test_workspace_files.py`、`test_workspace_agent.py`、`test_workspace_cli.py` 均通过。替代文件 provider 输出不同内容，相同工具和应用正确消费；真实 CLI 子进程通过本地 HTTP 模型完成实际读取。

发现并修复：文件 provider 未显式绑定工作区时会退回当前目录；无效 .gitignore 的解码错误未转换为稳定配置错误。两者均先补失败行为测试，再修复。另覆盖空搜索词、过长搜索词及结果窗口校验。取消与符号链接循环在当前实现中直接通过，作为回归证据，不宣称这些用例经历了 red。

首批 red → green 包括：读取接口不存在、越界链接被读取、敏感别名未拒绝、二进制被接受、超限文件被加载、嵌套忽略未生效、窗口与输出无限制、遍历限额缺失、YAML 装配缺失、工作区不一致未拒绝、拒绝路径无法返回工具错误、CLI 未传入工作区。端到端与替换性在相应实现就绪后直接通过，作为独立验收。

执行结果：全套本地测试 124 项通过，2 项 live 默认跳过；Ruff、格式检查、mypy 和 sdist/wheel 构建通过。独立 wheel 的 6 项 CLI 测试通过（原有 5 项加仓库读取 1 项）。

C10：显式执行 `pytest tests/test_workspace_live.py --run-live --tb=short`，使用原 .env，约 3 秒返回 `AgentExecutionError <- OpenAIRateLimitError <- RateLimitError`。测试只向模型发送临时生成的两行代码。未将端点失败伪装为成功；`stage3` 标记代码与本地验收里程碑，完整真实验收仍待端点可用。


### 阶段 7.5 真实复验（2026-09-25）

显式执行 `pytest tests/test_agent_live.py tests/test_workspace_live.py --run-live -q`：仓库验收 **1 passed**，加法/追问验收 **1 failed**（RateLimitError）。C10 实际执行 read_file，读取临时 answer.py 并在最终回答中返回 42、文件名与第 2 行，所有断言通过。保留 stage3 当时未通过的历史记录；该次复验关闭 C10，B11 当时仍未通过（后续已通过，见阶段 2 最新记录）。未更换模型、地址或凭据。
