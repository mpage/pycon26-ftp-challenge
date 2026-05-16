"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

from threading import Thread
from graph import BuildGraph
from threading import Semaphore
import ctypes
import sys

NUM_WORKERS = 24


import gc
gc.freeze()
gc.disable()


_REACHED_ZERO = 1

if sys.platform == 'darwin':
    _atomic_dec = ctypes.CDLL('/usr/lib/libSystem.B.dylib').OSAtomicDecrement64Barrier
    _atomic_dec.argtypes = [ctypes.POINTER(ctypes.c_int64)]
    _atomic_dec.restype = ctypes.c_int64
    _REACHED_ZERO = 0

    class AtomicInt:
        __slots__ = ('_val', '_ref')

        def __init__(self, value: int):
            self._val = ctypes.c_int64(value)
            self._ref = ctypes.byref(self._val)

        def dec(self) -> int:
            return _atomic_dec(self._ref)
else:
    _atomic_fetch_sub = ctypes.CDLL('libatomic.so.1').__atomic_fetch_sub_8
    _atomic_fetch_sub.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.c_int64, ctypes.c_int]
    _atomic_fetch_sub.restype = ctypes.c_int64
    _SEQ_CST = 5

    class AtomicInt:
        __slots__ = ('_val', '_ref')

        def __init__(self, value: int):
            self._val = ctypes.c_int64(value)
            self._ref = ctypes.byref(self._val)

        def dec(self) -> int:
            return _atomic_fetch_sub(self._ref, 1, _SEQ_CST)


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
    remaining = AtomicInt(len(targets))

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
        for name in order:
            target = targets[name]
            dep_results = {dep.name: results[dep.name] for dep in target.deps}
            results[name] = target.build(dep_results)
        return results

    # Pre-allocate dep_results dicts and atomic pending counts
    dep_results_for: dict[str, dict[str, bytes]] = immortalize({})
    sorted_dep_names: dict[str, list[str]] = immortalize({})
    pending: dict[str, AtomicInt] = immortalize({})
    for name, target in targets.items():
        if target.deps:
            dep_results_for[name] = immortalize({})
            sorted_dep_names[name] = sorted(dep.name for dep in target.deps)
            pending[name] = AtomicInt(len(target.deps))

    ready = immortalize([])
    sem = immortalize(Semaphore(0))
    for name in targets:
        if in_degree[name] == 0:
            ready.append(name)
            sem.release()

    def build_target() -> None:
        while True:
            sem.acquire()
            try:
                name = ready.pop()
            except IndexError:
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
                if pending[child].dec() == _REACHED_ZERO:
                    ready.append(child)
                    sem.release()

            if remaining.dec() == _REACHED_ZERO:
                for _ in range(NUM_WORKERS - 1):
                    sem.release()
                return

    threads = []
    for i in range(NUM_WORKERS):
        t = Thread(target=build_target)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return results
