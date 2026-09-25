# 持久会话与恢复

阶段 4 使用 `checkpoint.sqlite` 替换内存 checkpoint。适用于本地单 Agent；当前文件锁实现面向 macOS/Linux。数据库保存模型消息和执行状态，不是脱敏日志，请按本地工作资料管理。

在项目根目录首次运行：

```bash
uv run --locked python -m bridge_agent.interfaces.agent \
  --config examples/persistent.yaml --workspace . \
  --env-file .env --env-override --session-id repository-study \
  --prompt '请读取 README.md 并解释项目当前能力，记住你的结论。'
```

另起进程，以同一配置、工作区和 session ID 续接：

```bash
uv run --locked python -m bridge_agent.interfaces.agent \
  --config examples/persistent.yaml --workspace . \
  --env-file .env --env-override --session-id repository-study \
  --prompt '基于上一轮结论，下一步应该阅读哪个入口文件？'
```

去掉 `--prompt` 可串行交互。交互终端显示 Session ID，`/new` 创建并显示新 ID；单次调用如需以后恢复，请显式指定 `--session-id`。不指定时每次新建随机 ID。

状态查询不调用模型：

```bash
uv run --locked python -m bridge_agent.interfaces.agent \
  --config examples/persistent.yaml --workspace . \
  --env-file .env --env-override --session-id repository-study --session-status
```

返回 session_id、workspace、status、compatible。状态为 `succeeded` 或 `incomplete`；后者包括仍在执行或被中断的轮次。只有 succeeded 且 compatible 的会话可继续。当前正在运行的另一进程会持有数据库锁，状态命令也会报占用，待其退出后再查询。

`checkpoint.sqlite.config.path` 是显式本地路径，相对当前启动目录解析，支持 `~`。示例保存在 `.bridge-agent/sessions.sqlite`，本仓库已忽略 `.bridge-agent/`；不要提交数据库、WAL 或会话资料。更改当前目录时使用绝对数据库路径，避免误开一个新库。

数据库目录由插件创建，checkpoint 连接和旁边的 `.lock` 文件句柄由宿主管理。关闭或进程退出后锁释放，锁文件留在磁盘不表示仍被占用；不要手动删除正在使用的锁文件。一次只允许一个宿主打开同一规范化数据库路径。

会话历史、工作区、完成标记与运行时兼容指纹都保存在 LangGraph checkpoint 中。使用 `durability="sync"`，执行下一步前先持久化状态；最终回答通过校验后，再将完成标记写为 succeeded。不维护另一个消息库，也不从日志重建输入。

恢复范围：

- 正常完成后，重启进程可继续原会话；新 session ID 保持独立。
- 模型失败、执行中取消或进程崩溃后，已开始的轮次保留 incomplete；重新打开不会自动重放。
- 崩溃发生在回答生成后、完成标记写入前，仍按 incomplete 保守处理。
- 工作区不匹配会拒绝；模型类型/名称、工具输入 schema、system prompt 或运行时状态版本不兼容，也会拒绝续接。状态中 compatible 可用于判断。
- 没有未完成工具的自动恢复、跨版本迁移或副作用恰好一次保证；使用新 session ID 继续，并由用户核对已发生操作。

内存配置依然可用，但进程结束后丢失历史。自定义运行时仍只需实现 AgentRuntime；会话查询为独立可选能力 SessionControl，不强迫所有运行时采用 LangGraph。

验证：

```bash
uv run --locked pytest tests/test_persistent_sessions.py tests/test_persistent_cli.py
```

测试包括独立进程重启、SIGKILL 中断、不重放、占用与关闭释放，不访问真实模型。阶段 2/3 的真实模型验收已通过；这不替代本节对跨进程持久恢复的独立测试。
