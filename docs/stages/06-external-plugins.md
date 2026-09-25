# 阶段 6：外部插件与配置组合

基线：stage5 / c68433f。公开边界：PluginCatalog.load + PluginHost、独立包/CLI；沿用用户确认的范围。

已实现：纯数据 include、确定性顺序、重复/循环/越界与文档数量检查；Python entry points 延迟导入、显式启用、接口版本校验；可独立构建的 runtime.example 包。

TDD：先以 include 装配失败为 tracer，再实现组合；循环原来 RecursionError、越界原来成功、文档数量原来无限制，逐项转 green。外部包原先 unknown plugin，安装真实 wheel 后新增加载器转 green；内置同名原先静默忽略外部包，补失败测试后拒绝冲突。兼容版本、重复发行包和未选包不导入作为加载器契约回归检查，没有虚构 red。

Review：元数据发现不执行包代码，已选包导入属于可信代码；包版本交给 Python 安装器，API version=1 由应用检查，两者不混淆。保留旧插件宿主全部检查与清理规则；例子以开发依赖方式独立安装，不打进核心 wheel。配置组合不覆盖重复插件，以避免授权配置被静默合并。错误不输出配置原值或第三方异常正文。API 1 是阶段 1 静态插件协议，后续动态宿主另设入口，不破坏旧协议。

行为证据：tests/test_external_plugins.py、tests/test_config_composition.py，以及前序宿主回归。使用见 [外部插件指南](../guides/external-plugins.md)。

发布验证：203 passed、2 skipped；Ruff、mypy（47 文件）、构建通过。核心与示例的两个 wheel 同时安装到独立环境，外部插件验收 5 passed（包含独立 CLI）。
