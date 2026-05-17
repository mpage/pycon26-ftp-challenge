"""Parallel build scheduler for the PyCon free-threading challenge."""

from __future__ import annotations

import os
import sys
import sysconfig
import threading
from collections import deque
from heapq import heapify, heappop, heappush

from graph import BuildGraph, Target


_EMPTY_DEP_RESULTS: dict[str, bytes] = {}


class _GraphPlan:
    __slots__ = (
        "remaining_deps",
        "dependents",
        "initial_ready",
        "levels",
        "topological_order",
        "critical_work",
        "max_parallelism",
    )

    def __init__(
        self,
        remaining_deps: dict[str, int],
        dependents: dict[str, list[Target]],
        initial_ready: list[Target],
        levels: list[list[Target]],
        topological_order: list[Target],
        critical_work: dict[str, int],
        max_parallelism: int,
    ) -> None:
        self.remaining_deps = remaining_deps
        self.dependents = dependents
        self.initial_ready = initial_ready
        self.levels = levels
        self.topological_order = topological_order
        self.critical_work = critical_work
        self.max_parallelism = max_parallelism


def _analyze_graph(graph: BuildGraph) -> _GraphPlan:
    """Precompute dependency state and the widest runnable frontier."""
    remaining_deps = {name: len(target.deps) for name, target in graph.targets.items()}
    dependents: dict[str, list[Target]] = {name: [] for name in graph.targets}
    for target in graph.targets.values():
        for dep in target.deps:
            dependents[dep.name].append(target)

    initial_ready = [
        graph.targets[name] for name, count in remaining_deps.items() if count == 0
    ]
    ready = deque(initial_ready)
    topo_remaining = dict(remaining_deps)
    topological_order: list[Target] = []
    levels: list[list[Target]] = []
    max_parallelism = len(ready)

    while ready:
        level = list(ready)
        ready.clear()
        levels.append(level)
        if len(level) > max_parallelism:
            max_parallelism = len(level)
        for target in level:
            topological_order.append(target)
            for child in dependents[target.name]:
                topo_remaining[child.name] -= 1
                if topo_remaining[child.name] == 0:
                    ready.append(child)
        if len(ready) > max_parallelism:
            max_parallelism = len(ready)

    return _GraphPlan(
        remaining_deps=remaining_deps,
        dependents=dependents,
        initial_ready=initial_ready,
        levels=levels,
        topological_order=topological_order,
        critical_work={},
        max_parallelism=max_parallelism,
    )


def _fill_critical_work(plan: _GraphPlan) -> None:
    critical_work: dict[str, int] = {}
    for target in reversed(plan.topological_order):
        downstream = 0
        for child in plan.dependents[target.name]:
            child_work = critical_work[child.name]
            if child_work > downstream:
                downstream = child_work
        critical_work[target.name] = target.work + downstream
    plan.critical_work = critical_work


def _dependency_results(
    target: Target, results: dict[str, bytes]
) -> dict[str, bytes]:
    deps = target.deps
    if not deps:
        return _EMPTY_DEP_RESULTS
    return {dep.name: results[dep.name] for dep in deps}


def _simple_chain_plan(graph: BuildGraph) -> tuple[Target, dict[str, Target]] | None:
    root: Target | None = None
    child_of: dict[str, Target] = {}

    for target in graph.targets.values():
        if not target.deps:
            if root is not None:
                return None
            root = target
        elif len(target.deps) > 1:
            return None

        for dep in target.deps:
            if dep.name in child_of:
                return None
            child_of[dep.name] = target

    if root is None:
        return None
    return root, child_of


def _build_simple_chain(root: Target, child_of: dict[str, Target]) -> dict[str, bytes]:
    results: dict[str, bytes] = {}
    one_dep: dict[str, bytes] = {}
    one_dep_clear = one_dep.clear
    child_of_get = child_of.get
    last_result: bytes | None = None
    target: Target | None = root
    while target is not None:
        deps = target.deps
        build = target.build
        name = target.name

        if deps:
            assert last_result is not None
            one_dep_clear()
            one_dep[deps[0].name] = last_result
            dep_results = one_dep
        else:
            dep_results = _EMPTY_DEP_RESULTS

        result = build(dep_results)
        results[name] = result
        last_result = result
        target = child_of_get(name)

    return results


def _gil_is_enabled() -> bool:
    checker = getattr(sys, "_is_gil_enabled", None)
    if checker is not None:
        return bool(checker())
    return sysconfig.get_config_var("Py_GIL_DISABLED") != 1


def _available_threads() -> int:
    env_workers = os.environ.get("MAX_WORKERS")
    if env_workers:
        return max(1, int(env_workers))

    return _process_cpu_threads() * 2


def _process_cpu_threads() -> int:
    process_cpu_count = getattr(os, "process_cpu_count", None)
    if process_cpu_count is not None:
        count = process_cpu_count()
    else:
        count = os.cpu_count()
    return max(1, count or 1)


def _worker_budget(plan: _GraphPlan) -> int:
    workers = min(_available_threads(), plan.max_parallelism, len(plan.topological_order))

    # Diamond-like graphs alternate between moderate fan-out and single-node joins.
    # Extra workers sit idle or contend on the scheduler, so cap them near CPU count.
    if len(plan.levels) > 64 and plan.max_parallelism <= workers * 2:
        workers = min(workers, _process_cpu_threads())

    return workers


def _level_chunk_factor(plan: _GraphPlan) -> int:
    value = os.environ.get("LEVEL_CHUNK_FACTOR")
    if value:
        return max(1, int(value))
    if len(plan.levels) <= 2:
        return 2
    return 4


def _parallel_build(plan: _GraphPlan, max_workers: int) -> dict[str, bytes]:
    results: dict[str, bytes] = {}
    ready = [
        (-plan.critical_work[target.name], -target.work, index, target)
        for index, target in enumerate(plan.initial_ready)
    ]
    heapify(ready)

    condition = threading.Condition()
    errors: list[BaseException] = []
    remaining_targets = len(plan.topological_order)
    next_index = len(ready)
    batch_size = 8 if plan.max_parallelism >= max_workers * 16 else 1

    def worker() -> None:
        nonlocal remaining_targets, next_index

        while True:
            with condition:
                while not ready and remaining_targets and not errors:
                    condition.wait()
                if errors or remaining_targets == 0:
                    return

                batch = []
                while ready and len(batch) < batch_size:
                    _, _, _, target = heappop(ready)
                    batch.append(target)

            try:
                built = [
                    (target, target.build(_dependency_results(target, results)))
                    for target in batch
                ]
            except BaseException as exc:
                with condition:
                    errors.append(exc)
                    condition.notify_all()
                return


            with condition:
                added_ready = 0

                for target, result in built:
                    results[target.name] = result
                    remaining_targets -= 1

                    for child in plan.dependents[target.name]:
                        plan.remaining_deps[child.name] -= 1
                        if plan.remaining_deps[child.name] == 0:
                            heappush(
                                ready,
                                (
                                    -plan.critical_work[child.name],
                                    -child.work,
                                    next_index,
                                    child,
                                ),
                            )
                            next_index += 1
                            added_ready += 1
                if remaining_targets == 0:
                    condition.notify_all()
                elif added_ready:
                    condition.notify(min(added_ready, max_workers))

    workers = [threading.Thread(target=worker) for _ in range(max_workers)]
    for worker_thread in workers:
        worker_thread.start()
    for worker_thread in workers:
        worker_thread.join()

    if errors:
        raise errors[0]
    return results


def _level_build(plan: _GraphPlan, max_workers: int) -> dict[str, bytes]:
    results: dict[str, bytes] = {}
    errors: list[BaseException] = []
    chunk_factor = _level_chunk_factor(plan)

    for level in plan.levels:
        if len(level) == 1:
            target = level[0]
            dep_results = _dependency_results(target, results)
            results[target.name] = target.build(dep_results)
            continue

        index_lock = threading.Lock()
        next_index = 0
        chunk_size = max(1, len(level) // (max_workers * chunk_factor))
        level_results: list[tuple[str, bytes]] = []

        def worker() -> None:
            nonlocal next_index

            while not errors:
                with index_lock:
                    start = next_index
                    if start >= len(level):
                        return
                    next_index = min(len(level), start + chunk_size)
                    stop = next_index

                try:
                    built = []
                    for target in level[start:stop]:
                        dep_results = _dependency_results(target, results)
                        built.append((target.name, target.build(dep_results)))
                    with index_lock:
                        level_results.extend(built)
                except BaseException as exc:
                    with index_lock:
                        errors.append(exc)
                    return

        workers = [
            threading.Thread(target=worker)
            for _ in range(min(max_workers, len(level)))
        ]
        for worker_thread in workers:
            worker_thread.start()
        for worker_thread in workers:
            worker_thread.join()

        if errors:
            raise errors[0]

        results.update(level_results)

    return results


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets while respecting dependency order."""
    if not graph.targets:
        return {}

    chain_plan = _simple_chain_plan(graph)
    if chain_plan is not None:
        chain_root, child_of = chain_plan
        return _build_simple_chain(chain_root, child_of)

    gil_enabled = _gil_is_enabled()
    plan = _analyze_graph(graph)

    # Threads only help on free-threaded Python and when the graph exposes parallel work.
    if gil_enabled or plan.max_parallelism <= 1:
        results: dict[str, bytes] = {}
        for target in plan.topological_order:
            dep_results = _dependency_results(target, results)
            results[target.name] = target.build(dep_results)
        return results

    max_workers = _worker_budget(plan)
    if plan.max_parallelism >= max_workers * 8 and len(plan.levels) <= 16:
        return _level_build(plan, max_workers)
    _fill_critical_work(plan)
    return _parallel_build(plan, max_workers)
