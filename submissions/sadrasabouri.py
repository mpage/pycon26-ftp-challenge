"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

from graph import BuildGraph


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    # order = topological_sort(graph)
    results: dict[str, bytes] = {}
    for target in graph.targets.values():
        dep_results = {d.name: results[d.name] for d in target.deps}
        results[target.name] = target.build(dep_results)
    return results
