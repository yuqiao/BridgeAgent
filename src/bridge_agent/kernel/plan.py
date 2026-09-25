"""Validate all declarations before producing a deterministic activation order."""

from graphlib import CycleError, TopologicalSorter

from bridge_agent.contracts.errors import DependencyError
from bridge_agent.contracts.plugins import PreparedPlugin, ServiceIdentity


def activation_plan(plugins: tuple[PreparedPlugin, ...]) -> tuple[PreparedPlugin, ...]:
    """Reject missing/conflicting declarations and return the full sorted plan."""
    by_name: dict[str, PreparedPlugin] = {}
    canonical: dict[str, ServiceIdentity] = {}
    providers: dict[ServiceIdentity, str] = {}
    for plugin in plugins:
        if not plugin.name.strip() or plugin.name in by_name:
            raise DependencyError(f"Invalid or repeated plugin name: {plugin.name!r}")
        by_name[plugin.name] = plugin
        for label, keys in (
            ("requires", plugin.requires),
            ("provides", plugin.provides),
        ):
            if len(set(keys)) != len(keys):
                raise DependencyError(f"Plugin {plugin.name}: repeated {label} key")
            for key in keys:
                previous = canonical.setdefault(key.name, key)
                if previous is not key:
                    raise DependencyError(
                        f"Service {key.name}: conflicting key identities"
                    )
        for key in plugin.provides:
            if key in providers:
                raise DependencyError(
                    f"Service {key.name}: duplicate providers "
                    f"{providers[key]} and {plugin.name}"
                )
            providers[key] = plugin.name

    graph: dict[str, tuple[str, ...]] = {}
    for plugin in plugins:
        dependencies: list[str] = []
        for key in plugin.requires:
            provider = providers.get(key)
            if provider is None:
                raise DependencyError(
                    f"Plugin {plugin.name}: missing provider for {key.name}"
                )
            if provider == plugin.name:
                raise DependencyError(
                    f"Plugin {plugin.name}: self-dependency {key.name}"
                )
            if provider not in dependencies:
                dependencies.append(provider)
        graph[plugin.name] = tuple(dependencies)

    sorter = TopologicalSorter(graph)
    try:
        sorter.prepare()
    except CycleError as error:
        cycle = " -> ".join(str(name) for name in error.args[1])
        raise DependencyError(f"Plugin dependency cycle: {cycle}") from error

    positions = {plugin.name: index for index, plugin in enumerate(plugins)}
    ordered: list[PreparedPlugin] = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready(), key=positions.__getitem__)
        ordered.extend(by_name[name] for name in ready)
        sorter.done(*ready)
    return tuple(ordered)
