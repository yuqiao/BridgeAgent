# 动态插件使用与开发

阶段 7 在原有静态宿主之外提供 DynamicHost。应用仍通过 AgentRuntime 执行任务，LangChain 仍负责模型—工具循环。动态宿主负责多实例、作用域、依赖变化、事件、资源、配置更新和源码重载。完整性依据见 [能力矩阵](../stages/07-cordis-matrix.md)，发布证据见 [阶段 7.5](../stages/07.5-integration.md)。

## 不调用模型的快速体验

在仓库根目录执行；开发依赖中包含独立示例插件包：

```bash
uv sync --locked
uv run --locked python -m bridge_agent.interfaces.agent \
  --dynamic --watch --config examples/dynamic-external.yaml
```

输入 `hello` 得到 `external: hello`。输入 `/plugins` 查看实例状态、缺失依赖与登记的 effect；`/reload` 重新读取配置；`/new` 新建会话；`/exit` 退出并释放资源。

保持进程运行，将 YAML 中 default 的 prefix 改成 `updated: `，保存后看到 stderr 的 `Reloaded`，下一条输入使用新前缀。配置非法时报告失败并保留旧树；修正文件后再次加载。只有有变化的配置才重挂载，无关实例保留。`--watch` 轮询间隔为 0.25 秒，等待输入时也会处理变化。

选择另一作用域：

```bash
uv run --locked python -m bridge_agent.interfaces.agent \
  --dynamic --config examples/dynamic-external.yaml --entry second --prompt hello
# second: hello
```

部署时需要另外安装可信示例/业务插件 wheel，示例包不是核心运行时依赖。参见 [外部插件指南](external-plugins.md)。

## 编码助手

```bash
uv run --locked python -m bridge_agent.interfaces.agent \
  --dynamic --watch --config examples/dynamic.yaml \
  --workspace . --env-file .env --env-override --show-tools
```

此配置装配 OpenAI 兼容模型、LangChain 运行时、SQLite、工作区文件、Skills、修改工具和命名测试命令。模型及一轮运行超时均为 600 秒。模型依赖 `.env` 中的三个 OPENAI 变量；实际工具端点验收状态见 [阶段 2](../stages/02-agent-loop.md)。

默认交互式逐次审批修改与命令；非交互输入默认拒绝，需要明确配置允许路径/命令才能执行。Skill 无法绕过审批。文件、命令和会话的使用方式分别见 [修改与测试](coding.md)、[Skills](skills.md)、[会话](sessions.md)。SQLite 文件位于工作区下 `.bridge-agent/dynamic.sqlite`，同库不能被两个进程同时打开。

`--session-id ID` 续接已完成且兼容的会话；`--session-status --session-id ID` 查询。配置更改造成运行时不兼容、换成内存存储或执行失败时，不保证原会话可继续；使用 `/new` 或新的 ID。热重载不是会话格式迁移，也不会重放已发生的修改和命令。

## YAML v2

```yaml
version: 2
entries:
  - id: group
    scope: {agent.runtime: isolated}
  - id: runtime
    parent: group
    name: runtime.example
    config: {prefix: 'hello: '}
    disabled: false
```

`id` 是实例身份，`name` 是已注册插件名；同一插件可有多个实例。省略 name 创建分组。parent 构成树；循环和不存在的 parent 拒绝。父项禁用会卸载子树，恢复时按依赖重新激活。

`scope` 将服务键映射到标签，同键同标签共享一个提供者；不声明则继承父作用域。一个作用域的同一服务只有一个提供者。`inject` 附加服务依赖名，`intercept` 提供服务配置覆盖；服务键必须已由清单定义。缺少服务时实例为 pending，服务出现后自动激活。隔离的槽位不会悄悄回退到根服务。

v1 的 `include` 用于静态配置组合；v2 用显式 entry 树，不接受 include、表达式、任意模块路径或可执行 YAML 标签。通过 `DynamicLoader.create/update/remove` 编辑运行中的树，`resolve/context/locate` 查询；只有显式 `save(path)` 才写磁盘。`update(id, parent=...)` 移动实例，`disabled=True` 停用，`config={...}` 替换整个配置对象。

## Python 插件与动态接口

既有 PluginDefinition / prepare / activate / ServiceKey 协议继续可用，API 版本仍为 1。使用动态扩展的插件在 activate 收到的 Context 上登记监听器和资源：

```python
from bridge_agent.contracts.plugins import PluginContext, PluginDefinition
from bridge_agent.kernel.dynamic import Context


class TracePlugin:
    async def activate(self, context: PluginContext) -> None:
        if not isinstance(context, Context):
            raise TypeError("trace requires DynamicHost")

        async def around(request, next):
            context.logger("trace").info("Agent run started")
            result = await next()
            context.logger("trace").info("Agent run completed")
            return result

        context.on("agent.run", around)


TRACE = PluginDefinition("extension.trace", lambda config: TracePlugin)
```

在组合入口 `agent_catalog(extra=(TRACE,), ...)` 显式登记，或从独立包 entry point 导出。导入、validate_config、prepare 必须没有长期副作用；prepare 可能在预检、应用和回滚中重复调用。实际资源在 activate 中创建并登记；仅有定义不会激活插件。

- `extend(metadata)` 派生元数据，`root` 获取根 Context；Python 类型检查使用 isinstance。`isolate(key, label)` 改变一个服务槽位，父 Context 不变。
- `require(key)` 读取已声明依赖，`provide(key, value, check=...)` 激活时发布服务。availability 条件变化后调用 `host.refresh()`；`host.set_service(id, key, value)` 替换值并重启消费者。
- `accessor(key, getter)` 延迟读取服务，`alias(alias_key, target_key)` 提供别名；可调用服务使用普通 Python `__call__`。共享键须由 contracts 模块导出，不能自行创建同名 token。
- `intercept(key, config)` 和 `config_for(key, base=..., head=..., merge=...)` 让服务按 base→祖先→局部→head 合并配置。默认浅合并，可提供自定义 merge 接收按序配置元组。
- `on_close`、`on_close_async`、`enter_context`、`enter_async_context` 登记归属。`await context.effect(factory, label=...)` 创建资源并返回幂等的异步 disposer；factory 返回清理函数。手动 await disposer 或卸载均释放一次，正在创建/释放的资源被排空等待。
- `context.config` 是只读动态配置引用。PluginDefinition 可声明 `volatile_fields=("level", "nested.option")`，配合 `validate_config`（可调用 Pydantic 的 model_validate/model_dump）校验并归一化。只有 volatile 字段变化时原子更新引用，插件每次使用时读取新值；普通字段变化重新激活。显式 reload 始终重启。

实例可通过 `host.instances`、`host.status(id)`、`host.definition(id)` 查询。mount 接收独立实例 ID、定义、配置和 Context；unmount 停止消费者后卸载提供者；reconfigure 更新配置，reload 重新创建实现。状态为 pending / loading / active / failed / unloading / disposed；清理期间先撤销 active 服务，经过 unloading 后回到 pending，清理失败通过汇总异常报告。失败实例可显式 reload 重试。

## 事件和调用生命周期

`on` / `once` 返回可手动撤销的 disposer，卸载时自动撤销；支持 prepend 和 global_。`select(predicate)` 按监听器所属 Context 过滤，global_ 监听器不受过滤限制。

| 调用 | 行为 |
| --- | --- |
| emit | 同步广播，监听器必须同步 |
| bail | 同步短路；只有 None 和 False 继续，0 和空字符串也短路 |
| await serial | 按顺序等待并短路 |
| await parallel | 等待全部监听器，再汇总错误 |
| await waterfall(..., next=...) | 监听器收到原参数及 next；可 await next() 包装结果，或不调用 next 来拦截 |

`agent.run` 包装公开 RunRequest → RunResult。`internal.config/update` 包装配置与更新；`internal.get/set` 为同步服务拦截；`internal.listener` 可以接管注册；`internal.plugin/status/service/dispatch` 为生命周期和分发通知。诊断通知异常记日志后继续，业务/配置拦截异常交给调用者。日志使用 Python logging 的命名 logger、Handler 和 Formatter，`export_logs` 将 exporter 归属当前插件。

动态 Agent 适配器和异步事件自动持有调用租约；直接调用裸服务时使用：

```python
async with host.lease(context) as active:
    result = await active.require(MY_SERVICE).run()
```

变更暂停新调用，等待整个宿主的在途调用结束，默认排空上限 30 秒，超时不释放仍使用中的资源。嵌套租约可重入。生命周期回调不能等待其他任务对同宿主做 mount/reload 等变更；此类操作由外层 loader 统一调度。未登记的后台任务和资源不会自动被宿主发现。

## 源码监听与迁移

`--watch` 自动监听已启用外部 entry point 的源码模块。导出可声明：

```python
PluginExport(
    api_version=1,
    definition=MY_PLUGIN,
    reload_modules=("my_plugin.helpers",),
    restart_modules=("my_plugin.contracts",),
)
```

reload_modules 按依赖先于使用者排序，最后重载导出模块。手动装配可调用 `SourceReloader.register(id, module, attribute, dependencies=...)`。共享 contracts、类型身份或 BridgeAgent 核心变化要求重启；watcher 返回失败并正常释放宿主。`watch_file(path)` 监听一般资料文件，触发 hmr.change，不自动重载源码。

变更按文件内容 hash 检测，同一轮合并。编译、导入或激活失败恢复旧模块/定义和依赖链；失败版本不会在每次轮询重复尝试，修复文件或手动重载配置后再操作。外部 I/O 副作用无法通用回滚。入口导出的属性应为模块顶层 PluginExport。运行环境须有可读 Python 源文件；生产 wheel 更新建议重启进程，开发可使用 editable 安装。

静态 v1 插件无须迁移；启用动态模式时为每项补 id，将 plugins 改成 entries/version 2 并传 --dynamic。多实例隔离按实际服务键配置，不能只改 id；需要动态行为时再使用 Context 扩展。接受的 Python 映射为显式 API 与依赖清单，不提供 JS Proxy、装饰器或 Node 私有模块图兼容。
