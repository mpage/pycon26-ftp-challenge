"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

from collections import deque

from graph import BuildGraph


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    results: dict[str, bytes] = {}

    remaining_deps = {name: len(t.deps) for name, t in graph.targets.items()}
    dependents: dict[str, list[str]] = {name: [] for name in graph.targets}
    for name, target in graph.targets.items():
        for dep in target.deps:
            dependents[dep.name].append(name)

    ready = deque(name for name, n in remaining_deps.items() if n == 0)
    while ready:
        name = ready.popleft()
        target = graph.targets[name]
        dep_results = {d.name: results[d.name] for d in target.deps}
        results[name] = target.build(dep_results)
        for dependent in dependents[name]:
            remaining_deps[dependent] -= 1
            if remaining_deps[dependent] == 0:
                ready.append(dependent)

    return results
