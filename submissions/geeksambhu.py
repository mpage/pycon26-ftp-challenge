"""Low-overhead free-threaded scheduler for the PyCon 2026 challenge."""

from __future__ import annotations

import gc
import os
import sys
import threading
from collections import deque
try:
    from contextvars import Context
except ImportError:  # pragma: no cover
    Context = None  # type: ignore[assignment]

from graph import BuildGraph, Target

MAX_WORKERS = 24
_EMPTY_RESULTS: dict[str, bytes] = {}
_THREAD_CONTEXT_KWARGS: dict[str, object] | None = None


class _WorkQueue:
    __slots__ = ("_items", "_cond", "_shutdown")

    def __init__(self) -> None:
        self._items: deque[int] = deque()
        self._cond = threading.Condition()
        self._shutdown = False

    def put_many(self, items: list[int]) -> None:
        if not items:
            return
        with self._cond:
            self._items.extend(items)
            self._cond.notify(len(items))

    def get(self) -> int:
        with self._cond:
            while not self._items and not self._shutdown:
                self._cond.wait()
            if self._items:
                return self._items.popleft()
            return -1

    def shutdown(self) -> None:
        with self._cond:
            self._shutdown = True
            self._cond.notify_all()


def _gil_enabled() -> bool:
    checker = getattr(sys, "_is_gil_enabled", None)
    return bool(checker()) if checker is not None else True


def _thread_kwargs() -> dict[str, object]:
    global _THREAD_CONTEXT_KWARGS
    kwargs = _THREAD_CONTEXT_KWARGS
    if kwargs is not None:
        return kwargs

    if Context is None:
        kwargs = {}
    else:
        try:
            threading.Thread(target=lambda: None, context=Context())
        except TypeError:
            kwargs = {}
        else:
            kwargs = {"context": Context()}

    _THREAD_CONTEXT_KWARGS = kwargs
    return kwargs


def _serial_build(
    targets: list[Target],
    names: list[str],
    dependents: list[list[int] | None],
) -> dict[str, bytes]:
    results: list[bytes | None] = [None] * len(targets)
    tid = 0
    prev_name = ""
    prev_result: bytes | None = None

    while True:
        dep_results = (
            _EMPTY_RESULTS if prev_result is None else {prev_name: prev_result}
        )
        result = targets[tid].build(dep_results)
        results[tid] = result
        succs = dependents[tid]
        if not succs:
            break
        prev_name = names[tid]
        prev_result = result
        tid = succs[0]

    return {names[i]: results[i] for i in range(len(names))}  # type: ignore[dict-item]


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = list(graph.targets.values())
    count = len(targets)
    if count == 0:
        return {}

    names = [target.name for target in targets]
    dep_info: list[tuple[tuple[str, int], ...]] = [()] * count
    dependents: list[list[int] | None] = [None] * count
    remaining = [0] * count
    roots: list[int] = []
    has_parallelism = False

    for tid, target in enumerate(targets):
        target._sched_id = tid

    for tid, target in enumerate(targets):
        deps = target.deps
        if deps:
            info = tuple((dep.name, dep._sched_id) for dep in deps)
            dep_info[tid] = info
            remaining[tid] = len(info)
            for dep in deps:
                dep_id = dep._sched_id
                succs = dependents[dep_id]
                if succs is None:
                    dependents[dep_id] = [tid]
                else:
                    has_parallelism = True
                    succs.append(tid)
        else:
            if roots:
                has_parallelism = True
            roots.append(tid)

    if _gil_enabled() or not has_parallelism:
        return _serial_build(targets, names, dependents)

    topo = list(roots)
    topo_remaining = remaining[:]
    head = 0
    while head < len(topo):
        tid = topo[head]
        head += 1
        succs = dependents[tid]
        if succs is not None:
            for succ_id in succs:
                left = topo_remaining[succ_id] - 1
                topo_remaining[succ_id] = left
                if left == 0:
                    topo.append(succ_id)

    priority = [0] * count
    for tid in reversed(topo):
        best_child = 0
        succs = dependents[tid]
        if succs is not None:
            for succ_id in succs:
                child_priority = priority[succ_id]
                if child_priority > best_child:
                    best_child = child_priority
        priority[tid] = targets[tid].work + best_child

    roots.sort(key=priority.__getitem__, reverse=True)

    results: list[bytes | None] = [None] * count
    queue = _WorkQueue()
    lock = threading.Lock()
    pending = count

    def dep_results_for(tid: int) -> dict[str, bytes]:
        deps = dep_info[tid]
        if not deps:
            return _EMPTY_RESULTS
        if len(deps) == 1:
            name, dep_id = deps[0]
            return {name: results[dep_id]}  # type: ignore[dict-item]
        return {name: results[dep_id] for name, dep_id in deps}  # type: ignore[dict-item]

    def run(start_tid: int) -> None:
        nonlocal pending
        tid = start_tid

        while tid >= 0:
            target = targets[tid]
            result = target.build(dep_results_for(tid))

            next_tid = -1
            extra_ready: list[int] | None = None

            with lock:
                results[tid] = result
                pending -= 1
                finished = pending == 0

                succs = dependents[tid]
                if succs is not None:
                    for succ_id in succs:
                        left = remaining[succ_id] - 1
                        remaining[succ_id] = left
                        if left == 0:
                            if next_tid < 0:
                                next_tid = succ_id
                            elif priority[succ_id] > priority[next_tid]:
                                if extra_ready is None:
                                    extra_ready = [next_tid]
                                else:
                                    extra_ready.append(next_tid)
                                next_tid = succ_id
                            elif extra_ready is None:
                                extra_ready = [succ_id]
                            else:
                                extra_ready.append(succ_id)

            if finished:
                queue.shutdown()
                return

            if extra_ready is not None:
                queue.put_many(extra_ready)

            tid = next_tid

    def worker() -> None:
        while True:
            tid = queue.get()
            if tid < 0:
                return
            run(tid)

    workers = min(MAX_WORKERS, count)

    was_enabled = gc.isenabled()
    if was_enabled:
        gc.disable()
    try:
        queue.put_many(roots)
        thread_kwargs = _thread_kwargs()
        threads = [
            threading.Thread(target=worker, **thread_kwargs)
            for _ in range(workers - 1)
        ]
        for thread in threads:
            thread.start()
        worker()
        for thread in threads:
            thread.join()
    finally:
        if was_enabled:
            gc.enable()

    return {names[i]: results[i] for i in range(count)}  # type: ignore[dict-item]
