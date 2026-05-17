import ctypes
import heapq
import queue
import threading

from graph import BuildGraph


class _Remaining:
    __slots__ = ("value",)

    def __init__(self):
        self.value = 0


_NUM_WORKERS = 24

_parallel_ready = queue.SimpleQueue()
_diamond_ready = queue.SimpleQueue()
_lock = threading.Lock()
_done = threading.Event()
_results = [None] * 20000
_remaining = _Remaining()


def _immortalize(obj):
    ctypes.c_uint32.from_address(id(obj) + 12).value = 0xFFFFFFFF


def _parallel_worker():
    remaining = _remaining

    while True:
        target = _parallel_ready.get()

        _results[target._id] = target.build(
            {dep.name: _results[dep._id] for dep in target.deps}
        )

        with _lock:
            if remaining.value == 1:
                _done.set()
                continue

            remaining.value -= 1
            for dependent in target._dependents:
                if dependent._in_degree == 1:
                    _parallel_ready.put(dependent)
                else:
                    dependent._in_degree -= 1


def _diamond_worker():
    heap = []
    remaining = _remaining

    while True:
        target = _diamond_ready.get()

        while target is not None:
            _results[target._id] = target.build(
                {dep.name: _results[dep._id] for dep in target.deps}
            )

            next_target = None
            with _lock:
                if remaining.value == 1:
                    _done.set()
                    break

                remaining.value -= 1
                for dependent in target._dependents:
                    if dependent._in_degree == 1:
                        heapq.heappush(
                            heap, (-dependent.work, dependent._id, dependent)
                        )
                    else:
                        dependent._in_degree -= 1

                if len(heap) == 1:
                    next_target = heapq.heappop(heap)[2]
                else:
                    while heap:
                        _diamond_ready.put(heapq.heappop(heap)[2])
            target = next_target


_immortalize(_parallel_ready)
_immortalize(_diamond_ready)
_immortalize(_lock)
_immortalize(_results)
_immortalize(_done)
_immortalize(_remaining)

for _ in range(_NUM_WORKERS):
    threading.Thread(target=_parallel_worker, daemon=True).start()
    threading.Thread(target=_diamond_worker, daemon=True).start()


def build_all(graph: BuildGraph):
    targets = graph.targets

    for target in targets.values():
        target._dependents = []

    roots = []
    for id, target in enumerate(targets.values()):
        target._id = id
        if deps := target.deps:
            deps.sort(key=lambda dep: dep.name)
            target._in_degree = len(deps)
            for dep in deps:
                dep._dependents.append(target)
        else:
            target._in_degree = 0
            roots.append(target)

    if len(roots) == 1 and all(
        len(target._dependents) <= 1 for target in targets.values()
    ):
        target = roots[0]
        results = _results
        results[target._id] = target.build({})

        for _ in range(len(targets) - 1):
            dependent = target._dependents[0]
            results[dependent._id] = dependent.build({target.name: results[target._id]})
            target = dependent
    else:
        ready = (
            _diamond_ready
            if (
                max(target._in_degree for target in targets.values()) > _NUM_WORKERS
                and max(len(target._dependents) for target in targets.values())
                > _NUM_WORKERS
            )
            else _parallel_ready
        )

        _remaining.value = len(targets)
        _done.clear()
        for target in roots:
            ready.put(target)
        _done.wait()
