# 阶段 7：Cordis 能力对齐矩阵

## 7.1 基线与 review

核对本地参考提交 `46a7f68b0922371ce7144b668b90e377d8e799f4`，范围为 vendor/cordis/src/index.ts 的公开导出及 Context 混入方法、vendor/loader 和 vendor/hmr；不把 Harness 产品插件算进框架完整性。

源码链接基址：[固定参考提交](https://github.com/deepseek-ai/deepseek-harness/tree/46a7f68b0922371ce7144b668b90e377d8e799f4/vendor)。下面路径相对 vendor。此表在 7.1 是待实现清单，不表示实现完成；7.5 必须回填每行证据。

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
5. Python 没有稳定的 Node 私有模块图 API。拟用显式源码依赖清单，核心/contracts 变化要求重启；已向用户发起这项实质映射确认，7.5 未确认前不得宣称完整对齐。
6. logging/异步 context manager/类型 Protocol 等语言原生机制替代 JS utility/symbol 工具；UI 颜色表、堆栈格式、内部私有字段不是跨语言行为承诺。

7.1 review：覆盖了 core 导出、反射层、logger、volatile、loader 分组和 HMR，特别纠正“waterfall=值流水线”的误读。此阶段只记录设计证据，不为文档制造 TDD 测试。后续逐行为 red→green，再关闭矩阵行。
