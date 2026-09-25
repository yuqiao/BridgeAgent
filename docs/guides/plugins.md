# 插件使用与开发指南

本文面向阶段 1 的静态插件宿主：从 YAML 启用已有插件，到开发一个新服务提供者，再到管理依赖与资源。所有命令在仓库根目录运行，使用 Python 3.13 与 uv，无需模型密钥。

接口与行为以当前源码为准。设计理由、状态机和完整验收矩阵见 [阶段 1 交付文档](../stages/01-plugin-host.md)，后续能力见 [路线图](../roadmap.md)。

## 1. 先运行已有插件

安装锁定依赖：

```bash
uv sync --locked
```

若本机设置了不同的 `UV_DEFAULT_INDEX`，先执行 `export UV_DEFAULT_INDEX=https://pypi.org/simple`，使安装源与锁文件一致。

分别选择三个服务提供者：

```bash
uv run --locked python -m bridge_agent.interfaces.plugin_demo --config examples/plugins.upper.yaml --text Hello
# result: HELLO

uv run --locked python -m bridge_agent.interfaces.plugin_demo --config examples/plugins.lower.yaml --text Hello
# result: hello

uv run --locked python -m bridge_agent.interfaces.plugin_demo --config examples/plugins.reverse.yaml --text Hello
# result: olleH!
```

这三次运行使用同一个应用用例和 Report 消费者，仅通过配置选择不同的 TextTransform 提供者。reverse 是本指南配套的完整开发示例，已经实现并登记，无需重复创建。

## 2. 理解配置与核心概念

[反转示例配置](../../examples/plugins.reverse.yaml)：

```yaml
version: 1
plugins:
  - name: demo.report
    config:
      prefix: "result: "
  - name: demo.reverse
    config:
      suffix: "!"
```

`version` 是配置格式版本，目前只接受整数 `1`。`plugins` 列出本次要启用的插件，`name` 对应显式清单中的名称，`config` 是该插件的参数，省略时默认为空字典。

Catalog 中登记的插件只是可供选择；没有写入 YAML 的插件不会实例化或激活。YAML 不导入任意 Python 路径。配置与各插件参数严格校验，拒绝未知字段、重复键、错误类型、锚点、别名、merge 和自定义对象标签。容易被 YAML 识别为布尔值的文本（例如 `yes`）需要引号。

| 概念 | 示例 | 使用方式 |
| --- | --- | --- |
| 能力接口 | `TextTransform` | 约定消费者可以调用 `transform(text)` |
| 服务键 | `TEXT_TRANSFORM` | 唯一的能力身份，关联名称与静态类型 |
| 服务对象 | `ReverseTransform` | 执行具体业务方法 |
| 插件实例 | `ReversePlugin` | 在激活时装配服务与登记资源 |
| 插件定义 | `REVERSE` | 声明名称、配置处理函数、requires/provides |
| 插件清单 | `PluginCatalog` | 显式登记允许配置选择的定义 |
| 插件上下文 | `PluginContext` | 插件读取依赖、提供服务、登记清理的入口 |
| 插件宿主 | `PluginHost` | 验证依赖，管理启动、回滚与关闭 |

插件名称 `demo.reverse` 用于选择实现，服务名称 `demo.text-transform` 用于表达能力身份。消费者依赖服务键，因此更换提供者不需要修改消费者代码。

```mermaid
flowchart LR
    Y[YAML 选择提供者] --> C[显式 Catalog]
    C --> H[Host 校验并激活]
    H --> P[reverse 插件]
    P --> T[TextTransform 服务]
    T --> R[report 插件]
    R --> S[Report 服务]
    S --> A[应用调用 render]
```

图中箭头表示装配与能力传递。业务调用发生在启动成功后：应用调用 Report，Report 再调用 TextTransform；宿主不参与每次文本转换。

## 3. 开发一个新的服务提供者

以下步骤解释已落地的 reverse 示例。开发自己的插件时，更换插件名称和业务实现，复用同样的分层。

### 3.1 复用或定义能力接口

已有 [能力定义](../../src/bridge_agent/contracts/demo.py)：

```python
from typing import Protocol

from bridge_agent.contracts.plugins import ServiceKey


class TextTransform(Protocol):
    def transform(self, text: str) -> str: ...


TEXT_TRANSFORM = ServiceKey[TextTransform]("demo.text-transform")
```

提供者和消费者都应从这个模块导入同一个 `TEXT_TRANSFORM`，不要在各自模块重建同名键。宿主会拒绝相同名称、不同身份的服务键。

新实现只需满足该 Protocol 的方法约定，无需继承它。`ServiceKey[T]` 让 mypy 检查注册值与读取返回值；这些属于静态类型约束，宿主不会在运行时逐项验证任意对象的方法签名。

已有接口能够表达需求时直接复用。引入新能力时，在 contracts 中定义其接口和唯一键，并让真实消费者使用它。

### 3.2 实现能力、插件与配置入口

完整实现位于 [plugins/reverse.py](../../src/bridge_agent/plugins/reverse.py)：

```python
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from bridge_agent.contracts.demo import TEXT_TRANSFORM
from bridge_agent.contracts.plugins import Plugin, PluginContext, PluginDefinition


class ReverseConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    suffix: str = ""


@dataclass
class ReverseTransform:
    suffix: str

    def transform(self, text: str) -> str:
        return text[::-1] + self.suffix


@dataclass
class ReversePlugin:
    suffix: str

    async def activate(self, context: PluginContext) -> None:
        context.provide(TEXT_TRANSFORM, ReverseTransform(self.suffix))


def prepare_reverse(config: Mapping[str, object]) -> Callable[[], Plugin]:
    parsed = ReverseConfig.model_validate(dict(config))
    return lambda: ReversePlugin(suffix=parsed.suffix)


REVERSE = PluginDefinition(
    name="demo.reverse",
    prepare=prepare_reverse,
    provides=(TEXT_TRANSFORM,),
)
```

`ReverseTransform` 完成业务，`ReversePlugin` 完成装配。所有插件统一使用 `async activate`，其中可以包含简单同步操作；耗时阻塞操作应由具体实现妥善处理。

三个准备步骤有不同约束：

| 步骤 | 职责 | 资源约束 |
| --- | --- | --- |
| `prepare(config)` | 校验配置，返回绑定配置的 factory | 不打开连接、文件或启动任务 |
| `factory()` | 创建实例并保存配置 | 不获取需要清理的资源 |
| `activate(context)` | 取得依赖、获取资源、登记服务 | 资源取得后及时交给上下文管理 |

`return lambda: ReversePlugin(...)` 返回创建函数，尚未创建插件实例。全部配置与依赖预检成功后，宿主构造全部实例，再按依赖顺序激活。因此，晚出现的无效配置或构造错误不会导致一部分插件已经激活。

### 3.3 在组合入口登记

[bootstrap/demo.py](../../src/bridge_agent/bootstrap/demo.py) 负责显式登记：

```python
from bridge_agent.bootstrap.config import PluginCatalog
from bridge_agent.plugins.demo import LOWERCASE, REPORT_PLUGIN, UPPERCASE
from bridge_agent.plugins.reverse import REVERSE


def demo_catalog() -> PluginCatalog:
    return PluginCatalog((UPPERCASE, LOWERCASE, REVERSE, REPORT_PLUGIN))
```

最后在 YAML 中选择 `demo.reverse`。第 1 节的命令经过真实的配置解析、依赖检查、激活和应用调用路径，输出 `result: olleH!`。应用与 Report 消费者不需要修改。

## 4. 插件之间如何协作

现有 Report 插件同时消费 TextTransform、提供 Report，定义如下（摘自 [demo.py](../../src/bridge_agent/plugins/demo.py)）：

```python
REPORT_PLUGIN = PluginDefinition(
    "demo.report",
    _prepare_report,
    requires=(TEXT_TRANSFORM,),
    provides=(REPORT,),
)
```

它的激活方法读取依赖，再提供服务：

```python
async def activate(self, context: PluginContext) -> None:
    transform = context.require(TEXT_TRANSFORM)
    context.provide(REPORT, _Report(transform, self.prefix))
```

以上是已有类的片段，`_prepare_report`、`_Report` 和服务键定义见链接源码。

- `requires/provides` 是启动前声明，帮助宿主验证依赖和计算激活顺序。
- `require/provide` 是激活时操作，必须符合声明。读取未声明依赖、提供未声明服务、重复提供或少提供都会使启动失败。
- `provide()` 暂存贡献，只有该插件激活成功且全部声明兑现后，宿主才发布服务。
- Report 可以在 YAML 中写在提供者前面，宿主仍先激活提供者。每一批就绪插件按输入顺序稳定排列。
- 插件启动只负责就绪与装配；用户任务在服务方法中执行。

## 5. 从 Python 应用调用服务

下面的完整程序可从仓库根目录直接执行：

```bash
uv run --locked python - <<'PY'
import asyncio
from pathlib import Path

from bridge_agent.application.demo import render_report
from bridge_agent.bootstrap.demo import demo_catalog
from bridge_agent.contracts.demo import REPORT
from bridge_agent.kernel.host import PluginHost


async def main() -> None:
    plugins = demo_catalog().load(Path("examples/plugins.reverse.yaml"))
    async with PluginHost(plugins) as host:
        report = host.resolve(REPORT)
        print(render_report(report, "Hello"))


asyncio.run(main())
PY
# result: olleH!
```

`load()` 返回已验证配置绑定的 PreparedPlugin。进入 `async with` 会启动宿主，进入成功时所有插件已经激活；离开时等待清理完成，应用异常也会触发关闭。

入口负责装配与宿主生命周期，应用用例只接收 `Report` 接口。插件内部通过 `ctx.require()` 取得声明的依赖，应用入口通过 `host.resolve()` 取得已发布服务。

服务引用只在宿主运行期间有效。停止前先结束业务调用，关闭后不要继续使用保留的对象。宿主关闭后拒绝新的 resolve，但不能撤销外部已保存的普通 Python 引用，也不会自动排空应用自行创建的任务。

## 6. 管理资源与关闭

插件需要文件句柄、连接或其他资源时，通过 PluginContext 登记其归属。以下是放在 `activate()` 内部的用法片段，`resource` / `manager` 指具体插件取得的对象。

| API | 用途 |
| --- | --- |
| `ctx.enter_context(manager)` | 进入同步上下文管理器，并接管退出 |
| `await ctx.enter_async_context(manager)` | 进入异步上下文管理器，并接管退出 |
| `ctx.on_close(resource.close)` | 登记同步清理函数 |
| `ctx.on_close_async(resource.aclose)` | 登记返回 awaitable 的异步清理函数 |

可直接放入文件型插件的激活方法：

```python
async def activate(self, context: PluginContext) -> None:
    stream = context.enter_context(open(self.path, encoding="utf-8"))
    # 使用 stream 构造并登记本插件声明的服务。
```

此处 `self.path` 由具体插件配置提供。该片段只说明资源获取；完整插件仍需兑现 provides 声明。文件会在激活返回后保持打开，直到宿主关闭或启动回滚。

传给 `on_close` 的是函数本身：`resource.close`；写成 `resource.close()` 会立即执行关闭。当前宿主没有自动调用插件 `close()` 的约定，清理必须显式登记。

资源管理遵循以下规则：

1. 仅在本插件激活期间登记服务和长期资源。业务调用中的临时资源用方法内部的 `with` / `async with` 管理。
2. 一个插件内按资源登记逆序释放，插件之间按激活逆序关闭。消费者清理时，其提供者仍可访问。
3. 获取资源后立即登记；未交给上下文管理的资源无法由宿主自动发现。上下文管理器进入失败时，它必须自行处理未交接的部分资源。
4. 单项清理失败仍尝试其余资源，最后汇总错误。重复关闭不重复执行清理，也不重放此前报告的清理错误。
5. `enter_context` / `enter_async_context` 用于生命周期资源释放，退出时传入 `(None, None, None)`；需要依据业务异常提交或回滚的事务应放在服务方法的调用范围内。

## 7. 启动失败、取消与错误定位

启动流程为：配置校验 → 依赖预检 → 创建全部实例 → 按依赖激活 → 应用调用。任一插件激活失败，会先清理其部分资源，再逆序清理已激活插件；尚未激活的插件不再启动。

| 现象 | 原因与处理 |
| --- | --- |
| `unknown plugin` | YAML 名称未加入 Catalog，检查显式登记与拼写 |
| `missing provider` | 消费者已启用，但 YAML 未选择依赖的服务提供者 |
| `duplicate providers` | 同一次运行选了多个相同服务的提供者，例如 upper 和 reverse |
| `conflicting key identities` | 不同模块重新创建了同名键，改为导入 contracts 中的唯一键 |
| `duplicate mapping key` | YAML 字段重复，按错误中的文件与行列修正 |
| `string_type` / `extra_forbidden` | 配置值类型错误或存在未知字段，按配置模型修正 |
| `Plugin ...: activation failed` | Python 异常的 `__cause__` 保留具体原因，包括声明违约和实际激活错误 |
| `Plugin cleanup failed` 异常组 | 检查异常组各项及其原因，其他清理已继续尝试 |
| `start requires NEW` / `resolve requires RUNNING` | 调用时机错误；关闭或失败后创建新宿主 |

演示命令在失败时返回非零退出码，配置错误输出不包含原始配置值。Python 调用方可沿异常原因和异常组检查原始失败；这些原始异常可能包含插件自己的诊断内容，不应直接作为脱敏日志。

启动中需要中断时，取消执行 `host.start()` 的任务；直接调用 `close()` 会得到状态错误。启动或关闭被取消时，宿主等待已登记资源清理结束，再传播 `CancelledError`；清理错误作为 cause 保留。并发关闭等待同一清理任务。具体资源应自行设定超时，宿主没有全局强制关闭期限。

手动验证配置失败，可使用临时文件：

```bash
config_path=$(mktemp)
cat > "$config_path" <<'YAML'
version: 1
plugins:
  - name: demo.report
YAML
uv run --locked python -m bridge_agent.interfaces.plugin_demo --config "$config_path" --text Hello
echo $?
# 报告 missing provider，退出码为 1
rm "$config_path"
```

## 8. 验证自己的插件

现有端到端测试 [test_demo.py](../../tests/test_demo.py) 使用真实 YAML 运行命令，验证 upper、lower、reverse 的固定预期输出。reverse 示例按 TDD 加入：先运行测试观察未知插件错误，再实现并登记插件使测试通过。

```bash
# 本指南的新增插件示例
uv run --locked pytest tests/test_demo.py -k reverse -v

# 配置与真实命令
uv run --locked pytest tests/test_config.py tests/test_demo.py -v

# 依赖、回滚、资源关闭与取消
uv run --locked pytest tests/test_host.py -v

# 类型约束与所有源码检查
uv run --locked pytest tests/test_typing.py -v
uv run --locked mypy
uv run --locked ruff check .
uv run --locked ruff format --check .
```

增加插件时先选择一个用户可观察的行为，例如“指定 YAML 后，同一个消费者得到新的预期结果”，写失败测试，再实现最小改动。配置错误通过 Catalog 或真实命令验证；资源生命周期通过真实插件、宿主和可观察资源验证。避免直接测试内部注册表或 mock 宿主流程。已确认测试边界见 [测试说明](../../tests/README.md)。

## 9. 当前边界与源码导航

- 一次宿主运行中，每种插件最多一个实例，每项服务最多一个提供者；一个插件可以提供多项不同服务。
- 宿主单次使用，关闭或失败后不可重新启动。修改配置后创建新宿主。
- 插件为可信、同进程 Python 代码。登记服务不会自动把它暴露为模型工具；Agent 和工具适配在后续阶段实现。
- 新插件通过显式清单接入；外部包发现安排在阶段 6，动态加载、作用域与热重载在阶段 7。
- YAML 不执行表达式、自动安装包或插值环境变量。模型凭据的环境变量引用在模型接入阶段定义。

建议阅读顺序：

1. [能力接口与服务键](../../src/bridge_agent/contracts/demo.py)。
2. [完整 reverse 插件](../../src/bridge_agent/plugins/reverse.py)。
3. [Report 消费者及其他提供者](../../src/bridge_agent/plugins/demo.py)。
4. [显式清单](../../src/bridge_agent/bootstrap/demo.py) 与 [YAML 配置](../../examples/plugins.reverse.yaml)。
5. [应用用例](../../src/bridge_agent/application/demo.py) 与 [命令入口](../../src/bridge_agent/interfaces/plugin_demo.py)。
6. 需要理解内部行为时，再读 [插件协议](../../src/bridge_agent/contracts/plugins.py) 和 [宿主](../../src/bridge_agent/kernel/host.py)。
