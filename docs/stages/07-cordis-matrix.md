# 阶段 7：Cordis 能力对齐矩阵

## 7.1 基线与 review

核对本地参考提交 `46a7f68b0922371ce7144b668b90e377d8e799f4`，范围为 vendor/cordis/src/index.ts 的公开导出及 Context 混入方法、vendor/loader 和 vendor/hmr；不把 Harness 产品插件算进框架完整性。

源码链接基址：[固定参考提交](https://github.com/deepseek-ai/deepseek-harness/tree/46a7f68b0922371ce7144b668b90e377d8e799f4/vendor)。下面路径相对 vendor。此表在 7.1 建立，7.5 已逐项 review 并在下方关闭验收项；路径和命名差异按已确认的 Python 映射处理。

| ID | 参考公开能力与源码依据 | Python 映射与验收节点 |
| --- | --- | --- |
| C01 | cordis/src/context.ts: extend、root、metadata、is | 显式 Context、不可变派生 metadata；7.2 隔离/继承测试 |
| C02 | context.ts: isolate(name,label)，同 label 共享 | 类型化 ServiceKey + scope label；7.2 多实例、独立/共享 scope |
| C03 | context.ts: intercept；service.ts: resolveConfig | Context.config_for 按祖先→局部→head 合并；7.2 |
| C04 | registry.ts: plugin/inject、函数/类/apply、依赖元数据 | PluginDefinition 工厂统一形态，mount + 依赖声明；7.2 |
| C05 | registry.ts: get/has/keys/values/entries/delete，Fiber identity | 实例 ID、只读状态快照、unmount；7.2/7.4 |
| C06 | fiber.ts: PENDING/LOADING/ACTIVE/FAILED/UNLOADING/DISPOSED、await/restart/update | 对应状态、await mount 完成、显式重试/更新；7.2/7.4 |
| C07 | fiber.ts: _refresh/_checkImpl，依赖变化重激活 | 缺依赖等待、服务出现/消失/换代级联；7.2 |
| C08 | reflect.ts: get/set/provide/notify，availability check | Context 服务查询/发布与 host 更新、可用性通知；7.2/7.4 |
| C09 | reflect.ts: accessor/mixin，service.ts: callable/init/extend/tracker | 显式 accessor/别名服务；Python __call__、工厂、Context 参数替代 JS Proxy；7.3 |
| C10 | fiber.ts: effect、disposer、getEffects、inactive guard | 资源/effect 登记、手动撤销、逆序清理、诊断；7.3 |
| C11 | events.ts: on/once/prepend/global/filter | 所属 Context 自动释放、过滤与单次监听；7.3 |
| C12 | events.ts: emit/parallel/serial/bail；isBailed(0 和空字符串也短路) | 同步 emit/bail、异步 parallel/serial；精确 None/False 判断；7.3 |
| C13 | events.ts: waterfall 是 next 链，不是累加器 | around/next 包装、修改结果与 veto；7.3 |
| C14 | events.ts: internal/plugin/status/config/update/get/set/listener/dispatch | 明确生命周期/配置/服务/事件扩展入口，实际 Agent around；7.3/7.4 |
| C15 | logger.ts: levels/names/exporters/formatters | Python logging 命名 logger/Handler/Formatter，Context 资源登记撤销；7.3 |
| L01 | loader/src/config/tree.ts: create/update/remove/resolve/resolveGroup/locate/await | ID 树、分组、移动/禁用/恢复、就绪快照；7.4 |
| L02 | loader/config/isolate.ts: local/global realm | 显式 scope label、共享 label、销毁子树；7.2/7.4 |
| L03 | loader/index.ts: import/unwrapExports、entry inject、schema validate | entry points/显式清单、附加依赖、Pydantic prepare；6/7.4 |
| L04 | loader/config/diff.ts、entry.ts: volatile 字段原子提交，普通字段变化重挂载 | 显式可变配置引用与字段声明，验证失败保留旧值；7.4 |
| L05 | loader/config/group.ts、index.ts: write/noSave、子项更新、配置保存 | 纯数据树读写、显式保存、临时更新不改文件；7.4 |
| H01 | hmr/src/index.ts: source watch/debounce/affected graph/cache invalidation | 源码依赖清单 + 内容变化检测、只重载受影响 entry；7.4 |
| H02 | hmr: config reload、hmr/change/reload、框架变化 exit | 配置刷新、诊断事件、核心/共享 contracts 变化要求重启；7.4 |
| H03 | hmr/error.ts: 报错、回滚；Fiber effect 清理 | 失败状态、旧配置恢复、在途调用排空与超时；7.4/7.5 |
| P01 | 实际 Harness provider/consumer 与 lifecycle 组合 | 实际 LangChain Agent provider 换代、作用域、反复 reload；7.5 |

## 已知映射和差异

1. 已确认独立 Python 实现，不加载 JS 插件、不使用可执行 YAML。纯数据 include/显式 entry points 替代 JS module specifier/evaluate/interpolate；不提供 JS 求值兼容模式。
2. 阶段 1 保持单次严格宿主不变；动态宿主另开入口，依赖缺失显示 PENDING。旧 API 1 插件可原样使用，动态扩展通过额外 Context API 提供。
3. Python 使用类型化键、工厂、Pydantic、显式 Context/accessor，不实现 JS Proxy/装饰器运行时。同等能力通过显式 API 验证；不是 TypeScript 二进制或语法兼容。
4. 清理沿用已确认的“尝试全部资源后汇总错误”，不复制参考实现中部分异常可能中断后续清理的行为。变更前排空在途调用；外部副作用不能通用回滚。
5. Python 没有稳定的 Node 私有模块图 API。使用显式源码依赖清单，核心/contracts 变化要求重启；用户已于 2026-09-25 确认本项，以及显式 Context/工厂/Pydantic 的 Python 映射。
6. logging/异步 context manager/类型 Protocol 等语言原生机制替代 JS utility/symbol 工具；UI 颜色表、堆栈格式、内部私有字段不是跨语言行为承诺。

7.1 review：覆盖了 core 导出、反射层、logger、volatile、loader 分组和 HMR，特别纠正“waterfall=值流水线”的误读。此阶段只记录设计证据，不为文档制造 TDD 测试。后续逐行为 red→green，再关闭矩阵行。

7.2 实施证据见 [Context 与动态依赖](07.2-dynamic-context.md)，C01–C07 的基础行为已验证；注册表扩展诊断、动态更新在 7.4 收尾。

7.3 证据见 [事件与贡献](07.3-events-effects.md)：C09–C13、C15 与实际 Agent around 已验证，C14 生命周期/配置事件待 7.4。

7.4 证据见 [动态生命周期与重载](07.4-reload.md)。已实现配置树、更新/撤销、可用性、volatile、配置/服务拦截、显式源码清单与 watcher；最终应用入口和完整性 review 在 7.5。


## 7.5 最终验收证据

下表测试文件均位于仓库 tests/，通过公开边界执行。源码符号仅用于定位实现，未用内部状态替代行为验收。

| ID | 已交付的实现与证据 | 结论 |
| --- | --- | --- |
| C01 | Context.extend/root/metadata；test_dynamic_host 的 metadata/intercepts 测试；Context.is 使用 Python isinstance 表达 | Python 映射通过 |
| C02 | Context.isolate；test_dynamic_host 独立/共享 label；test_dynamic_agent 两套真实 LangChain runtime | 通过 |
| C03 | config_for 默认/自定义 merge；test_dynamic_host 两项继承合并测试 | 通过 |
| C04 | PluginDefinition + mount + requires，统一工厂返回 Plugin；test_dynamic_host 等待依赖与激活失败 | Python 工厂映射通过 |
| C05 | instances/status/definition/unmount，以实例 ID 表示身份；test_dynamic_reload registry snapshot + test_dynamic_loader create/remove | 通过；Python 快照替代 JS Map 方法 |
| C06 | 六种状态、await mount/reload/reconfigure、失败与取消；test_dynamic_reload 配置替换/失败恢复/关闭保护 | 通过 |
| C07 | 服务消失先停消费者，重新出现自动激活；test_dynamic_host removal/rearrival + test_dynamic_reload consumer rollback | 通过 |
| C08 | require/provide/check、set_service/refresh；test_dynamic_reload availability；test_dynamic_events get/set 拦截 | 通过 |
| C09 | accessor/alias、普通 callable/工厂/显式 Context 参数；test_dynamic_events accessor_and_alias；协议可提供任意类型服务 | 已确认 Python 映射通过 |
| C10 | effect + 所属 context managers + 状态 effect 标签；test_dynamic_events 手动撤销、释放/创建排空；test_dynamic_agent 实际文件全部关闭 | 通过 |
| C11 | on/once/prepend/global_/select；test_dynamic_events once/filter、旧 disposer、listener interception | 通过 |
| C12 | 同步 emit/bail、异步 parallel/serial；test_dynamic_events 短路值与汇总错误 | 通过；同步入口拒绝异步监听器，避免遗漏 await |
| C13 | waterfall next 链；test_dynamic_events wrap/veto；test_dynamic_agent 实际 Agent around | 通过 |
| C14 | internal 生命周期/配置/更新/get/set/listener/dispatch；test_dynamic_events 与 test_dynamic_reload 中对应公开事件用例 | 通过 |
| C15 | logging logger/levels/Handler/Formatter；test_dynamic_events named_logging_exporter 随卸载撤销 | Python logging 映射通过 |
| L01 | DynamicLoader entry 树、create/update/remove/resolve/context/locate；test_dynamic_loader 移动/禁用/恢复/回滚 | 通过；await 异步 API + host 状态代替独立 await 方法 |
| L02 | 父 Context 继承 + 显式服务标签；test_dynamic_loader scopes + test_dynamic_cli --entry | 通过 |
| L03 | entry point PluginExport + inject + prepare/validate_config；test_external_plugins；test_dynamic_loader injection/normalized config | 通过 |
| L04 | ConfigView、volatile_fields、验证后更新、显式 reload 强制重启；test_dynamic_reload volatile 与 reload | 通过 |
| L05 | loader.save 原子写入、临时操作不写盘；test_dynamic_loader create/move/remove/save | 通过 |
| H01 | SourceReloader 内容 hash + manifest 依赖顺序 + 受影响实例；test_source_reload 真实文件/依赖/父包引用/自动清单 | 已确认显式清单映射通过 |
| H02 | watch_config、watch_file、hmr.change/reload、RestartRequired；test_source_reload + test_dynamic_cli 等待输入时更新 | 通过 |
| H03 | 模块/定义/配置树回滚、租约排空与超时；test_source_reload 语法失败、test_dynamic_reload 消费者失败、test_dynamic_events 并发释放 | 通过；外部 I/O 不承诺回滚 |
| P01 | test_dynamic_agent 真实 LangChain 循环、两作用域、连续十次模型替换/扩展 reload、监听器单次触发、实际文件资源释放；动态 workspace/coding CLI 回归 | 本地通过，真实供应商验收单独保留 |

完整性结论：参考基线公开能力已逐项实现或落实已确认的 Python 映射，未以“能热重载”替代完整性判断。此次没有将参考 Harness 的产品工具、UI、MCP 等未编号后续工作算入 Cordis 框架范围。静态 API 1 保持兼容，动态迁移见 [指南](../guides/dynamic-plugins.md)。最终数量、review 与验证范围见 [7.5](07.5-integration.md)。
