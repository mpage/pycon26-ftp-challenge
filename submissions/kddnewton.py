import ctypes
import heapq
import queue
import threading

from graph import BuildGraph


_NUM_WORKERS = 24
_NUM_WORKERS_M1 = _NUM_WORKERS - 1


def immortalize(obj):
    ctypes.c_uint32.from_address(id(obj) + 12).value = 0xFFFFFFFF


_ready = queue.SimpleQueue()
_lock = threading.Lock()
_results = [None] * 20000
_SENTINEL = object()

immortalize(_ready)
immortalize(_lock)
immortalize(_results)
immortalize(_SENTINEL)


class _Remaining:
    __slots__ = ("value",)
    def __init__(self, value):
        self.value = value


def build_all(graph: BuildGraph):
    targets = graph.targets
    remaining = _Remaining(len(targets))

    for id, (name, target) in enumerate(targets.items()):
        target._id = id
        target._dependents = []

    for target in targets.values():
        if (deps := target.deps):
            deps.sort(key=lambda dep: dep.name)
            target._in_degree = len(deps)
            for dep in deps:
                dep._dependents.append(target)
        else:
            target._in_degree = 0
            _ready.put(target)

    if _ready.qsize() > 1 or (max_fan_out := max((len(target._dependents) for target in targets.values()), default=0)) > 1:
        heap = []

        if max(target._in_degree for target in targets.values()) > _NUM_WORKERS and max_fan_out > _NUM_WORKERS:
            def worker(remaining):
                target = _ready.get()

                while target is not _SENTINEL:
                    _results[target._id] = target.build({dep.name: _results[dep._id] for dep in target.deps})

                    next_target = None
                    with _lock:
                        if remaining.value == 1:
                            for _ in range(_NUM_WORKERS_M1):
                                _ready.put(_SENTINEL)
                            return

                        remaining.value -= 1
                        for dependent in target._dependents:
                            if dependent._in_degree == 1:
                                heapq.heappush(heap, (-dependent.work, dependent._id, dependent))
                            else:
                                dependent._in_degree -= 1

                        if len(heap) == 1:
                            next_target = heapq.heappop(heap)[2]
                        else:
                            while heap:
                                _ready.put(heapq.heappop(heap)[2])

                    if next_target is not None:
                        target = next_target
                    else:
                        target = _ready.get()
        else:
            def worker(remaining):
                while (target := _ready.get()) is not _SENTINEL:
                    _results[target._id] = target.build({dep.name: _results[dep._id] for dep in target.deps})

                    with _lock:
                        if remaining.value == 1:
                            for _ in range(_NUM_WORKERS_M1):
                                _ready.put(_SENTINEL)
                            return

                        remaining.value -= 1
                        for dependent in target._dependents:
                            if dependent._in_degree == 1:
                                _ready.put(dependent)
                            else:
                                dependent._in_degree -= 1

        for _ in range(_NUM_WORKERS_M1):
            threading.Thread(target=worker, args=(remaining,), daemon=True).start()
        worker(remaining)
    else:
        target = _ready.get()

        results = _results
        results[target._id] = target.build({})

        for _ in range(remaining.value - 1):
            dependent = target._dependents[0]
            results[dependent._id] = dependent.build({target.name: results[target._id]})
            target = dependent
