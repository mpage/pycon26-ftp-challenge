"""Build graph simulator challenge - falkben.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.

parallel Kahn scheduler
"""

from __future__ import annotations

import os
import queue
import threading

from graph import BuildGraph, Target  # type: ignore[import-not-found]


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    targets = graph.targets
    n = len(targets)
    if n == 0:
        return {}

    results: dict[str, bytes] = {}

    in_degree = {name: len(t.deps) for name, t in targets.items()}  # remaining inbound edges for each target
    dependents: dict[str, list[Target]] = {name: [] for name in targets}
    for name, target in targets.items():
        for dep in target.deps:
            dependents[dep.name].append(target)

    ready: queue.SimpleQueue = queue.SimpleQueue()  # queue of targets to build
    for name, deg in in_degree.items():
        if deg == 0:
            ready.put(targets[name])

    lock = threading.Lock()  # lock to synchronize access to the results dictionary
    completed = 0
    STOP = object()  # sentinel value to stop the worker thread

    def worker() -> None:
        nonlocal completed
        while True:
            item = ready.get()
            if item is STOP:
                ready.put(STOP)
                return
            dep_results = {d.name: results[d.name] for d in item.deps}
            result = item.build(dep_results)
            results[item.name] = result
            newly_ready = []
            with lock:
                for dep_tgt in dependents[item.name]:
                    in_degree[dep_tgt.name] -= 1
                    if in_degree[dep_tgt.name] == 0:
                        newly_ready.append(dep_tgt)
                completed += 1
                done = completed == n
            for t in newly_ready:
                ready.put(t)
            if done:
                ready.put(STOP)

    num_workers = os.cpu_count() or 8
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(num_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results
