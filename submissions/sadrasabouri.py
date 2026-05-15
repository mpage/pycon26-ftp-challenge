"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

import os
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from graph import BuildGraph, Target


def topological_sort(graph: BuildGraph) -> list[Target]:
    """Return targets in a valid build order."""
    in_degree = {name: len(t.deps) for name, t in graph.targets.items()}
    dependents: dict[str, list[Target]] = {name: [] for name in graph.targets}
    for name, target in graph.targets.items():
        for dep in target.deps:
            dependents[dep.name].append(target)

    queue = deque(graph.targets[name] for name, deg in in_degree.items() if deg == 0)
    order: list[Target] = []
    while queue:
        target = queue.popleft()
        order.append(target)
        for dep in dependents[target.name]:
            in_degree[dep.name] -= 1
            if in_degree[dep.name] == 0:
                queue.append(dep)

    return order


def trivial_sort(graph: BuildGraph) -> list[Target]:
    return list(graph.targets.values())


def my_smart_order(graph: BuildGraph) -> list[Target]:
    """
    My smart! way to order targets in the graph.

    Args:
        graph: The build graph to execute.

    Returns:
        Optimized order for targets
    """
    # TODO: make it better
    return topological_sort(graph)


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    in_degree = {name: len(t.deps) for name, t in graph.targets.items()}
    dependents: dict[str, list[Target]] = {name: [] for name in graph.targets}
    for target in graph.targets.values():
        for dep in target.deps:
            dependents[dep.name].append(target)

    ready = deque(graph.targets[name] for name, deg in in_degree.items() if deg == 0)
    results: dict[str, bytes] = {}

    def build_target(
        target: Target, dep_results: dict[str, bytes]
    ) -> tuple[Target, bytes]:
        return target, target.build(dep_results)

    max_workers = min(24, os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running: set[Future[tuple[Target, bytes]]] = set()

        while ready or running:
            while ready and len(running) < max_workers:
                target = ready.popleft()
                dep_results = {d.name: results[d.name] for d in target.deps}
                running.add(executor.submit(build_target, target, dep_results))

            done, running = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                target, result = future.result()
                results[target.name] = result

                for dependent in dependents[target.name]:
                    in_degree[dependent.name] -= 1
                    if in_degree[dependent.name] == 0:
                        ready.append(dependent)

    return results
