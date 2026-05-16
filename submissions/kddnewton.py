import _thread
import queue
import threading

from graph import BuildGraph

NUM_WORKERS = 24


def build_all(graph: BuildGraph):
    targets = graph.targets
    remaining = len(targets)
    ready = queue.SimpleQueue()
    in_degree = {name: len(target.deps) for name, target in targets.items()}
    dependents = {name: [] for name in targets}
    empty_deps = {}

    for name, target in targets.items():
        if (deps := target.deps):
            for dep in deps:
                dependents[dep.name].append(name)
        else:
            ready.put(name)

    results = {}

    if ready.qsize() > 1 or any(len(value) > 1 for value in dependents.values()):
        lock = threading.Lock()

        def worker():
            nonlocal remaining
            while True:
                if (name := ready.get()) is None:
                    return

                target = targets[name]
                results[name] = target.build({dep.name: results[dep.name] for dep in target.deps} if target.deps else empty_deps)

                with lock:
                    remaining -= 1
                    if remaining == 0:
                        for _ in range(NUM_WORKERS - 1):
                            ready.put(None)
                        return
                    for dependent in dependents[name]:
                        in_degree[dependent] -= 1
                        if in_degree[dependent] == 0:
                            ready.put(dependent)

        handles = [_thread.start_joinable_thread(worker, daemon=True) for _ in range(NUM_WORKERS)]
        for handle in handles:
            handle.join()
    else:
        name = ready.get()
        results[name] = targets[name].build({})

        for _ in range(remaining - 1):
            dependent = dependents[name][0]
            results[dependent] = targets[dependent].build({name: results[name]})
            name = dependent
