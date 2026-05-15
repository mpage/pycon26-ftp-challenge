"""Free-threaded build scheduler for Python 3.14t.

The hot path uses integer target ids, a SimpleQueue worker pool, and
precomputed dependency lookups. When a build makes dependents ready, the
worker keeps one and queues the rest, saving one queue round trip on common
chain-like stretches of the graph.

Pure chains run inline because there is no parallel work to schedule.
"""

from __future__ import annotations

import _thread
import queue
import threading

from graph import BuildGraph

NUM_WORKERS = 24


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    n = len(targets)
    if n == 0:
        return {}

    target_list = list(targets.values())
    names = [target.name for target in target_list]
    in_degree = [0] * n
    dependents: list[list[int] | None] = [None] * n
    dep_info: list[tuple[tuple[str, int], ...]] = [()] * n
    ready = queue.SimpleQueue()
    has_parallelism = False

    for i, target in enumerate(target_list):
        target._id = i

    for i, target in enumerate(target_list):
        if target.deps:
            deps = tuple((dep.name, dep._id) for dep in target.deps)
            dep_info[i] = deps
            in_degree[i] = len(deps)
            for dep in target.deps:
                dep_id = dep._id
                succs = dependents[dep_id]
                if succs is None:
                    dependents[dep_id] = [i]
                else:
                    has_parallelism = True
                    succs.append(i)
        else:
            if not ready.empty():
                has_parallelism = True
            ready.put(i)

    results: list[bytes | None] = [None] * n

    if not has_parallelism:
        tid = ready.get()
        dep_result: bytes | None = None
        dep_name = ""
        while True:
            dep_results = {dep_name: dep_result} if dep_result is not None else {}
            result = target_list[tid].build(dep_results)
            results[tid] = result
            succs = dependents[tid]
            if not succs:
                break
            dep_name, _ = dep_info[succs[0]][0]
            dep_result = result
            tid = succs[0]
        return {names[i]: results[i] for i in range(n)}  # type: ignore[misc]

    remaining = n
    lock = threading.Lock()
    done = threading.Event()

    def worker() -> None:
        nonlocal remaining
        tid = ready.get()
        while tid != -1:
            results[tid] = target_list[tid].build(
                {name: results[idx] for name, idx in dep_info[tid]}  # type: ignore[misc]
            )

            next_tid = -1
            with lock:
                remaining -= 1
                if remaining == 0:
                    for _ in range(NUM_WORKERS - 1):
                        ready.put(-1)
                    done.set()
                    return
                succs = dependents[tid]
                if succs:
                    for succ_id in succs:
                        in_degree[succ_id] -= 1
                        if in_degree[succ_id] == 0:
                            if next_tid == -1:
                                next_tid = succ_id
                            else:
                                ready.put(succ_id)
            tid = next_tid if next_tid != -1 else ready.get()

    for _ in range(NUM_WORKERS):
        _thread.start_joinable_thread(worker, daemon=True)
    done.wait()

    return {names[i]: results[i] for i in range(n)}  # type: ignore[misc]
