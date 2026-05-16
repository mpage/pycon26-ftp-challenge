"""Parallel build scheduler with inline chaining.

Key optimizations:
- Inline chaining: first ready dependent executes without a queue round-trip
- SimpleQueue (C-implemented) for work distribution
- Root sorting by descending work for critical path scheduling
"""

import os
import queue
import threading

from graph import BuildGraph, Target

NUM_WORKERS = 24


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    n = len(targets)
    if n == 0:
        return {}

    in_degree: dict[str, int] = {}
    dependents: dict[str, list[str]] = {name: [] for name in targets}
    initial: list[str] = []

    for name, target in targets.items():
        deg = len(target.deps)
        in_degree[name] = deg
        if deg == 0:
            initial.append(name)
        for dep in target.deps:
            dependents[dep.name].append(name)

    num_workers = min(NUM_WORKERS, os.cpu_count() or 4)
    results: dict[str, bytes] = {}

    # Chain fast-path: skip threading overhead for linear graphs
    if len(initial) == 1 and all(len(d) <= 1 for d in dependents.values()):
        name = initial[0]
        results[name] = targets[name].build({})
        for _ in range(n - 1):
            child = dependents[name][0]
            target = targets[child]
            results[child] = target.build(
                {dep.name: results[dep.name] for dep in target.deps}
            )
            name = child
        return results

    # Parallel build scheduling
    initial.sort(key=lambda name: targets[name].work, reverse=True)

    q: queue.SimpleQueue = queue.SimpleQueue()
    for name in initial:
        q.put(name)

    lock = threading.Lock()
    completed = 0

    def worker():
        nonlocal completed
        _get = q.get
        _put = q.put

        while True:
            name = _get()
            if name is None:
                return

            while name is not None:
                target = targets[name]
                result = target.build(
                    {dep.name: results[dep.name] for dep in target.deps}
                )

                inline = None
                with lock:
                    results[name] = result
                    completed += 1
                    if completed == n:
                        for _ in range(num_workers - 1):
                            _put(None)
                        return
                    for child in dependents[name]:
                        in_degree[child] -= 1
                        if in_degree[child] == 0:
                            if inline is None:
                                inline = child
                            else:
                                _put(child)
                name = inline

    threads = []
    for _ in range(num_workers - 1):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)

    worker()

    for t in threads:
        t.join()

    return results
