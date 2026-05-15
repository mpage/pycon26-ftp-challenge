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

Linear-chain fast path: pure-chain graphs (every node has at most one
dep and at most one dependent, single root) skip thread setup entirely;
iterating in one thread avoids lock + cond + dict overhead the threaded
path otherwise pays for zero-parallelism input.

Tried-and-rejected (kept here for the next maintainer): critical-path
priority queue via heapq replaced the FIFO; on uniform-work benchmark
graphs the priority gives no benefit, but the per-op O(log n) heap cost
under cond produced a ~10x regression (2.92x -> 0.35x). Work-stealing
per-worker deques replaced the single shared deque; at 8 cores the
extra per-steal lock acquisitions and random shuffle overhead beat the
contention savings (2.92x -> 2.58x). Both might shine on 24+ cores;
neither was worth shipping without that bench.
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

    initial_ready = [i for i, deps in enumerate(deps_idx) if not deps]

    # Linear-chain fast path: every node has <= 1 dep and <= 1 dependent,
    # and exactly one root. Threading buys nothing here; run inline.
    if (
        len(initial_ready) == 1
        and all(len(deps) <= 1 for deps in deps_idx)
        and all(len(deps) <= 1 for deps in dependents_idx)
    ):
        i = initial_ready[0]
        dep_result: bytes | None = None
        dep_name = ""
        while True:
            t = target_objs[i]
            dep_results = {dep_name: dep_result} if dep_result is not None else {}
            out = t.build(dep_results)
            results_arr[i] = out
            if not dependents_idx[i]:
                break
            dep_name = t.name
            dep_result = out
            i = dependents_idx[i][0]
        return {names[i]: results_arr[i] for i in range(n)}  # type: ignore[misc]

    # Mild oversubscription: 2x cpu_count, clamped to [16, 32]. Above
    # one-thread-per-core lets the scheduler keep cores busy whenever a
    # worker briefly sits in the cond/data lock acquire path; the [16,
    # 32] clamp protects against tiny machines (forces enough workers
    # to amortize startup) and against giant 96-core boxes (where 192
    # threads would just thrash the cond.notify wake-up). Sweep on
    # local 8-core M showed 2.92 -> 2.96; eval-machine 24c -> 48 threads
    # is well within the clamp.
    cpu_count = os.cpu_count() or 4
    workers = max(16, min(32, cpu_count * 2))

    data_lock = threading.Lock()
    cond = threading.Condition()
    ready_q: deque[int] = deque(initial_ready)
    completed = 0
    shutdown = False

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
                # target only ran because all its deps' decrements
                # landed under data_lock after each dep wrote its
                # result. The cond.acquire/release between push and pop
                # establishes happens-before for queued work; the tail-
                # call path stays in one thread and needs no extra sync.
                dep_results = {
                    target_objs[j].name: results_arr[j]  # type: ignore[misc]
                    for j in deps_idx[i]
                }
                out = t.build(dep_results)
                next_i: int | None = None
                pushed: list[int] | None = None
                is_done = False
                results_arr[i] = out
                with data_lock:
                    for j in dependents_idx[i]:
                        in_degree[j] -= 1
                        if in_degree[j] == 0:
                            if next_i is None:
                                next_i = j
                            else:
                                if pushed is None:
                                    pushed = []
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
    for th in threads:
        th.join()

    return {names[i]: results_arr[i] for i in range(n)}  # type: ignore[misc]
