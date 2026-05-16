"""
Worker pool drains a blocking queue.SimpleQueue, coordination is lock-free via itertools.count atomic next().
Per-target scheduling state attached as Target attributes so hot-path lookups are LOAD_ATTR, not dict[str].
"""

from __future__ import annotations

import queue
import threading
from itertools import count

from graph import BuildGraph, Target


# use eval system physical cores
N_WORKERS = 24


def _build_chain(root: Target) -> dict[str, bytes]:
    # Serial walk: no parallelism in a chain
    results: dict[str, bytes] = {}
    target = root
    while True:
        deps = target.deps
        target._out = target.build({d.name: d._out for d in deps} if deps else {})
        results[target.name] = target._out
        children = target._dependents
        if not children:
            return results
        target = children[0]


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    n_targets = len(targets)

    # Attach scheduling state to Target, instance-attr access beats dict[str].
    roots: list[Target] = []
    sinks = 0
    chainable = True
    for target in targets.values():
        target._out = None
        target._arrival = count()
        target._n_deps_minus_1 = len(target.deps) - 1
        target._dependents = []
        if not target.deps:
            roots.append(target)
    for target in targets.values():
        for dep in target.deps:
            dep._dependents.append(target)
    for target in targets.values():
        if not target._dependents:
            sinks += 1
        if len(target.deps) > 1 or len(target._dependents) > 1:
            chainable = False

    if chainable and len(roots) == 1 and sinks == 1:
        return _build_chain(roots[0])

    ready: queue.SimpleQueue = queue.SimpleQueue()
    for target in roots:
        ready.put(target)

    n_workers = N_WORKERS if n_targets >= N_WORKERS else n_targets
    completed = count()
    completion_threshold = n_targets - 1
    empty_deps: dict[str, bytes] = {}

    # Default args bind enclosing names as LOAD_FAST, do not "modernize" to nonlocal.
    def worker(
        _get=ready.get,
        _put=ready.put,
        _next=next,
        _completed=completed,
        _threshold=completion_threshold,
        _empty_deps=empty_deps,
        _n_workers=n_workers,
    ):
        while True:
            t = _get()
            if t is None:
                return
            deps = t.deps
            # queue's mutex carries release/acquire, d._out write happens-before this read.
            t._out = t.build({d.name: d._out for d in deps} if deps else _empty_deps)
            # itertools.count.__next__ is C-atomic
            for c in t._dependents:
                if _next(c._arrival) == c._n_deps_minus_1:
                    _put(c)
            if _next(_completed) == _threshold:
                for _ in range(_n_workers):
                    _put(None)

    threads = [threading.Thread(target=worker) for _ in range(n_workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return {name: target._out for name, target in targets.items()}
