"""Parallel DAG scheduler submission for the build graph challenge."""

from __future__ import annotations

import heapq
import os
import threading

from graph import BuildGraph, Target


_EMPTY_DEP_RESULTS: dict[str, bytes] = {}
_MAX_READY_BATCH = 16
_WIDE_FRONTIER = 512


def _worker_count(total_targets: int, max_frontier: int) -> int:
    override = os.environ.get("BUILD_WORKERS")
    if override:
        return max(1, min(total_targets, int(override)))

    cpu_count_fn = getattr(os, "process_cpu_count", os.cpu_count)
    cpus = cpu_count_fn() or 1
    if max_frontier >= _WIDE_FRONTIER:
        cpus = max(cpus * 2, 48)
    return max(1, min(total_targets, cpus))


def _prepare_graph(
    graph: BuildGraph,
) -> tuple[list[Target], dict[str, list[Target]], dict[str, int], int]:
    targets = list(graph.targets.values())
    in_degree = {target.name: len(target.deps) for target in targets}
    dependents = {target.name: [] for target in targets}

    for target in targets:
        for dep in target.deps:
            dependents[dep.name].append(target)

    pending = in_degree.copy()
    ready = [target for target in targets if pending[target.name] == 0]
    topo_order: list[Target] = []
    head = 0
    max_frontier = len(ready)

    while head < len(ready):
        target = ready[head]
        head += 1
        topo_order.append(target)

        for dependent in dependents[target.name]:
            remaining = pending[dependent.name] - 1
            pending[dependent.name] = remaining
            if remaining == 0:
                ready.append(dependent)

        frontier = len(ready) - head
        if frontier > max_frontier:
            max_frontier = frontier

    if len(topo_order) != len(targets):
        raise ValueError("Build graph contains a cycle")

    return topo_order, dependents, in_degree, max_frontier


def _critical_paths(
    topo_order: list[Target],
    dependents: dict[str, list[Target]],
) -> dict[str, int]:
    critical_path = {}
    for target in reversed(topo_order):
        longest_child_path = 0
        for dependent in dependents[target.name]:
            child_path = critical_path[dependent.name]
            if child_path > longest_child_path:
                longest_child_path = child_path
        critical_path[target.name] = target.work + longest_child_path
    return critical_path


def _build_serial(topo_order: list[Target]) -> dict[str, bytes]:
    results: dict[str, bytes] = {}
    for target in topo_order:
        dep_results = (
            {dep.name: results[dep.name] for dep in target.deps}
            if target.deps
            else _EMPTY_DEP_RESULTS
        )
        results[target.name] = target.build(dep_results)
    return results


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in dependency order using a priority worker pool."""
    total_targets = len(graph)
    if total_targets == 0:
        return {}

    topo_order, dependents, in_degree, max_frontier = _prepare_graph(graph)

    worker_count = _worker_count(total_targets, max_frontier)
    if worker_count == 1 or max_frontier <= 1:
        return _build_serial(topo_order)

    critical_path = _critical_paths(topo_order, dependents)
    results: dict[str, bytes] = {}
    ready_heap: list[tuple[int, int, int, Target]] = []
    batch_publishing = max_frontier >= _WIDE_FRONTIER
    condition = threading.Condition()
    completed = 0
    next_sequence = 0
    first_error: BaseException | None = None

    def push_ready(target: Target) -> None:
        nonlocal next_sequence
        heapq.heappush(
            ready_heap,
            (
                -critical_path[target.name],
                -len(dependents[target.name]),
                next_sequence,
                target,
            ),
        )
        next_sequence += 1

    for target in topo_order:
        if in_degree[target.name] == 0:
            push_ready(target)

    def worker() -> None:
        nonlocal completed, first_error

        while True:
            work_batch: list[tuple[Target, dict[str, bytes]]] = []
            with condition:
                while (
                    not ready_heap and completed < total_targets and first_error is None
                ):
                    condition.wait()

                if first_error is not None or completed >= total_targets:
                    return

                available = len(ready_heap)
                batch_size = 1
                if max_frontier >= _WIDE_FRONTIER and available > worker_count:
                    batch_size = min(_MAX_READY_BATCH, available // worker_count)

                for _ in range(batch_size):
                    if not ready_heap:
                        break
                    _, _, _, target = heapq.heappop(ready_heap)
                    dep_results = (
                        {dep.name: results[dep.name] for dep in target.deps}
                        if target.deps
                        else _EMPTY_DEP_RESULTS
                    )
                    work_batch.append((target, dep_results))

            built_batch: list[tuple[Target, bytes]] = []
            for target, dep_results in work_batch:
                try:
                    result = target.build(dep_results)
                except BaseException as exc:
                    with condition:
                        if first_error is None:
                            first_error = exc
                        condition.notify_all()
                    return

                if batch_publishing:
                    built_batch.append((target, result))
                    continue

                with condition:
                    if first_error is not None:
                        return

                    results[target.name] = result
                    completed += 1
                    new_ready = 0

                    for dependent in dependents[target.name]:
                        remaining = in_degree[dependent.name] - 1
                        in_degree[dependent.name] = remaining
                        if remaining == 0:
                            push_ready(dependent)
                            new_ready += 1

                    if completed >= total_targets:
                        condition.notify_all()
                    elif new_ready:
                        condition.notify(new_ready)

            if not batch_publishing:
                continue

            with condition:
                if first_error is not None:
                    return

                new_ready = 0
                for target, result in built_batch:
                    results[target.name] = result
                    completed += 1

                    for dependent in dependents[target.name]:
                        remaining = in_degree[dependent.name] - 1
                        in_degree[dependent.name] = remaining
                        if remaining == 0:
                            push_ready(dependent)
                            new_ready += 1

                if completed >= total_targets:
                    condition.notify_all()
                elif new_ready:
                    condition.notify(new_ready)

    threads = [
        threading.Thread(target=worker, name=f"build-worker-{idx}")
        for idx in range(worker_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if first_error is not None:
        raise first_error

    return results
