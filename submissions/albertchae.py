"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

import os
import threading
from collections import deque

from graph import BuildGraph


class _WorkQueue:
    """Minimal FIFO queue with bulk-put. One condvar acquire per call,
    regardless of how many items go in — cuts queue-mutex contention
    when wide-fanout targets dump many ready dependents at once.
    """

    __slots__ = ("_deque", "_cond", "_shutdown")

    def __init__(self) -> None:
        self._deque: deque[str] = deque()
        self._cond = threading.Condition()
        self._shutdown = False

    def put(self, item: str) -> None:
        with self._cond:
            self._deque.append(item)
            self._cond.notify()

    def put_many(self, items: list[str]) -> None:
        if not items:
            return
        with self._cond:
            self._deque.extend(items)
            self._cond.notify(len(items))

    def get(self) -> str | None:
        """Block until an item is available. Return None once shut down."""
        with self._cond:
            while not self._deque and not self._shutdown:
                self._cond.wait()
            if self._deque:
                return self._deque.popleft()
            return None

    def shutdown(self) -> None:
        with self._cond:
            self._shutdown = True
            self._cond.notify_all()


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    results: dict[str, bytes] = {}

    if not graph.targets:
        return results

    remaining = {name: len(t.deps) for name, t in graph.targets.items()}
    dependents: dict[str, list[str]] = {name: [] for name in graph.targets}
    for name, target in graph.targets.items():
        for dep in target.deps:
            dependents[dep.name].append(name)

    lock = threading.Lock()
    pending = len(graph.targets)
    work_queue = _WorkQueue()
    n_workers = os.cpu_count() or 1

    def run(start: str) -> None:
        nonlocal pending
        name: str | None = start
        while name is not None:
            target = graph.targets[name]
            # Safe to read without the lock: name was only enqueued
            # after every dep's result was written under `lock`, and
            # the release/acquire establishes happens-before.
            dep_results = {d.name: results[d.name] for d in target.deps}
            result = target.build(dep_results)

            inline: str | None = None
            extras: list[str] | None = None
            with lock:
                results[name] = result
                pending -= 1
                finished = pending == 0
                for dep in dependents[name]:
                    remaining[dep] -= 1
                    if remaining[dep] == 0:
                        # First ready dependent stays in-thread (chains
                        # avoid the queue round-trip); the rest get
                        # bulk-enqueued outside the lock. Allocating the
                        # extras list lazily skips the empty-case alloc
                        # for fanout ≤ 1, which is most builds.
                        if inline is None:
                            inline = dep
                        elif extras is None:
                            extras = [dep]
                        else:
                            extras.append(dep)

            if finished:
                # Last build done: wake every worker blocked on get().
                # Safe — `inline`/`extras` are provably empty when
                # finished (any dependent would mean pending > 0), so
                # no put_many will race the shutdown.
                work_queue.shutdown()
            elif extras is not None:
                work_queue.put_many(extras)

            name = inline

    def worker() -> None:
        while True:
            item = work_queue.get()
            if item is None:
                return
            run(item)

    # Seed the queue with every root; workers will pick them up.
    roots = [name for name, n in remaining.items() if n == 0]
    work_queue.put_many(roots)

    # Spawn n_workers - 1 threads; main thread is the Nth worker.
    threads = [threading.Thread(target=worker) for _ in range(n_workers - 1)]
    for t in threads:
        t.start()
    worker()

    for t in threads:
        t.join()

    return results
