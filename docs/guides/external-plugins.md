# 外部插件包与配置组合

独立示例在 `examples/external-plugin`。开发环境通过 uv 的本地 editable 开发依赖安装该包；它不是核心 wheel 的组成部分，也不是生产依赖。

```bash
uv build examples/external-plugin --out-dir dist-examples
uv pip install dist-examples/*.whl
uv run --no-sync python -m bridge_agent.interfaces.agent --config examples/external.yaml --prompt hello
```

预期 `external: hello`。示例替换整个运行时，不调用模型。部署时在安装 BridgeAgent 的同一环境安装可信插件 wheel。常规生产环境不要依赖开发组中的示例包。

包在 `[project.entry-points."bridge_agent.plugins"]` 声明 `"runtime.example" = "bridge_example:plugin"`；导出 `PluginExport(api_version=1, definition=PluginDefinition(...))`，entry point 名称必须与 definition.name 一致。发行版本/依赖范围在包元数据声明，接口版本由宿主在导入后校验。缺失依赖导入失败、接口不兼容、同名 entry points、内置名冲突均报配置错误。所有已安装候选只读取元数据，未启用的包不导入；已启用的包是可信 Python 代码，导入可能有副作用。不要在 import 或 prepare 中分配长期资源，资源应在 activate 中登记。

与内部插件一样，依赖用共享的规范 ServiceKey、资源用 PluginContext 管理。新增服务时提供独立 contracts 模块供消费者引用，不能各自创建同名 token。接口无需依赖应用或入口。

配置组合示例：

```yaml
version: 1
include: [base.yaml, tools.yaml]
plugins:
  - name: runtime.example
```

按 include 顺序深度优先，再追加当前文件 plugins；每个文档声明 version: 1，plugins 可省略。最多读取 32 个文档，循环拒绝；路径相对包含者目录，不能绝对、含 `..` 或经符号链接越出该目录。相同插件出现两次直接报错，不做静默覆盖、深合并或任意表达式求值。插件配置中的业务路径（如 SQLite 路径）保持原有语义，不自动相对 include 文件重写。需要另一实现时选择不同组合文件。所有配置准备完成后才启动宿主。

动态多实例和源码重载见 [动态插件指南](dynamic-plugins.md)。PluginExport 可选声明 reload_modules 与 restart_modules；现有 API 1 导出不需修改即可静态或动态加载。
