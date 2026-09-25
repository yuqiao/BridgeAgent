# 测试

功能测试放在此目录，文件名使用 `test_*.py`。
通过 `uv run --locked pytest` 运行，使用 `importlib` 模式导入已安装的项目包，
无需手动修改 `PYTHONPATH`。

## 已确认的测试边界

沿用用户在阶段 1 实施设计 P1-Q4 中确认的验收边界：

- `PluginCatalog.load(path)`：真实 YAML 到已验证插件配置。
- `PluginHost.start/resolve/close`：装配、服务消费与生命周期结果。
- 真实插件收到的 `PluginContext`：声明约束、资源所有权与清理结果。
- 演示模块命令：退出码、标准输出及错误定位。
- 类型契约：插件作者经 mypy 使用服务键注册、读取服务，验证输入限制和返回类型。

测试不直接调用内部 planner、注册表、OwnedContext 或私有清理方法，
不 mock 宿主内部协作。测试插件是接口的真实消费者。

用户要求 TDD 前已存在一版实现；其既有行为补回归验证，后续行为修改
先写失败测试、确认失败原因，再实现最小修复。每次围绕一个行为推进，
不将后来补写的回归测试称为最初的 red → green 证据。

## 已执行的 red → green

| 行为 | 先观察到的失败 | 最小修正 |
| --- | --- | --- |
| 服务键拒绝错误类型的注册值 | `ServiceKey[str]` 注册整数仍通过 mypy | 显式不变泛型，禁止推断为共同父类型 |
| YAML 重复键可定位 | 只有笼统的 YAML 错误 | 受控诊断保留重复键类别与行列 |
| 非法显式 YAML 标量属于配置错误 | PyYAML 抛出 ValueError / IndexError / KeyError，可能包含配置值 | 在解析边界转为不带输入值的配置错误 |

`test_config.py` 验证配置边界，`test_host.py` 验证生命周期，
`test_demo.py` 运行真实命令，`test_typing.py` 验证公开类型契约。
异步测试使用真实 asyncio 事件门，不依赖网络、模型密钥或固定等待时长。
并发测试的 `sleep(0)` 仅让调度器交付已经请求的取消，不模拟耗时。
