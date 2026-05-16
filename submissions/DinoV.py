"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

from threading import Thread
from graph import BuildGraph, Target
from threading import Semaphore
import ctypes
import sys

NUM_WORKERS = 24


import gc
gc.freeze()
gc.disable()


_REACHED_ZERO = 1

if sys.platform == 'darwin':
    _libSystem = ctypes.CDLL('/usr/lib/libSystem.B.dylib')

    _atomic_dec = _libSystem.OSAtomicDecrement64Barrier
    _atomic_dec.argtypes = [ctypes.POINTER(ctypes.c_int64)]
    _atomic_dec.restype = ctypes.c_int64
    _REACHED_ZERO = 0

    _os_atomic_add = _libSystem.OSAtomicAdd64Barrier
    _os_atomic_add.argtypes = [ctypes.c_int64, ctypes.POINTER(ctypes.c_int64)]
    _os_atomic_add.restype = ctypes.c_int64

    class AtomicInt:
        __slots__ = ('_val', '_ref')

        def __init__(self, value: int, order: int = 0):
            self._val = ctypes.c_int64(value)
            self._ref = ctypes.byref(self._val)

        def dec(self) -> int:
            return _atomic_dec(self._ref)

    class LockFreeQueue:
        __slots__ = ('_data', '_flags', '_head', '_head_ref', '_tail', '_tail_ref')

        def __init__(self, capacity):
            self._data = [None] * capacity
            self._flags = (ctypes.c_int64 * capacity)()
            self._head = ctypes.c_int64(0)
            self._tail = ctypes.c_int64(0)
            self._head_ref = ctypes.byref(self._head)
            self._tail_ref = ctypes.byref(self._tail)

        def push(self, item):
            idx = _os_atomic_add(1, self._tail_ref) - 1
            self._data[idx] = item
            self._flags[idx] = 1

        def pop(self):
            idx = _os_atomic_add(1, self._head_ref) - 1
            while self._flags[idx] == 0:
                pass
            return self._data[idx]

else:
    _libatomic = ctypes.CDLL('libatomic.so.1')

    _atomic_fetch_sub = _libatomic.__atomic_fetch_sub_8
    _atomic_fetch_sub.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.c_int64, ctypes.c_int]
    _atomic_fetch_sub.restype = ctypes.c_int64

    _atomic_fetch_add = _libatomic.__atomic_fetch_add_8
    _atomic_fetch_add.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.c_int64, ctypes.c_int]
    _atomic_fetch_add.restype = ctypes.c_int64

    _RELAXED = 0
    _ACQ_REL = 4

    class AtomicInt:
        __slots__ = ('_val', '_ref', '_order')

        def __init__(self, value: int, order: int = _ACQ_REL):
            self._val = ctypes.c_int64(value)
            self._ref = ctypes.byref(self._val)
            self._order = order

        def dec(self) -> int:
            return _atomic_fetch_sub(self._ref, 1, self._order)

    class LockFreeQueue:
        __slots__ = ('_data', '_flags', '_head', '_head_ref', '_tail', '_tail_ref')

        def __init__(self, capacity):
            self._data = immortalize([None] * capacity)
            self._flags = immortalize((ctypes.c_int64 * capacity)())
            self._head = immortalize(ctypes.c_int64(0))
            self._tail = immortalize(ctypes.c_int64(0))
            self._head_ref = immortalize(ctypes.byref(self._head))
            self._tail_ref = immortalize(ctypes.byref(self._tail))

        def push(self, item):
            idx = _atomic_fetch_add(self._tail_ref, 1, _RELAXED)
            self._data[idx] = item
            self._flags[idx] = 1

        def pop(self):
            idx = _atomic_fetch_add(self._head_ref, 1, _RELAXED)
            while self._flags[idx] == 0:
                pass
            return self._data[idx]


def immortalize[T](obj: T) -> T:
    """Make a Python object immortal on 3.14 free-threaded builds."""
    # ob_refcnt is the first field in PyObject, at offset 0
    # On free-threaded builds, ob_refcnt_split[0] (lower 32 bits) must be UINT32_MAX
    addr = id(obj) + 12
    ctypes.c_uint32.from_address(addr).value = 0xFFFFFFFF
    assert sys._is_immortal(obj)
    return obj


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    targets = graph.targets
    if not targets:
        return {}

    dependents: dict[str, list[str]] = immortalize({name: [] for name in targets})
    in_degree: dict[str, int] = immortalize({name: 0 for name in targets})

    for name, target in targets.items():
        in_degree[name] = len(target.deps)
        immortalize(name)
        immortalize(target)
        for dep in target.deps:
            immortalize(dep)
            dependents[dep.name].append(name)

    results: dict[str, bytes] = immortalize({})
    remaining = immortalize(AtomicInt(len(targets), 0))

    is_chain = all(len(t.deps) <= 1 for t in targets.values())
    if is_chain:
        order = []
        for name in targets:
            if in_degree[name] == 0:
                order.append(name)
        while len(order) < len(targets):
            name = order[-1]
            for child in dependents[name]:
                if in_degree[child] == 1:
                    order.append(child)
                    break
        dep_results = {}
        for name in order:
            results[name] = targets[name].build(dep_results)
            dep_results.clear()
            dep_results[name] = results[name]
        return results

    # Pre-allocate dep_results dicts and atomic pending counts
    dep_results_for: dict[str, dict[str, bytes]] = immortalize({})
    sorted_dep_names: dict[str, list[str]] = immortalize({})
    pending: dict[str, AtomicInt] = immortalize({})
    for name, target in targets.items():
        if target.deps:
            dep_results_for[name] = immortalize({})
            sorted_dep_names[name] = immortalize(sorted(dep.name for dep in target.deps))
            pending[name] = immortalize(AtomicInt(len(target.deps)))

    ready = immortalize(LockFreeQueue(len(targets) + NUM_WORKERS))
    sem = immortalize(Semaphore(0))
    for name in targets:
        if in_degree[name] == 0:
            ready.push(name)
            sem.release()

    def build_target(target, results) -> None:
        while True:
            sem.acquire()
            name = ready.pop()
            if name is None:
                return
            target = targets[name]
            if name in sorted_dep_names:
                accum = dep_results_for[name]
                dep_results = {dn: accum[dn] for dn in sorted_dep_names[name]}
            else:
                dep_results = {}
            result = target.build(dep_results)
            results[name] = result

            for child in dependents[name]:
                dep_results_for[child][name] = result
                if pending[child].dec() is _REACHED_ZERO:
                    ready.push(child)
                    sem.release()

            if remaining.dec() is _REACHED_ZERO:
                for _ in range(NUM_WORKERS - 1):
                    ready.push(None)
                    sem.release()
                return

    threads = []
    for i in range(NUM_WORKERS):
        t = Thread(target=build_target, args=(target,results))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return results
