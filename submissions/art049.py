"""Free-threaded build scheduler.

Strategy:
- Spawn NUM_WORKERS threads (24, matching the eval machine's core count).
  Free-threaded Python 3.14t/3.15t removes the GIL, so the CPU-bound
  Target.build() calls actually run in parallel.
- Each Target keeps its own scheduling state as instance attributes
  (`_my_result`, `_rem_deps`, `_dependents`, `_dep_objs`). No shared
  scheduling dicts: in FT mode every shared dict carries a per-object
  mutex, and a single hot one becomes the bottleneck under contention.
- A single sched_lock + Condition guards the ready deque and dep counters.
  Its critical section is the per-completion decrement loop and the
  remaining-target counter — tiny in walltime.
- Inline next-task fast path: when a finished target enables exactly one
  successor, the same worker runs it immediately. Saves a cvwait+wakeup
  pair per chain step (~50us each on macOS pthreads).
- Extras (>1 newly-ready) get pushed onto the deque. Use notify_all when
  there are many; cheaper than repeated notify() once the wake count is
  large enough.
"""

from __future__ import annotations

import threading
from collections import deque

from graph import BuildGraph


NUM_WORKERS = 24


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets

    for t in targets.values():
        t._rem_deps = len(t.deps)
        t._dependents = []
        t._dep_objs = tuple(t.deps)
        t._my_result = None
    for t in targets.values():
        for dep in t.deps:
            dep._dependents.append(t)
    for t in targets.values():
        t._dependents = tuple(t._dependents)

    ready: deque = deque()
    sched_lock = threading.Lock()
    sched_cv = threading.Condition(sched_lock)
    remaining = [len(targets)]

    for t in targets.values():
        if t._rem_deps == 0:
            ready.append(t)

    def worker():
        cv = sched_cv
        while True:
            with cv:
                while not ready and remaining[0] > 0:
                    cv.wait()
                if remaining[0] == 0:
                    cv.notify_all()
                    return
                target = ready.popleft()

            while target is not None:
                dep_results = {d.name: d._my_result for d in target._dep_objs}
                target._my_result = target.build(dep_results)

                next_target = None
                extras_count = 0
                with cv:
                    for dep in target._dependents:
                        dep._rem_deps -= 1
                        if dep._rem_deps == 0:
                            if next_target is None:
                                next_target = dep
                            else:
                                ready.append(dep)
                                extras_count += 1
                    if extras_count >= 4:
                        cv.notify_all()
                    elif extras_count:
                        for _ in range(extras_count):
                            cv.notify()
                    remaining[0] -= 1
                    if remaining[0] == 0:
                        cv.notify_all()
                target = next_target

    threads = [threading.Thread(target=worker) for _ in range(NUM_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return {t.name: t._my_result for t in targets.values()}
