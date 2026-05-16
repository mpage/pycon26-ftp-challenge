"""Persistent-worker build scheduler for the PyCon free-threading challenge."""

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
            dep_results = (
                {dep.name: results[dep.name] for dep in target.deps}
                if target.deps
                else {}
            )
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
    workers = min(MAX_WORKERS, total)
    sentinel = None
    empty_deps: dict[str, bytes] = {}

    def worker(
        ready_get=ready.get,
        ready_put=ready.put,
        target_map=targets,
        result_map=results,
        child_map=dependents,
        remain=remaining,
        lock_obj=lock,
        empty=empty_deps,
    ) -> None:
        nonlocal pending

        while True:
            name = ready_get()
            if name is sentinel:
                return

            target = target_map[name]
            dep_results = (
                {dep.name: result_map[dep.name] for dep in target.deps}
                if target.deps
                else empty
            )
            result = target.build(dep_results)

            with lock_obj:
                result_map[name] = result
                pending -= 1

                if pending == 0:
                    for _ in range(workers):
                        ready_put(sentinel)
                    continue

                for child in child_map[name]:
                    remain[child] -= 1
                    if remain[child] == 0:
                        ready_put(child)

    threads = [threading.Thread(target=worker) for _ in range(workers - 1)]
    for thread in threads:
        thread.start()

    worker()

    for thread in threads:
        thread.join()

    return results
