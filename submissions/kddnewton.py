import queue
import threading
import heapq

from graph import BuildGraph

NUM_WORKERS = 24


def build_all(graph: BuildGraph):
    targets = graph.targets
    remaining = len(targets)

    heap = []
    ready = queue.SimpleQueue()

    in_degree = {name: len(target.deps) for name, target in targets.items()}
    dependents = {name: [] for name in targets}
    empty_deps = {}

    for name, target in targets.items():
        if (deps := target.deps):
            for dep in deps:
                dependents[dep.name].append(name)
        else:
            heapq.heappush(heap, (-target.work, name))

    results = {}

    max_fan_in = max(in_degree.values())
    max_fan_out = max((len(v) for v in dependents.values()), default=0)

    if len(heap) > 1 or max_fan_out > 1:
        lock = threading.Lock()

        while heap:
            _, name = heapq.heappop(heap)
            ready.put(name)

        if max_fan_in > NUM_WORKERS and max_fan_out > NUM_WORKERS:
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
                                heapq.heappush(heap, (-targets[dependent].work, dependent))
                        while heap:
                            _, ready_name = heapq.heappop(heap)
                            ready.put(ready_name)
        else:
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

        threads = [threading.Thread(target=worker) for _ in range(NUM_WORKERS - 1)]
        for thread in threads:
            thread.start()
        worker()
        for thread in threads:
            thread.join()
    else:
        _, name = heap[0]
        results[name] = targets[name].build({})

        for _ in range(remaining - 1):
            dependent = dependents[name][0]
            results[dependent] = targets[dependent].build({name: results[name]})
            name = dependent
