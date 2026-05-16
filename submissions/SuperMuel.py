from __future__ import annotations

import threading
from queue import SimpleQueue

from graph import BuildGraph, Target


NUM_WORKERS = 24
_SENTINEL = object()


class _Run:
    __slots__ = (
        "targets",
        "dependents",
        "remaining_deps",
        "results",
        "remaining",
        "ready",
        "lock",
        "done",
    )

    def __init__(
        self,
        targets: tuple[Target, ...],
        dependents: list[list[int]],
        remaining_deps: list[int],
        results: list[bytes | None],
    ) -> None:
        self.targets = targets
        self.dependents = dependents
        self.remaining_deps = remaining_deps
        self.results = results
        self.remaining = len(targets)
        self.ready: SimpleQueue[int | object] = SimpleQueue()
        self.lock = threading.Lock()
        self.done = threading.Event()


_BUILD_LOCK = threading.Lock()
_POOL_COND = threading.Condition()
_ACTIVE_RUN: _Run | None = None
_RUN_TOKEN = 0


def _pool_worker() -> None:
    seen_token = 0

    while True:
        with _POOL_COND:
            while seen_token == _RUN_TOKEN:
                _POOL_COND.wait()
            seen_token = _RUN_TOKEN
            run = _ACTIVE_RUN

        if run is None:
            return

        targets = run.targets
        dependents = run.dependents
        remaining_deps = run.remaining_deps
        results = run.results
        ready = run.ready
        lock = run.lock

        work_idx = ready.get()
        while work_idx is not _SENTINEL:
            while True:
                target = targets[work_idx]
                deps = target.deps
                if not deps:
                    dep_results = {}
                elif len(deps) == 1:
                    dep = deps[0]
                    dep_results = {dep.name: results[dep._idx]}
                else:
                    dep_results = {dep.name: results[dep._idx] for dep in deps}
                results[work_idx] = target.build(dep_results)

                next_idx = -1
                with lock:
                    for child_idx in dependents[work_idx]:
                        left = remaining_deps[child_idx] - 1
                        remaining_deps[child_idx] = left
                        if left == 0:
                            if next_idx < 0:
                                next_idx = child_idx
                            else:
                                ready.put(child_idx)

                    run.remaining -= 1
                    if run.remaining == 0:
                        for _ in range(NUM_WORKERS):
                            ready.put(_SENTINEL)
                        run.done.set()

                if next_idx < 0:
                    break
                work_idx = next_idx

            work_idx = ready.get()


_WORKERS = tuple(
    threading.Thread(target=_pool_worker, daemon=True) for _ in range(NUM_WORKERS)
)
for _thread in _WORKERS:
    _thread.start()


def _prepare(
    graph: BuildGraph,
) -> tuple[tuple[Target, ...], list[list[int]], list[int], list[int], int]:
    targets = tuple(graph.targets.values())
    dependents: list[list[int]] = [[] for _ in targets]
    remaining_deps = [0] * len(targets)
    roots: list[int] = []

    for idx, target in enumerate(targets):
        target._idx = idx

    max_fan_out = 0
    for idx, target in enumerate(targets):
        deps = target.deps
        dep_count = len(deps)
        remaining_deps[idx] = dep_count
        if dep_count == 0:
            roots.append(idx)
        for dep in deps:
            dependents[dep._idx].append(idx)

    for child_indexes in dependents:
        child_count = len(child_indexes)
        if child_count > max_fan_out:
            max_fan_out = child_count
        if child_count > 1:
            child_indexes.sort(key=lambda child_idx: targets[child_idx].work, reverse=True)

    return targets, dependents, remaining_deps, roots, max_fan_out


def _build_chain(
    targets: tuple[Target, ...],
    dependents: list[list[int]],
    remaining_deps: list[int],
    roots: list[int],
    results: list[bytes | None],
) -> list[bytes | None]:
    work_idx = roots[0] if roots else -1
    previous_name = ""
    previous_result = None

    while work_idx >= 0:
        target = targets[work_idx]
        if target.deps:
            result = target.build({previous_name: previous_result})
        else:
            result = target.build({})
        results[work_idx] = result

        next_idx = -1
        for child_idx in dependents[work_idx]:
            remaining_deps[child_idx] -= 1
            if remaining_deps[child_idx] == 0:
                next_idx = child_idx

        previous_name = target.name
        previous_result = result
        work_idx = next_idx

    return results


def _build_spawned(
    targets: tuple[Target, ...],
    dependents: list[list[int]],
    remaining_deps: list[int],
    roots: list[int],
    results: list[bytes | None],
) -> list[bytes | None]:
    ready: SimpleQueue[int | object] = SimpleQueue()
    for root_idx in roots:
        ready.put(root_idx)

    remaining = len(targets)
    lock = threading.Lock()

    def worker() -> None:
        nonlocal remaining
        local_ready = ready
        local_results = results
        local_dependents = dependents
        local_remaining_deps = remaining_deps
        local_lock = lock
        local_targets = targets

        work_idx = local_ready.get()
        while work_idx is not _SENTINEL:
            while True:
                target = local_targets[work_idx]
                deps = target.deps
                if not deps:
                    dep_results = {}
                elif len(deps) == 1:
                    dep = deps[0]
                    dep_results = {dep.name: local_results[dep._idx]}
                else:
                    dep_results = {dep.name: local_results[dep._idx] for dep in deps}
                local_results[work_idx] = target.build(dep_results)

                next_idx = -1
                with local_lock:
                    for child_idx in local_dependents[work_idx]:
                        left = local_remaining_deps[child_idx] - 1
                        local_remaining_deps[child_idx] = left
                        if left == 0:
                            if next_idx < 0:
                                next_idx = child_idx
                            else:
                                local_ready.put(child_idx)
                    remaining -= 1
                    if remaining == 0:
                        for _ in range(NUM_WORKERS):
                            local_ready.put(_SENTINEL)

                if next_idx < 0:
                    break
                work_idx = next_idx

            work_idx = local_ready.get()

    for _ in range(NUM_WORKERS - 1):
        threading.Thread(target=worker, daemon=True).start()

    worker()
    return results


def _build_pool(
    targets: tuple[Target, ...],
    dependents: list[list[int]],
    remaining_deps: list[int],
    roots: list[int],
    results: list[bytes | None],
) -> list[bytes | None]:
    run = _Run(targets, dependents, remaining_deps, results)
    for root_idx in roots:
        run.ready.put(root_idx)

    with _BUILD_LOCK:
        global _ACTIVE_RUN, _RUN_TOKEN
        with _POOL_COND:
            _ACTIVE_RUN = run
            _RUN_TOKEN += 1
            _POOL_COND.notify_all()
        run.done.wait()

    return results


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets, dependents, remaining_deps, roots, max_fan_out = _prepare(graph)
    if not targets:
        return {}

    results: list[bytes | None] = [None] * len(targets)
    if len(roots) <= 1 and max_fan_out <= 1:
        return _build_chain(targets, dependents, remaining_deps, roots, results)
    if len(roots) > 64:
        return _build_pool(targets, dependents, remaining_deps, roots, results)
    return _build_spawned(targets, dependents, remaining_deps, roots, results)
