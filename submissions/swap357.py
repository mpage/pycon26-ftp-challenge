"""
Now is better than never. Although never is often better than right now.
"""

from __future__ import annotations

import os
import threading
from collections import deque
from itertools import count

from graph import BuildGraph


def _max_layer_width(remaining: dict, dependents: dict) -> int:
    in_deg = dict(remaining)
    frontier = [n for n, d in in_deg.items() if d == 0]
    width = len(frontier)
    while frontier:
        nxt = []
        for n in frontier:
            for c in dependents[n]:
                in_deg[c.name] -= 1
                if in_deg[c.name] == 0:
                    nxt.append(c.name)
        if len(nxt) > width:
            width = len(nxt)
        frontier = nxt
    return width


def _serial(graph: BuildGraph, remaining: dict, dependents: dict) -> dict[str, bytes]:
    targets = graph.targets
    results: dict[str, bytes] = {}
    in_deg = dict(remaining)
    ready = [targets[n] for n, d in in_deg.items() if d == 0]
    while ready:
        t = ready.pop()
        results[t.name] = t.build({d.name: results[d.name] for d in t.deps})
        for c in dependents[t.name]:
            in_deg[c.name] -= 1
            if in_deg[c.name] == 0:
                ready.append(c)
    return results


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    total = len(targets)
    results: dict[str, bytes] = {}

    n_deps = {name: len(t.deps) for name, t in targets.items()}
    dependents: dict[str, list] = {name: [] for name in targets}
    for t in targets.values():
        for d in t.deps:
            dependents[d.name].append(t)

    if _max_layer_width(n_deps, dependents) == 1:
        return _serial(graph, n_deps, dependents)

    arrivals = {name: count() for name in targets}
    completed = count()
    target_threshold = total - 1

    # collections.deque append/popleft are C-level thread-safe (atomic).
    ready: deque = deque()
    for name, n in n_deps.items():
        if n == 0:
            ready.append(targets[name])

    n_workers = min(os.cpu_count() or 1, max(1, total))

    def worker():
        local_pop = ready.popleft
        while True:
            try:
                target = local_pop()
            except IndexError:
                continue
            if target is None:
                return
            dep_results = {d.name: results[d.name] for d in target.deps}
            results[target.name] = target.build(dep_results)
            for child in dependents[target.name]:
                if next(arrivals[child.name]) == n_deps[child.name] - 1:
                    ready.append(child)
            if next(completed) == target_threshold:
                for _ in range(n_workers):
                    ready.append(None)

    threads = [threading.Thread(target=worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results
