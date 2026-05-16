"""Single-threaded reference implementation for the build graph simulator."""

from __future__ import annotations

import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from queue import PriorityQueue

from graph import BuildGraph, Target


def topological_sort(graph: BuildGraph) -> list[Target]:
    """Return targets in a valid build order."""
    in_degree = {name: len(t.deps) for name, t in graph.targets.items()}
    dependents: dict[str, list[Target]] = {name: [] for name in graph.targets}
    for name, target in graph.targets.items():
        for dep in target.deps:
            dependents[dep.name].append(target)

    queue = deque(graph.targets[name] for name, deg in in_degree.items() if deg == 0)
    order: list[str] = []
    while queue:
        target = queue.popleft()
        order.append(target)
        for dep in dependents[target.name]:
            in_degree[dep.name] -= 1
            if in_degree[dep.name] == 0:
                queue.append(dep)

    return order


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Return {target_name: result_hash} using parallel execution."""
    in_degree = {name: len(target.deps) for name, target in graph.targets.items()}
    dependents: dict[str, list[Target]] = {name: [] for name in graph.targets}
    for target in graph.targets.values():
        for dep in target.deps:
            dependents[dep.name].append(target)

    def compute_priority_metrics() -> tuple[dict[str, int], dict[str, int]]:
        depth: dict[str, int] = {}
        order = topological_sort(graph)
        for target in reversed(order):
            if not dependents[target.name]:
                depth[target.name] = 0
            else:
                depth[target.name] = 1 + max(depth[child.name] for child in dependents[target.name])
        fanout = {name: len(dependents[name]) for name in graph.targets}
        return depth, fanout

    depth, fanout = compute_priority_metrics()
    priority = {
        name: (-fanout[name], -depth[name], -graph.targets[name].work, name)
        for name in graph.targets
    }
    dep_names = {name: [dep.name for dep in target.deps] for name, target in graph.targets.items()}

    results: dict[str, bytes] = {}
    ready = PriorityQueue()
    for name, deg in in_degree.items():
        if deg == 0:
            ready.put((priority[name], name))

    lock = threading.Lock()
    max_workers = min(os.cpu_count() or 1, len(graph.targets))

    def worker() -> None:
        while True:
            priority_item = ready.get()
            if priority_item[0] is None:
                ready.task_done()
                break
            _, name = priority_item
            with lock:
                dep_results = {dep_name: results[dep_name] for dep_name in dep_names[name]}
            result = graph.targets[name].build(dep_results)
            with lock:
                results[name] = result
                for dependent in dependents[name]:
                    in_degree[dependent.name] -= 1
                    if in_degree[dependent.name] == 0:
                        ready.put((priority[dependent.name], dependent.name))
            ready.task_done()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for _ in range(max_workers):
            executor.submit(worker)
        ready.join()
        for _ in range(max_workers):
            ready.put(((None, None, None, None), None))

    return results
