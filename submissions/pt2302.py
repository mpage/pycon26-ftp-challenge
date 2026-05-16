"""Persistent-worker build scheduler for the PyCon free-threading challenge."""

import os
import queue
import threading

from graph import BuildGraph


MAX_WORKERS = 24


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    total = len(targets)
    if total == 0:
        return {}

    remaining = {name: len(target.deps) for name, target in targets.items()}
    dependents: dict[str, list[str]] = {name: [] for name in targets}
    for name, target in targets.items():
        for dep in target.deps:
            dependents[dep.name].append(name)

    roots = [name for name, count in remaining.items() if count == 0]
    leaves = sum(1 for children in dependents.values() if not children)

    if (
        len(roots) == 1
        and leaves == 1
        and all(count <= 1 for count in remaining.values())
        and all(len(children) <= 1 for children in dependents.values())
    ):
        results: dict[str, bytes] = {}
        name = roots[0]
        while True:
            target = targets[name]
            deps = target.deps
            if deps:
                dep = deps[0]
                dep_results = {dep.name: results[dep.name]}
            else:
                dep_results = {}
            results[name] = target.build(dep_results)

            children = dependents[name]
            if not children:
                return results
            name = children[0]

    ready: queue.SimpleQueue[str | None] = queue.SimpleQueue()
    for name in roots:
        ready.put(name)

    results: dict[str, bytes] = {}
    lock = threading.Lock()
    pending = total
    workers = min(MAX_WORKERS, os.cpu_count() or 1, total)
    sentinel = None
    empty_deps: dict[str, bytes] = {}

    def worker() -> None:
        nonlocal pending

        while True:
            name = ready.get()
            if name is sentinel:
                return

            target = targets[name]
            deps = target.deps
            if not deps:
                dep_results = empty_deps
            elif len(deps) == 1:
                dep = deps[0]
                dep_results = {dep.name: results[dep.name]}
            else:
                dep_results = {dep.name: results[dep.name] for dep in deps}
            result = target.build(dep_results)

            with lock:
                results[name] = result
                pending -= 1

                if pending == 0:
                    for _ in range(workers):
                        ready.put(sentinel)
                    continue

                for child in dependents[name]:
                    remaining[child] -= 1
                    if remaining[child] == 0:
                        ready.put(child)

    threads = [threading.Thread(target=worker) for _ in range(workers - 1)]
    for thread in threads:
        thread.start()

    worker()

    for thread in threads:
        thread.join()

    return results
