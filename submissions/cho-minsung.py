"""Free-threaded build scheduler for Python 3.14t.

Topologically dispatches a build graph across a custom worker pool. Each
ready target (zero unmet deps) lands on a shared deque guarded by a
Condition; workers pop, build, then under one data lock decrement
dependents and append the freshly-ready ones back. Skipping
concurrent.futures avoids a Future allocation per target plus the
executor's two-queue hop, both of which dominate when targets are small
and the graph is wide.

Two locks instead of one: data_lock guards results_arr + in_degree +
the completed counter; cond (with its own lock) guards ready_q + the
shutdown flag. The hot decrement loop under data_lock no longer blocks
workers that are sleeping on cond.wait(), and the cond critical section
shrinks to "extend deque + notify" only when there is something to push.

Continuation: when a finished task uncovers k new ready targets, the
worker keeps one for itself (next_i) and pushes only k-1 to the deque.
This saves a deque hop and keeps cache-hot data in the same thread.
"""

from __future__ import annotations

import os
import threading
from collections import deque

from graph import BuildGraph


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    n = len(targets)
    if n == 0:
        return {}

    # Integer-index every target. Hot path uses ints, not strings.
    names = list(targets.keys())
    target_objs = [targets[name] for name in names]
    idx = {name: i for i, name in enumerate(names)}

    deps_idx: list[list[int]] = [
        [idx[d.name] for d in target_objs[i].deps] for i in range(n)
    ]
    dependents_idx: list[list[int]] = [[] for _ in range(n)]
    for i, deps in enumerate(deps_idx):
        for d in deps:
            dependents_idx[d].append(i)

    in_degree = [len(d) for d in deps_idx]
    results_arr: list[bytes | None] = [None] * n

    workers = max(2, os.cpu_count() or 4)

    data_lock = threading.Lock()
    cond = threading.Condition()
    ready_q: deque[int] = deque()
    completed = 0
    shutdown = False

    # Seed targets with zero structural deps. Snapshot before any worker
    # runs: checking live in_degree here would race with workers
    # decrementing it to 0, causing duplicate submissions.
    for i in range(n):
        if not deps_idx[i]:
            ready_q.append(i)

    def worker() -> None:
        nonlocal completed, shutdown
        while True:
            with cond:
                while not ready_q and not shutdown:
                    cond.wait()
                if shutdown and not ready_q:
                    return
                i: int | None = ready_q.popleft()
            # Continuation loop: keep running tail-ready work in this
            # thread instead of bouncing through the deque.
            while i is not None:
                t = target_objs[i]
                # Reading results_arr[j] without a lock is safe: this
                # target only ran because all its deps' decrements landed
                # under data_lock after each dep wrote its result. The
                # cond.acquire/release between submit and pop establishes
                # happens-before for queued work; the tail-call path
                # stays in one thread and needs no synchronization.
                dep_results = {
                    target_objs[j].name: results_arr[j]  # type: ignore[misc]
                    for j in deps_idx[i]
                }
                out = t.build(dep_results)
                next_i: int | None = None
                pushed: list[int] = []
                is_done = False
                with data_lock:
                    results_arr[i] = out
                    for j in dependents_idx[i]:
                        in_degree[j] -= 1
                        if in_degree[j] == 0:
                            if next_i is None:
                                next_i = j
                            else:
                                pushed.append(j)
                    completed += 1
                    if completed == n:
                        is_done = True
                if pushed or is_done:
                    with cond:
                        if pushed:
                            ready_q.extend(pushed)
                            cond.notify(len(pushed))
                        if is_done:
                            shutdown = True
                            cond.notify_all()
                i = next_i

    threads = [
        threading.Thread(target=worker, name=f"build-{k}") for k in range(workers)
    ]
    for th in threads:
        th.start()
    # Seed queue was filled before threads started; the first cond.wait()
    # call inside a worker re-checks the deque after acquiring the lock,
    # so no separate initial notify is needed.
    for th in threads:
        th.join()

    return {names[i]: results_arr[i] for i in range(n)}  # type: ignore[misc]
