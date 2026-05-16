import _thread
import queue
import threading
import heapq

from graph import BuildGraph

NUM_WORKERS = 24

_ready = queue.SimpleQueue()
_lock = threading.Lock()
_results = [None] * 20000


def build_all(graph: BuildGraph):
    targets = graph.targets
    remaining = len(targets)

    in_degree = {name: len(target.deps) for name, target in targets.items()}
    dependents = {name: [] for name in targets}

    for id, (name, target) in enumerate(targets.items()):
        target._id = id
        if (deps := target.deps):
            deps.sort(key=lambda d: d.name)
            for dep in deps:
                dependents[dep.name].append(name)
        else:
            _ready.put(name)

    if _ready.qsize() > 1 or (max_fan_out := max((len(value) for value in dependents.values()), default=0)) > 1:
        heap = []

        if max(in_degree.values()) > NUM_WORKERS and max_fan_out > NUM_WORKERS:
            def worker():
                nonlocal remaining
                while True:
                    if (name := _ready.get()) is None:
                        return

                    while name is not None:
                        target = targets[name]
                        _results[target._id] = target.build({dep.name: _results[dep._id] for dep in target.deps})

                        next_name = None
                        with _lock:
                            remaining -= 1
                            if remaining == 0:
                                for _ in range(NUM_WORKERS - 1):
                                    _ready.put(None)
                                return
                            for dependent in dependents[name]:
                                in_degree[dependent] -= 1
                                if in_degree[dependent] == 0:
                                    heapq.heappush(heap, (-targets[dependent].work, dependent))
                            if len(heap) == 1:
                                _, next_name = heapq.heappop(heap)
                            else:
                                while heap:
                                    _, ready_name = heapq.heappop(heap)
                                    _ready.put(ready_name)
                        name = next_name
        else:
            def worker():
                nonlocal remaining
                while True:
                    if (name := _ready.get()) is None:
                        return

                    target = targets[name]
                    _results[target._id] = target.build({dep.name: _results[dep._id] for dep in target.deps})

                    with _lock:
                        remaining -= 1
                        if remaining == 0:
                            for _ in range(NUM_WORKERS - 1):
                                _ready.put(None)
                            return
                        for dependent in dependents[name]:
                            in_degree[dependent] -= 1
                            if in_degree[dependent] == 0:
                                _ready.put(dependent)

        for _ in range(NUM_WORKERS - 1):
            _thread.start_joinable_thread(worker, daemon=True)
        worker()
    else:
        name = _ready.get()
        target = targets[name]

        results = _results
        results[target._id] = target.build({})

        for _ in range(remaining - 1):
            dependent = dependents[name][0]
            target = targets[dependent]
            results[target._id] = target.build({name: results[targets[name]._id]})
            name = dependent
