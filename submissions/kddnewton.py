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
    for name, target in targets.items():
        if (deps := target.deps):
            for dep in deps:
                dependents[dep.name].append(name)
        else:
            ready.put(name)

    results = {}

    max_fan_in = max(in_degree.values())
    max_fan_out = max((len(value) for value in dependents.values()), default=0)

    if ready.qsize() > 1 or max_fan_out > 1:
        lock = threading.Lock()
        heap = []

        if max_fan_in > NUM_WORKERS and max_fan_out > NUM_WORKERS:
            import heapq

            def worker():
                nonlocal remaining
                while True:
                    if (name := ready.get()) is None:
                        return

                    target = targets[name]
                    results[name] = target.build({dep.name: results[dep.name] for dep in target.deps})

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
                    results[name] = target.build({dep.name: results[dep.name] for dep in target.deps})

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

        for _ in range(NUM_WORKERS - 1):
            _thread.start_joinable_thread(worker, daemon=True)
        worker()
    else:
        name = ready.get()
        results[name] = targets[name].build({})

        for _ in range(remaining - 1):
            dependent = dependents[name][0]
            results[dependent] = targets[dependent].build({name: results[name]})
            name = dependent
