"""Parallel build scheduler for Python 3.14t (free-threading).

Topo-driven worker pool. Optimizations:

1. Inline chain execution — after building, if a dependent becomes ready,
   run it directly on the same worker instead of queueing it. Collapses
   linear chains into a tight serial loop.
2. Batched pending decrement — workers count the length of each inlined
   chain locally and decrement the global pending counter once per chain
   instead of once per build. Drastically reduces global-lock acquisitions
   on chain-heavy graphs.
3. Main thread participates as a worker.
4. Sharded locks for per-target remaining-deps counters.
5. Target objects (not names) passed through the queue and dependents.
"""

from __future__ import annotations

import os
import threading
from queue import SimpleQueue

from graph import BuildGraph, Target


_NUM_SHARDS = 64


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets_map = graph.targets
    all_targets = list(targets_map.values())
    results: dict[str, bytes] = {}

    remaining: dict[str, int] = {t.name: len(t.deps) for t in all_targets}
    for t in all_targets:
        t._deps_to_notify = []
    for t in all_targets:
        for dep in t.deps:
            dep._deps_to_notify.append(t)

    total = len(all_targets)
    ready: SimpleQueue = SimpleQueue()
    for t in all_targets:
        if remaining[t.name] == 0:
            ready.put(t)

    rem_locks = [threading.Lock() for _ in range(_NUM_SHARDS)]
    pending = [total]
    pending_lock = threading.Lock()
    SENTINEL: Target | None = None
    num_workers = min(total, (os.cpu_count() or 1))

    def worker() -> None:
        local_get = ready.get
        local_put = ready.put
        locks = rem_locks
        while True:
            target = local_get()
            if target is SENTINEL:
                return
            chain_len = 0
            while target is not None:
                deps = target.deps
                dep_results = {d.name: results[d.name] for d in deps} if deps else {}
                results[target.name] = target.build(dep_results)
                chain_len += 1

                next_inline: Target | None = None
                for dep_t in target._deps_to_notify:
                    dn = dep_t.name
                    lock = locks[hash(dn) % _NUM_SHARDS]
                    with lock:
                        nv = remaining[dn] - 1
                        remaining[dn] = nv
                    if nv == 0:
                        if next_inline is None:
                            next_inline = dep_t
                        else:
                            local_put(dep_t)

                target = next_inline

            # Batch-decrement pending once per inlined chain.
            with pending_lock:
                pending[0] -= chain_len
                if pending[0] == 0:
                    for _ in range(num_workers):
                        local_put(SENTINEL)

    threads = [
        threading.Thread(target=worker, daemon=True) for _ in range(num_workers - 1)
    ]
    for th in threads:
        th.start()

    worker()

    for th in threads:
        th.join()

    return results
