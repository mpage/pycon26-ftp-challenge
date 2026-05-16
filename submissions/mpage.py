"""Build graph simulator challenge — parallel scheduler using threads."""

import os
import threading
from queue import SimpleQueue

from graph import BuildGraph, Target


NUM_WORKERS = os.cpu_count() or 24
_SENTINEL = None


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    num_targets = len(targets)

    idx = 0
    for target in targets.values():
        target.index = idx
        idx += 1

    results: list[bytes | None] = [None] * num_targets

    dependents: list[list[Target]] = [[] for _ in range(num_targets)]
    num_sources = 0
    for target in targets.values():
        target.in_degree = len(target.deps)
        if target.in_degree == 0:
            num_sources += 1
        for dep in target.deps:
            dependents[dep.index].append(target)

    max_fan_out = max((len(d) for d in dependents), default=0)

    # Sort dependents heaviest-first for better load balancing
    for dep_list in dependents:
        if len(dep_list) > 1:
            dep_list.sort(key=lambda t: t.work, reverse=True)

    # Sequential fast path for chain-like graphs
    if max_fan_out <= 1 and num_sources <= 1:
        target = None
        for t in targets.values():
            if t.in_degree == 0:
                target = t
                break

        while target is not None:
            tidx = target.index
            dep_results = {d.name: results[d.index] for d in target.deps}
            results[tidx] = target.build(dep_results)
            next_target = None
            for dep in dependents[tidx]:
                dep.in_degree -= 1
                if dep.in_degree == 0:
                    next_target = dep
            target = next_target

        return results

    # Parallel path
    remaining = num_targets
    lock = threading.Lock()
    queue: SimpleQueue[Target | None] = SimpleQueue()

    for target in targets.values():
        if target.in_degree == 0:
            queue.put(target)

    def worker():
        nonlocal remaining
        _results = results
        _dependents = dependents
        _lock = lock
        _queue = queue
        _NW = NUM_WORKERS

        target = _queue.get()
        while target is not _SENTINEL:
            tidx = target.index
            dep_results = {d.name: _results[d.index] for d in target.deps}
            _results[tidx] = target.build(dep_results)

            next_target = None
            with _lock:
                for dep in _dependents[tidx]:
                    dep.in_degree -= 1
                    if dep.in_degree == 0:
                        if next_target is None:
                            next_target = dep
                        else:
                            _queue.put(dep)
                remaining -= 1
                if remaining == 0:
                    for _ in range(_NW):
                        _queue.put(_SENTINEL)

            if next_target is not None:
                target = next_target
            else:
                target = _queue.get()

    threads = []
    for _ in range(NUM_WORKERS - 1):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)

    worker()

    for t in threads:
        t.join()

    return results
