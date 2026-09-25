"""A file-backed dynamic plugin tree with explicit scopes and lifecycle."""

import asyncio

from bridge_agent.bootstrap.agent import agent_catalog
from bridge_agent.bootstrap.dynamic import DynamicLoader
from bridge_agent.contracts.agent import AGENT_RUNTIME, RunRequest
from bridge_agent.kernel.dynamic import DynamicHost


def test_yaml_group_loads_an_external_runtime_and_can_be_disabled(tmp_path):
    path = tmp_path / "dynamic.yaml"
    path.write_text("""version: 2
entries:
  - id: group
  - id: runtime
    parent: group
    name: runtime.example
""")

    async def scenario():
        async with DynamicHost() as host:
            loader = DynamicLoader(host, agent_catalog(environment={}))
            await loader.load(path)
            assert loader.locate("runtime") == "group/runtime"
            result = await host.context.require(AGENT_RUNTIME).run(
                RunRequest("id", "hello", tmp_path)
            )
            assert result.text == "external: hello"
            await loader.update("group", disabled=True)
            assert host.status("runtime").state == "disposed"
            await loader.update("group", disabled=False)
            assert host.status("runtime").state == "active"

    asyncio.run(scenario())


def test_group_scopes_separate_multiple_instances_of_the_same_plugin(tmp_path):
    path = tmp_path / "dynamic.yaml"
    path.write_text("""version: 2
entries:
  - id: root-runtime
    name: runtime.example
  - id: group
    scope: {agent.runtime: second}
  - id: child-runtime
    parent: group
    name: runtime.example
""")

    async def scenario():
        async with DynamicHost() as host:
            loader = DynamicLoader(host, agent_catalog(environment={}))
            await loader.load(path)
            first = host.context.require(AGENT_RUNTIME)
            second = loader.context("child-runtime").require(AGENT_RUNTIME)
            assert first is not second
            assert loader.resolve("child-runtime").parent == "group"

    asyncio.run(scenario())


def test_update_create_remove_move_and_save_preserve_unaffected_instance(tmp_path):
    path = tmp_path / "dynamic.yaml"
    path.write_text(
        "version: 2\nentries: [{id: runtime, name: runtime.example}, {id: group}]\n"
    )

    async def scenario():
        async with DynamicHost() as host:
            loader = DynamicLoader(host, agent_catalog(environment={}))
            await loader.load(path)
            runtime = host.context.require(AGENT_RUNTIME)
            await loader.create({"id": "child", "parent": "group"})
            assert host.context.require(AGENT_RUNTIME) is runtime
            await loader.update("child", parent=None)
            assert loader.locate("child") == "child"
            await loader.remove("group")
            assert loader.resolve("child").parent is None
            target = tmp_path / "saved.yaml"
            loader.save(target)
            assert "child" in target.read_text() and "group" not in target.read_text()
            assert (
                path.read_text()
                == "version: 2\nentries: [{id: runtime, name: runtime.example}, {id: group}]\n"
            )

    asyncio.run(scenario())


def test_failed_tree_change_rolls_back_all_previous_entries(tmp_path):
    import pytest

    from bridge_agent.bootstrap.config import PluginCatalog
    from bridge_agent.contracts.errors import PluginActivationError
    from bridge_agent.contracts.plugins import PluginDefinition, ServiceKey

    key = ServiceKey[str]("value")

    def prepare(config):
        class Provider:
            async def activate(self, context):
                context.provide(key, config["value"])

        return Provider

    class Broken:
        async def activate(self, context):
            raise RuntimeError("broken")

    catalog = PluginCatalog(
        (
            PluginDefinition("provider", prepare, provides=(key,)),
            PluginDefinition("broken", lambda config: Broken),
        )
    )
    path = tmp_path / "tree.yaml"
    path.write_text(
        "version: 2\nentries: [{id: value, name: provider, config: {value: old}}]\n"
    )

    async def scenario():
        async with DynamicHost() as host:
            loader = DynamicLoader(host, catalog)
            await loader.load(path)
            path.write_text(
                "version: 2\nentries: [{id: value, name: provider, config: {value: new}}, {id: broken, name: broken}]\n"
            )
            with pytest.raises(PluginActivationError):
                await loader.load(path)
            assert host.context.require(key) == "old"
            assert loader.resolve("value").config == {"value": "old"}

    asyncio.run(scenario())


def test_entry_injection_waits_and_intercept_is_visible_to_plugin(tmp_path):
    from bridge_agent.bootstrap.config import PluginCatalog
    from bridge_agent.contracts.plugins import PluginDefinition, ServiceKey

    required = ServiceKey[str]("required")
    result = ServiceKey[str]("result")

    class Consumer:
        async def activate(self, context):
            context.provide(
                result,
                context.require(required) + context.config_for(required)["suffix"],
            )

    class Provider:
        async def activate(self, context):
            context.provide(required, "value")

    catalog = PluginCatalog(
        (
            PluginDefinition("consumer", lambda config: Consumer, provides=(result,)),
            PluginDefinition("provider", lambda config: Provider, provides=(required,)),
        )
    )
    path = tmp_path / "tree.yaml"
    path.write_text(
        'version: 2\nentries: [{id: consumer, name: consumer, inject: [required], intercept: {required: {suffix: "!"}}}]\n'
    )

    async def scenario():
        async with DynamicHost() as host:
            loader = DynamicLoader(host, catalog)
            await loader.load(path)
            assert host.status("consumer").state == "pending"
            await loader.create({"id": "provider", "name": "provider"})
            assert host.context.require(result) == "value!"

    asyncio.run(scenario())


def test_vetoed_update_does_not_save_unapplied_configuration(tmp_path):
    import pytest

    from bridge_agent.bootstrap.config import PluginCatalog
    from bridge_agent.contracts.errors import ConfigurationError
    from bridge_agent.contracts.plugins import PluginDefinition

    class Observer:
        async def activate(self, context):
            context.on(
                "internal.update",
                lambda ident, config, next: False if config.get("deny") else next(),
            )

    class Target:
        async def activate(self, context):
            pass

    path = tmp_path / "tree.yaml"
    path.write_text("version: 2\nentries: [{id: target, name: target}]\n")

    async def scenario():
        async with DynamicHost() as host:
            await host.mount(
                "observer", PluginDefinition("observer", lambda config: Observer)
            )
            loader = DynamicLoader(
                host,
                PluginCatalog((PluginDefinition("target", lambda config: Target),)),
            )
            await loader.load(path)
            with pytest.raises(ConfigurationError, match="veto"):
                await loader.update("target", config={"deny": True})
            assert loader.resolve("target").config == {}

    asyncio.run(scenario())


def test_loader_preflight_uses_normalized_plugin_configuration(tmp_path):
    from bridge_agent.bootstrap.config import PluginCatalog
    from bridge_agent.contracts.plugins import PluginDefinition, ServiceKey

    key = ServiceKey[str]("normalized")

    def prepare(config):
        value = config["value"]

        class Provider:
            async def activate(self, context):
                context.provide(key, value)

        return Provider

    definition = PluginDefinition(
        "normalized",
        prepare,
        provides=(key,),
        validate_config=lambda config: {"value": config.get("value", "default")},
    )
    path = tmp_path / "tree.yaml"
    path.write_text("version: 2\nentries: [{id: provider, name: normalized}]\n")

    async def scenario():
        async with DynamicHost() as host:
            loader = DynamicLoader(host, PluginCatalog((definition,)))
            await loader.load(path)
            assert host.context.require(key) == "default"

    asyncio.run(scenario())
