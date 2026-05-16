"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

from __future__ import annotations

from collections import deque

from graph import BuildGraph, Target
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed 

dict_lock = threading.Lock()
new_dep = threading.Event()

def build_and_deploy(target, results):

    finished_deps = False
    while not finished_deps:
        with dict_lock:
            if set(d.name for d in target.deps)<= set(results.keys()):
                dep_results = {d.name: results[d.name] for d in target.deps}
                finished_deps = True
                break

        new_dep.wait()

    result = target.build(dep_results)

    with dict_lock:
        results[target.name] = result
        new_dep.set()
    new_dep.clear()


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
    """Reference implementation. Returns {target_name: result_hash}."""
    order = topological_sort(graph)
    results: dict[str, bytes] = {}

    futures = dict()
    with ThreadPoolExecutor(max_workers=24) as executor:
        for target in order:
            futures[target.name] = executor.submit(build_and_deploy, target, results)

    for _ in as_completed(futures.values()):
        continue

    return results
