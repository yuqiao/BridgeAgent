"""Real source-file changes through the explicit Python module reload manifest."""

import asyncio
import sys
import types

from bridge_agent.bootstrap.reload import SourceReloader
from bridge_agent.contracts.agent import AGENT_RUNTIME, RunRequest
from bridge_agent.kernel.dynamic import DynamicHost


def source(text):
    return f"""
from bridge_agent.contracts.agent import AGENT_RUNTIME, RunResult
from bridge_agent.contracts.plugins import PluginDefinition
from bridge_agent.contracts.packages import PluginExport
class Runtime:
    async def run(self, request): return RunResult({text!r})
class Plugin:
    async def activate(self, context): context.provide(AGENT_RUNTIME, Runtime())
plugin = PluginExport(1, PluginDefinition('runtime.source', lambda config: Plugin, provides=(AGENT_RUNTIME,)))
"""


def test_changed_source_reloads_and_broken_source_keeps_working_runtime(
    tmp_path, monkeypatch
):
    import pytest

    file = tmp_path / "plugin.py"
    file.write_text(source("one"))
    module = types.ModuleType("bridge_hmr_fixture")
    module.__file__ = str(file)
    module.__package__ = ""
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(file.read_text(), str(file), "exec"), module.__dict__)

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("runtime", module.plugin.definition)
            reloader = SourceReloader(host)
            reloader.register("runtime", module.__name__, "plugin")
            file.write_text(source("two"))
            assert await reloader.check() == ("runtime",)
            request = RunRequest("id", "hello", tmp_path)
            assert (
                await host.context.require(AGENT_RUNTIME).run(request)
            ).text == "two"
            file.write_text("invalid Python source!")
            with pytest.raises(SyntaxError):
                await reloader.check()
            assert (
                await host.context.require(AGENT_RUNTIME).run(request)
            ).text == "two"
            file.write_text(source("new"))
            assert await reloader.check() == ("runtime",)
            assert (
                await host.context.require(AGENT_RUNTIME).run(request)
            ).text == "new"

    asyncio.run(scenario())


def test_declared_dependency_changes_reload_affected_instances(tmp_path, monkeypatch):
    helper_file = tmp_path / "helper.py"
    helper_file.write_text("value = 'one'\n")
    helper = types.ModuleType("bridge_hmr_helper")
    helper.__file__ = str(helper_file)
    helper.__package__ = ""
    exec(compile(helper_file.read_text(), str(helper_file), "exec"), helper.__dict__)
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    file = tmp_path / "plugin.py"
    file.write_text(
        source("unused")
        .replace("RunResult('unused')", "RunResult(value)")
        .replace(
            "class Runtime:", "from bridge_hmr_helper import value\nclass Runtime:"
        )
    )
    module = types.ModuleType("bridge_hmr_dependent")
    module.__file__ = str(file)
    module.__package__ = ""
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(file.read_text(), str(file), "exec"), module.__dict__)

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("runtime", module.plugin.definition)
            reloader = SourceReloader(host)
            reloader.register(
                "runtime", module.__name__, "plugin", dependencies=(helper.__name__,)
            )
            helper_file.write_text("value = 'two'\n")
            assert await reloader.check() == ("runtime",)
            result = await host.context.require(AGENT_RUNTIME).run(
                RunRequest("id", "hello", tmp_path)
            )
            assert result.text == "two"

    asyncio.run(scenario())


def test_shared_contract_change_requires_restart(tmp_path, monkeypatch):
    import pytest

    from bridge_agent.contracts.errors import RestartRequired

    file = tmp_path / "contracts.py"
    file.write_text("version = 1\n")
    module = types.ModuleType("bridge_shared_contracts")
    module.__file__ = str(file)
    monkeypatch.setitem(sys.modules, module.__name__, module)

    async def scenario():
        async with DynamicHost() as host:
            reloader = SourceReloader(host)
            reloader.require_restart(module.__name__)
            file.write_text("version = 2\n")
            with pytest.raises(RestartRequired):
                await reloader.check()

    asyncio.run(scenario())


def test_watcher_applies_config_changes_and_stops_cleanly(tmp_path):
    from bridge_agent.bootstrap.agent import agent_catalog
    from bridge_agent.bootstrap.dynamic import DynamicLoader
    from bridge_agent.contracts.plugins import PluginDefinition

    path = tmp_path / "tree.yaml"
    path.write_text("version: 2\nentries: [{id: runtime, name: runtime.example}]\n")
    observed = asyncio.Event()
    stop = asyncio.Event()

    class Observer:
        async def activate(self, context):
            context.on("hmr.reload", lambda changed: observed.set())

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "observer", PluginDefinition("observer", lambda config: Observer)
            )
            loader = DynamicLoader(host, agent_catalog(environment={}))
            await loader.load(path)
            reloader = SourceReloader(host)
            reloader.watch_config(path, loader)
            path.write_text(
                "version: 2\nentries: [{id: runtime, name: runtime.example, disabled: true}]\n"
            )
            task = asyncio.create_task(reloader.watch(stop, interval=0.01))
            try:
                await asyncio.wait_for(observed.wait(), 3)
                assert host.status("runtime").state == "disposed"
            finally:
                stop.set()
                await task

    asyncio.run(scenario())


def test_reload_updates_parent_package_module_attributes(tmp_path, monkeypatch):
    package = types.ModuleType("bridge_hmr_package")
    package.__path__ = [str(tmp_path)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    helper_file = tmp_path / "helper.py"
    helper_file.write_text("value = 'one'\n")
    helper = types.ModuleType(package.__name__ + ".helper")
    helper.__package__ = package.__name__
    helper.__file__ = str(helper_file)
    exec(compile(helper_file.read_text(), str(helper_file), "exec"), helper.__dict__)
    package.helper = helper
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    file = tmp_path / "plugin.py"
    file.write_text(
        source("unused")
        .replace("RunResult('unused')", "RunResult(helper.value)")
        .replace("class Runtime:", "from . import helper\nclass Runtime:")
    )
    module = types.ModuleType(package.__name__ + ".plugin")
    module.__package__ = package.__name__
    module.__file__ = str(file)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(file.read_text(), str(file), "exec"), module.__dict__)

    async def scenario():
        async with DynamicHost() as host:
            await host.mount("runtime", module.plugin.definition)
            reloader = SourceReloader(host)
            reloader.register(
                "runtime", module.__name__, "plugin", dependencies=(helper.__name__,)
            )
            helper_file.write_text("value = 'two'\n")
            await reloader.check()
            result = await host.context.require(AGENT_RUNTIME).run(
                RunRequest("id", "hello", tmp_path)
            )
            assert result.text == "two"

    asyncio.run(scenario())
