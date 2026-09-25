# 阶段 1：能力接口与最小插件宿主

状态：P1-Q4 已确认实施方案，阶段 1 已完成。本文记录 [路线图](../roadmap.md) 阶段 1 的功能、概念、实现约束和验收证据。

配置运行、开发新插件与排错见 [插件使用与开发指南](../guides/plugins.md)，配套 reverse 示例复用本阶段协议。

## 已确定边界

- 使用 Python 3.13、uv 和既定模块分层；应用配置为 YAML，选择显式清单中的可信插件。
- 本阶段交付服务装配、依赖检查、启动与关闭、失败回滚和资源清理，以及无需模型的完整运行示例。
- LangChain 和模型调用在阶段 2 接入；外部插件包发现在阶段 6，完整动态 Cordis 机制在阶段 7。
- 阶段 1 不实现热重载、动态服务重绑定、层级 Context 或运行中的单插件卸载。
- 已按确认方案完成代码、测试、CI 与文档；后续功能按路线图推进。

## 已确认设计树

```text
阶段 1 最小宿主 [范围已确认]
├── 装配模型
│   ├── 单实例、单服务提供者 [P1-Q1 已确认]
│   ├── 依赖服务键，不绑定 provider 名称 [P1-Q4 已确认并实现]
│   └── YAML → 配置验证 → 依赖计划 → 构造 → 激活 [P1-Q4 已确认并实现]
├── 插件接口
│   ├── 激活成功时声明与注册严格一致 [P1-Q4 已确认并实现]
│   ├── 统一 async 生命周期 [P1-Q2 已确认]
│   └── 激活期登记，逆序释放 [P1-Q4 已确认并实现]
├── 失败与退出
│   ├── 激活失败后完整回滚 [已有要求]
│   ├── 清理全部资源后汇总错误 [P1-Q3 已确认]
│   └── 单次宿主，共享清理任务，保留取消 [P1-Q4 已确认并实现]
└── 交付证据
    ├── 同一个消费者切换两个实现 [已有要求]
    ├── 成功、无效配置、依赖错误、生命周期测试 [已有要求]
    └── CLI 演示、CI、打包后运行与文档 [P1-Q4 已确认并实现]
```

用户已确认：

- P1-Q1：每种插件只启用一个实例、每项服务只允许一个提供者；多实例与作用域留待后期。
- P1-Q2：统一 async 生命周期，按依赖顺序串行激活。
- P1-Q3：清理尽力全部执行，最后汇总错误；启动失败与回滚失败同时保留。

- P1-Q4：整体方案及 17 组验收已确认，授权完成阶段 1 的代码、测试、CI 与文档。

以下为实现基线；参考行为和本项目的取舍分别说明。

## 从参考项目核实的机制

参考仓库基线沿用 [参考架构](../reference-architecture.md) 中的提交。

- Cordis 的 `inject` 声明服务依赖，不把消费者固定到某个插件实现。Python 宿主可借鉴这一分离。
- 声明提供某项服务与实际注册服务是两件事；必须定义激活成功后如何验证声明兑现。
- Cordis 通过服务可用性动态驱动插件；阶段 1 已选择静态启动装配，因此需要自己明确启动前检查与依赖计划，不照搬其等待依赖的运行时行为。
- effect 内部释放与插件整体卸载的执行策略不同，不能笼统推断所有清理都严格逆序或自动汇总错误。

源码证据（均对应既定参考提交）：

- [依赖和插件元数据](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/vendor/cordis/src/registry.ts#L91)。
- [服务所有权和实际注册](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/vendor/cordis/src/reflect.ts#L277)。
- [依赖可用性、激活和卸载](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/vendor/cordis/src/fiber.ts#L597)。
- [部分初始化失败、重复清理和异步回滚测试](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/extensions/tool-cordis/tests/cordis-lifecycle.spec.ts#L47)。

## 核心概念

领域名称统一见 [领域术语](../CONTEXT.md)，以下补充各概念在本阶段的职责。

| 概念 | 要解决的问题 |
| --- | --- |
| Plugin Definition | 一个已知插件的身份、配置解析、能力声明与创建方式 |
| Plugin Catalog | YAML 中允许使用哪些插件名称，以及对应哪一个定义 |
| Service Key | 消费者需要的能力是什么，与具体实现名称分开 |
| Plugin Context | 某个插件可以读取哪些依赖、提供哪些服务、登记哪些资源 |
| Activation Plan | 校验后的激活顺序与依赖关系，尚未产生插件副作用 |
| Owned Registration / Resource | 某项注册或资源归哪个插件负责，何时撤销 |

## 1. 功能与非目标

阶段 1 实现一个完整但静态的宿主生命周期：读取 YAML → 验证全部配置 → 检查能力依赖 → 得到激活计划 → 启动插件 → 使用服务 → 关闭宿主。

具体功能：显式插件清单、插件配置验证、类型化服务键、依赖排序、受声明约束的服务访问、服务发布、资源登记、失败回滚、关闭、可定位错误和演示入口。

不实现模型接口占位、Agent 循环、工具调用协议、Skills、checkpoint、hook 总线、自动包扫描、嵌套插件树、热重载或通用 IoC 容器。这些按实际消费需求在后续阶段引入。

## 2. 模块及框架选择

| 模块 | 阶段 1 内容 | 依赖约束 |
| --- | --- | --- |
| `contracts/` | ServiceKey、PluginDefinition、PreparedPlugin、Plugin / PluginContext 协议 | 仅标准库；不导入 kernel 或具体插件 |
| `kernel/` | 激活计划、服务注册表、插件局部上下文、宿主状态与清理 | 依赖 contracts，不读 YAML，不认识内置插件名字 |
| `bootstrap/` | YAML 解析、配置校验、显式 PluginCatalog、组装宿主 | 集中连接具体实现；不在这里实现资源生命周期 |
| `plugins/` | 两个演示 provider 和一个真实 consumer 插件 | 使用接口与自身配置，不直接操作宿主内部字典 |
| `application/` | 消费示例能力的应用用例 | 只依赖示例能力接口 |
| `interfaces/` | 最小插件演示命令，展示结果和定位错误 | 调用 bootstrap/application，不提供 Agent 聊天 |
| `examples/` | 可直接运行的两份 YAML | 仅列出显式插件及参数 |

实际工具选择：

- 标准库 `typing.Protocol` / 泛型表达能力接口，dataclass 表达宿主元数据。
- `graphlib.TopologicalSorter` 生成依赖顺序；先独立检查缺失与重复 provider，并在启动任何插件之前完整计算排序。
- `contextlib.AsyncExitStack` 管理资源逆序释放；在其回调外增加小型错误收集，避免清理错误遮蔽启动错误。
- `asyncio` 提供生命周期执行；不额外引入任务调度框架。
- PyYAML 的专用 SafeLoader 子类解析数据，Pydantic v2 严格校验配置。Pydantic 只用于配置边界与具体插件配置，不进入宿主核心协议。
- `argparse` 提供演示命令；现有 pytest、Ruff 和 mypy 继续使用。异步测试可通过 `asyncio.run` 驱动，不为少量生命周期测试额外引入测试插件。

PluginDefinition 的标准库声明位于 contracts，具体插件持有自己的 Pydantic 配置模型；bootstrap 调用配置验证并形成 PreparedPlugin（声明与绑定已验证配置的无副作用 factory）。kernel 只接收 PreparedPlugin，不接收 Catalog 或 Pydantic 模型，不反向导入 bootstrap。

暂不采用 Pluggy：它解决 hook 规范和调用，仍需要自行补充服务依赖、资源归属和启动回滚；阶段 1 没有真实 hook 聚合需求。这个选择不排除后续按需使用成熟 hook 实现。

依赖已通过 uv 安装并写入锁文件，Python 3.13 下通过检查与测试；具体版本见 [技术栈](../tech-stack.md)。

## 3. 服务与插件协议

公开接口职责：

| 接口 | 约束 |
| --- | --- |
| `ServiceKey[T]` | 由能力定义模块导出的规范键，关联稳定名称与静态类型 T；YAML 不直接注入任意服务对象 |
| 插件定义 | 提供稳定名称、配置验证与无副作用创建方式，声明 requires / provides |
| `Plugin.activate(ctx)` | 配置已经验证；在局部上下文中取得依赖、获取资源并注册声明的服务 |
| `ctx.require(key)` | 只能取插件显式声明的依赖，避免隐藏依赖破坏启动顺序 |
| `ctx.provide(key, value)` | 只能登记声明要提供的服务；重复登记拒绝 |
| 上下文资源方法 | 进入同步/异步资源上下文，或登记清理回调；资源取得后及时登记 |
| `host.resolve(key)` | 只在宿主运行状态向应用暴露已发布的服务；停止后拒绝新查询 |
| `host.start()` / `host.close()` | 统一管理整个宿主生命周期；不提供运行中单插件卸载 |

服务协议用静态类型检查与真实行为测试验证。`runtime_checkable Protocol` 不验证完整签名和返回值，不能据此宣称任意插件类型在运行时安全。

`ServiceKey[T]` 使用不变类型参数：注册值不能通过类型推断被放宽成共同父类型；`require` 和 `resolve` 返回 T。声明元数据用 `ServiceIdentity` 擦除不同键之间的类型差异，插件读写仍使用规范的 `ServiceKey[T]`。唯一的类型转换位于内核异构存储的读取边界。

消费者依赖服务键，而不是 provider 的插件名。更换 provider 后，Consumer 的 requires 与代码不变。同一个服务名称对应唯一规范键；相同名称被错误定义成不同键时明确拒绝，避免静态类型与运行时查找不一致。

发布规则：激活期间服务暂存在该插件的局部注册表，全部 provides 都兑现后才发布；消费者随后激活。少提供、多提供、重复提供或读取未声明依赖，都属于插件协议违约，触发启动失败与回滚。每种插件定义最多激活一个实例，每项服务最多一个提供者；一个插件可声明多个不同服务。

PluginContext 的 provide 与资源登记仅在本插件 ACTIVATING 时允许；activate 返回后禁止新增贡献。require 在 ACTIVATING、ACTIVE 及清理中的 CLOSING 可读取声明且仍存活的依赖，自身关闭后全部上下文操作拒绝。运行中的临时资源由服务方法自行在调用范围内管理，不登记成新的启动期贡献。

应用持有的服务引用只在宿主运行期间有效；停止宿主前调用方应结束相关调用。阶段 1 不给任意服务对象加动态代理，也不声称关闭宿主能撤销外部已保存的 Python 引用。

## 4. YAML 与配置边界

可直接使用的格式：

```yaml
version: 1
plugins:
  - name: demo.report
    config:
      prefix: "result: "
  - name: demo.uppercase
    config: {}
```

对应的第二份配置将 `demo.uppercase` 换成 `demo.lowercase`。消费者写在 provider 前面，用来验证 YAML 列表顺序不是依赖关系本身。requires / provides 由插件定义声明，配置不任意改写。

解析和验证规则：

- 单个 YAML 文档，顶层只接受版本和插件列表；插件行只接受规定字段。
- 禁止重复 YAML 键，错误包含文件与行列；这与重复插件名、重复服务分别诊断。
- 安全解析，只接受配置所需的数据值；阶段 1 不支持自定义对象标签、锚点/别名和 merge 组合，以避免隐含覆盖及递归配置。
- 外层和每个插件配置都严格校验类型，拒绝未知字段；文本中容易被 YAML 识别为布尔等类型的值需要引号。
- 未知插件、版本不支持、插件重复、配置错误都在任何激活前拒绝。
- 配置不执行表达式、自动导入任意模块或自动安装包。密钥引用环境变量的既定约束保留，阶段 1 演示不需要密钥，不先实现通用环境插值器。

## 5. 激活与关闭流程

宿主状态：`NEW → STARTING → RUNNING → CLOSING → CLOSED`；启动失败先回滚再进入 `FAILED`。关闭清理完成但有错误时仍终结生命周期，并向调用方报告错误，不把已尝试释放的资源重新清理。

1. bootstrap 完成全部配置验证，形成绑定配置的 PreparedPlugin，不激活或获取资源。
2. kernel 从 requires / provides 构建服务到提供者的映射，检查缺失、重复、自依赖和循环。
3. 求出完整激活顺序，再构造全部无副作用插件实例；构造失败时报告插件名并进入 FAILED，尚无插件被激活。每一批就绪插件按输入顺序稳定排列。
4. 调用每个插件前创建其局部注册表和资源栈。
5. 串行激活，检查声明兑现，再发布其服务。
6. 全部成功后进入 RUNNING；应用这时才能消费服务。
7. 启动失败立即停止后续激活，先清理失败插件已登记的部分资源，再逆序清理此前激活的插件。
8. 正常关闭按逆激活顺序释放插件；每个插件内部按资源登记逆序清理。消费者清理时其 provider 仍有效。
9. 进入清理后禁止新增服务或资源；各插件的服务在其清理后撤销。全宿主清理结束时不留宿主注册。

宿主单次使用，关闭或失败后不重新启动；新运行创建新宿主。重复关闭不重复释放资源。生命周期调用规则：

| 调用时状态 | 行为 |
| --- | --- |
| NEW + start | 进入 STARTING，验证计划、构造与激活 |
| 非 NEW + start | 立即状态错误，不重复激活 |
| NEW + close | 直接 CLOSED，无资源释放 |
| STARTING + close | 状态错误；如需中断启动，取消启动任务，由启动路径回滚 |
| RUNNING + close | 创建唯一清理任务并进入 CLOSING |
| CLOSING + close | 等待同一个清理任务，不重复清理；等待者观察同一清理结果 |
| CLOSED / FAILED + close | 无副作用返回；原始调用已报告的错误不在新调用中重放 |

插件激活期间通过资源栈登记的对象归宿主生命周期所有。宿主调用方在正常关闭前结束业务调用；阶段 1 不实现运行中业务任务的排空调度器。

资源所有者必须及时登记清理。若插件自行取得资源后没有登记，宿主无法凭空发现和释放它；失败的上下文进入方法也需要自行处理未交给宿主的部分资源。

`ctx.enter_context` / `enter_async_context` 表达资源所有权：退出方法以 `(None, None, None)` 调用，不负责将应用异常传给事务型上下文管理器，也不允许它压制宿主错误。需要提交/回滚语义的事务应由服务方法在调用范围内管理。

## 6. 错误与取消

P1-Q3 已确认：某项清理失败仍尝试其余清理，最后汇总报告；启动异常与回滚异常同时保留。普通异常使用可定位的领域错误与异常组，保留原始异常作为原因，不把多条错误拼成无法追踪的纯字符串。

错误至少区分配置解析/验证、未知插件、依赖计划、插件协议违约、插件激活和资源清理。错误定位包括相关插件名或服务名；不在错误里输出完整配置值或环境变量内容。

启动 await 点的取消也必须回滚。清理后继续传播 `CancelledError`，不能把取消吞成启动成功；清理错误附带报告，不覆盖取消本身。关闭期间取消同样不能使已经发起的清理任务无人等待：宿主保留唯一清理 Task，各等待者阻止自身取消传播给该 Task；即使等待者再次被取消，也等待同一清理 Task 结束后传播取消。清理失败作为取消异常的 cause 携带异常组，取消本身保持 CancelledError。用事件门控制测试时序，避免依赖 sleep 的偶然顺序。

阶段 1 面向配合协议的可信插件，不保证强制终止阻塞 Python 代码或进程强杀后的清理。全局关闭期限和强制终止策略不是本阶段能力；具体资源操作自行设置适当超时。

## 7. 可运行示例

一个 `TextTransform` 示例能力具有 uppercase / lowercase 两个 provider。`Report` consumer 声明依赖 TextTransform，在应用调用时转换文本并加上配置的前缀；不在插件激活时执行用户任务。

```text
YAML → Catalog / 配置验证 → 激活计划 → Host
                                        ├── TextTransform provider
                                        └── Report consumer
应用输入 Hello → Report → TextTransform → result: HELLO
关闭 → 先 Report，再 TextTransform
```

另一份 YAML 输出 `result: hello`，应用与 consumer 源码保持相同。演示命令必须经过生产配置解析与宿主路径，不在演示代码里手工绕过装配。

生命周期验证另用真实可观察的上下文资源（例如临时文件句柄）和异步事件门，证明资源确实关闭、失败回滚确实等待完成；大小写示例本身只证明组合与替换，不能代替资源测试。

运行命令：

```bash
uv run --locked python -m bridge_agent.interfaces.plugin_demo --config examples/plugins.upper.yaml --text Hello
# result: HELLO
uv run --locked python -m bridge_agent.interfaces.plugin_demo --config examples/plugins.lower.yaml --text Hello
# result: hello
```

这是阶段 1 的插件组合示例；Agent 聊天入口在阶段 2 实现。

## 8. 验收矩阵

| 编号 | 场景 | 通过条件 |
| --- | --- | --- |
| A01 | 两份 YAML 切换 provider | 相同应用输入得到两个预期结果，consumer 与 host 不变 |
| A02 | Consumer 在配置中先于 provider | 自动按依赖顺序启动，输出正确 |
| A03 | 两个无依赖插件 | 多次运行得到相同确定性启动顺序 |
| A04 | 无效 YAML、重复键、未知字段/插件、错误类型 | 激活计数为零，错误可定位；可执行标签与别名拒绝 |
| A05 | 缺失、重复 provider、自依赖、循环 | 全部在首次激活前失败；包含独立可启动节点的循环也不能先启动一部分 |
| A06 | 声明与实际注册不符、未声明依赖访问 | 明确协议错误，未发布半成品服务，并回滚 |
| A07 | 插件获取多个资源后失败 | 当前插件资源和已激活插件全部逆序尝试清理，后续插件未启动 |
| A08 | 一个清理动作失败 | 其他资源仍清理，错误包含原始失败与所有清理失败 |
| A09 | 正常关闭、重复关闭 | 实际资源已关闭，注册为空，清理每项只执行一次 |
| A10 | 清理过程中尝试注册 | 拒绝新增贡献，其他清理继续 |
| A11 | 在激活 await 点取消 | 等待已登记资源回滚，继续传播取消，无宿主遗留任务 |
| A12 | 关闭中取消、再次取消或并发关闭 | 等待唯一清理任务后传播取消；清理错误通过 cause 可见，无重复清理或遗留任务 |
| A13 | 非运行态 resolve、关闭后 start | 明确状态错误，不返回过期服务或重用宿主 |
| A14 | 类型与依赖方向 | mypy 检查 typed key 返回值；架构检查禁止 kernel/application 导入具体插件 |
| A15 | wheel 独立安装运行 | 在独立环境使用真实 YAML 运行示例，避免只在源码目录可用 |
| A16 | 插件 factory 构造失败 | 依赖计划已验证，任何插件都未激活，错误包含插件名，宿主进入 FAILED |
| A17 | activate 返回后或自身关闭后操作 ctx | 拒绝迟到的服务/资源登记；关闭后的依赖读取拒绝，消费者清理时仍能读取 provider |

配置和图检查使用单元测试，宿主与生命周期用真实插件实例作集成测试，演示命令以子进程做端到端测试。只替换测试插件/资源，不 mock 被验证的宿主流程。

## 9. 实现与验收证据

已交付协议及配置、依赖计划、宿主与资源生命周期、两个 provider 与 consumer、演示入口、CI 与打包验证。用户在已有初版实现后要求 TDD；既有行为补回归测试，后续缺陷按失败测试 → 最小修复推进，不将补写回归测试描述为最初的测试驱动开发。

- 已采纳场景均有对应证据；Ruff、mypy、pytest、构建与 wheel 独立运行作为交付检查。
- CI 已启用 pytest，mypy 覆盖全部源码；类型契约测试另外运行 mypy 检查插件使用方的输入与输出类型。
- wheel 在独立环境运行两份配置成功，错误配置非零退出；CI 执行相同安装验收。
- 本文、README、技术栈、AGENTS 与路线图同步阶段 1 状态。
- 不自动实现阶段 2、不安装 LangChain、不宣称阶段 7 完成；提交和推送按用户指令执行。

### 代码入口

- [插件协议与类型化键](../../src/bridge_agent/contracts/plugins.py)、[示例能力协议](../../src/bridge_agent/contracts/demo.py)。
- [配置目录与 YAML 校验](../../src/bridge_agent/bootstrap/config.py)、[显式插件清单](../../src/bridge_agent/bootstrap/demo.py)。
- [宿主](../../src/bridge_agent/kernel/host.py)、[依赖计划](../../src/bridge_agent/kernel/plan.py)、[插件资源上下文](../../src/bridge_agent/kernel/context.py)。
- [三个示例插件](../../src/bridge_agent/plugins/demo.py)、[应用用例](../../src/bridge_agent/application/demo.py)、[命令入口](../../src/bridge_agent/interfaces/plugin_demo.py)。

增加内置插件时：在 contracts 定义真实需要的能力与唯一服务键；在 plugins 实现能力和 async activate；通过 PluginDefinition 声明 requires/provides 与纯配置 prepare 函数；最后由 bootstrap 显式登记并在 YAML 选择。prepare 只校验配置并返回 factory，factory 只创建无资源的实例，资源获取在 activate 内及时交给 ctx 管理。现有示例完整展示这一链路。

### 验收对应关系

| 验收 | 可复查证据 |
| --- | --- |
| A01、A02 | `tests/test_demo.py`：两个真实 YAML、子进程输出及错误退出 |
| A03、A05–A13、A16、A17 | `tests/test_host.py`：真实插件、可关闭资源、事件门控制取消与并发 |
| A04 | `tests/test_config.py`：无效配置、重复键、非法显式标量、构造前校验；CLI 验证配置值不回显 |
| A14 | `tests/test_typing.py`：错误注册值被拒绝、消费方获得精确类型；源码 mypy 与分层导入检查 |
| A15 | CI 的独立 wheel 环境：两份配置输出与无效配置非零退出；本地同样执行 |

阶段 1 初次交付验收：48 个测试通过，Ruff 检查与格式检查、mypy 严格检查通过，sdist/wheel 构建与独立安装演示通过。后续使用指南增加 reverse 示例端到端验收。CI 已配置检查，远端运行结果需在提交后查看。

TDD 中实际发现并修复的问题：泛型服务键推断允许错误注册值、重复 YAML 键缺少明确诊断、非法 YAML 显式标量绕过配置错误并暴露值。记录见 [测试说明](../../tests/README.md)。

## 10. 官方依据

- [Python 3.13 typing / Protocol](https://docs.python.org/3.13/library/typing.html#typing.Protocol)：结构化静态类型，运行时检查限制。
- [graphlib](https://docs.python.org/3.13/library/graphlib.html)：拓扑排序与循环检测，不能替代缺失 provider 验证。
- [AsyncExitStack](https://docs.python.org/3.13/library/contextlib.html#contextlib.AsyncExitStack)：资源栈与逆序清理。
- [asyncio 取消](https://docs.python.org/3.13/library/asyncio-task.html#task-cancellation)：清理与取消传播。
- [PyYAML](https://pyyaml.org/wiki/PyYAMLDocumentation) 与 [mapping 构造源码](https://github.com/yaml/pyyaml/blob/main/lib/yaml/constructor.py)：安全解析与重复键检查的边界。
- [Pydantic 严格模式](https://docs.pydantic.dev/latest/concepts/strict_mode/) 和 [配置项](https://docs.pydantic.dev/latest/api/config/)：拒绝类型隐式转换和未知字段。
- [Pluggy](https://pluggy.readthedocs.io/en/stable/)：hook 的职责；本阶段是否使用的结论属于本项目设计判断。
