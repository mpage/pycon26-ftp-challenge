from __future__ import annotations
import os
import queue
import threading

from graph import BuildGraph

_SENTINEL = object()
NUM_WORKERS = 24


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build every target in `graph` in parallel, respecting dependencies.

    Uses Kahn's algorithm with three key optimizations:
      - Chain fast path: if the graph is a simple chain (single root,
        max fan-out 1), skip all threading overhead and build sequentially.
      - Inline execution: when a completed target has exactly one newly-ready
        dependent, build it directly in the same thread instead of enqueuing.
      - Main-thread-as-worker: spawn N-1 helper threads and use the main
        thread as the Nth worker, saving one thread creation.
    """
    targets = graph.targets
    n_total = len(targets)
    if n_total == 0:
        return {}

    remaining = {name: len(target.deps) for name, target in targets.items()}
    dependents: dict[str, list[str]] = {name: [] for name in targets}
    for name, target in targets.items():
        for dep in target.deps:
            # Add parent's name as a dependent for child
            dependents[dep.name].append(name)

    roots = [name for name, count in remaining.items() if count == 0]
    max_fan_out = max((len(children) for children in dependents.values()), default=0)

    # --- Chain fast path: skip threading entirely for linear graphs ---
    if len(roots) == 1 and max_fan_out <= 1:
        results: dict[str, bytes] = {}
        name = roots[0]
        while True:
            target = targets[name]
            dep_results = {dep.name: results[dep.name] for dep in target.deps}
            results[name] = target.build(dep_results)
            children = dependents[name]
            if not children:
                return results
            name = children[0]

    # --- Parallel path ---
    results: dict[str, bytes] = {}
    lock = threading.Lock()
    pending = n_total
    ready: queue.SimpleQueue = queue.SimpleQueue()

    for name in roots:
        ready.put(name)

    n_workers = min(NUM_WORKERS, os.cpu_count() or 1, n_total)

    def worker() -> None:
        nonlocal pending

        name = ready.get()
        while name is not _SENTINEL:
            target = targets[name]
            dep_results = {dep.name: results[dep.name] for dep in target.deps}
            result = target.build(dep_results)

            # Under lock: store result, decrement dependents, find next target.
            inline = None
            with lock:
                results[name] = result
                pending -= 1
                if pending == 0:
                    for _ in range(n_workers):
                        ready.put(_SENTINEL)
                else:
                    for child in dependents[name]:
                        remaining[child] -= 1
                        if remaining[child] == 0:
                            if inline is None:
                                inline = child
                            else:
                                ready.put(child)

            if inline is not None:
                name = inline
            else:
                name = ready.get()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(n_workers - 1)]
    for t in threads:
        t.start()

    worker()

    for t in threads:
        t.join()

    return results
