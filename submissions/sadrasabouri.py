"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

from graph import BuildGraph, Target


def my_smart_order(graph: BuildGraph) -> list[Target]:
    """
    My smart! way to order targets in the graph.

    Args:
        graph: The build graph to execute.
    
    Returns:
        Optimized order for targets  
    """
    # TODO: make it better
    return list(graph.targets.values())


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    order = my_smart_order(graph)
    results: dict[str, bytes] = {}
    for target in order:
        dep_results = {d.name: results[d.name] for d in target.deps}
        results[target.name] = target.build(dep_results)
    return results
