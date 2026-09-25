# 阶段 4：持久会话与可靠续接

状态：实现、本地验收和 review 已完成。基线 `stage3.5`，已确认边界为 AgentRuntime、会话查询能力、YAML checkpoint 切换与 CLI 跨进程恢复。使用见 [持久会话指南](../guides/sessions.md)。

实现使用官方 [AsyncSqliteSaver](https://reference.langchain.com/python/langgraph/checkpoints)（langgraph-checkpoint-sqlite 3.1.1）。SQLite 用于本地单 Agent，不作为多用户生产存储。checkpoint 同时持有历史、工作区、完成状态和配置兼容指纹。

| 编号 | 目标 | 证据 |
| --- | --- | --- |
| P01 | YAML 切换到真实 SQLite，重开宿主仍记住上一轮 | test_persistent_sessions |
| P02 | 独立进程退出并重开可续接相同会话 | test_persistent_cli；第二个模型请求包含第一轮历史 |
| P03 | 不同工作区、未完成状态或不兼容配置拒绝恢复 | 工作区、失败重开、配置改变测试 |
| P04 | 查询状态无需模型请求，显示完成与兼容状态 | SessionControl 与真实 CLI 的模型请求计数 |
| P05 | 强杀进程后保留 incomplete，不自动重放 | 真实 CLI SIGKILL 后查询和续接拒绝；模型请求数不增加 |
| P06 | 数据库单宿主占用，关闭后可再次打开 | 嵌套宿主与 CLI 并发占用测试 |
| P07 | 无效数据库目标、CLI 错误与清理可诊断 | 非文件目标、占用消息、插件关闭测试与前序回归 |

TDD 记录：SQLite 插件不存在、跨工作区恢复被接受、失败会话重启后可继续、状态接口缺失、配置改变后静默恢复、同时打开同库、CLI 参数缺失、兼容状态缺失、占用原因被包装隐藏及数据库错误缺少领域原因，分别经历失败测试与最小修复。

规范 review：新增会话查询为独立 SessionControl 能力，AgentRuntime.run 保持可替换。LangGraph 状态字段留在适配层，contracts 不暴露图和 checkpoint 对象。SQLite 连接与文件锁归属插件 context。checkpoint 服务键允许内存整数版本与 SQLite 字符串版本，保留类型检查而非抹为 Any。

目标 review：正常重启、故障与进程中断均有实际行为证据。review 核对上游持久化语义后显式选择 sync，防止默认异步写入与下一步执行并行导致未完成标记落盘滞后；未另建状态事实源。当前采用保守恢复，完成标记写入前崩溃也视为未完成。

限制：macOS/Linux 本地文件锁；同数据库不支持多个同时活跃的宿主；不自动迁移旧状态、不重放失败调用；真实端点限流状态独立保留。

交付检查：153 项测试通过、2 项 live 默认跳过；Ruff、格式、mypy、sdist/wheel 构建通过；独立 wheel 的 2 项持久 CLI 测试通过。真实模型工具端点的未通过项未被改为成功。
