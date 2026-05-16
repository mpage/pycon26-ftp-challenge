"""Free-threaded build scheduler.

Strategy:
- 24 long-lived worker threads. Free-threaded Python 3.14t removes the
  GIL, so CPU-bound Target.build() calls actually run in parallel.
- Per-target state stored as Target instance attributes (`_my_result`,
  `_rem_deps`, `_dependents`, `_dep_objs`). No shared scheduling dicts:
  in FT mode each shared dict carries a per-object mutex, and a single
  hot one becomes the bottleneck.
- A single sched_lock + Condition guards the ready deque, the dep
  counters, and the remaining counter. Critical section per task is
  just: dec deps, push extras (if any), dec remaining, notify.
- Inline next-task fast path: the first newly-ready successor (the
  heaviest under LPT ordering) stays on the same thread. Saves a
  cv-wait+wakeup pair per chain step.
- LPT ordering: dependents are pre-sorted by work descending so the
  current worker picks the heaviest as its inline next and other
  workers pop heaviest-first from the deque. Reduces makespan on wide
  fanouts (e.g. diamond's expand levels). Skip the sort for trivial
  lists so tree-shaped graphs don't pay it.
- Sequential fast-path: chain-like graphs (in-deg ≤ 1 AND fan-out ≤ 1)
  skip thread spawn entirely and run on the calling thread.
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
    max_fanout = 0
    max_indeg = 0
    for t in targets.values():
        d_list = t._dependents
        if len(d_list) > 1:
            d_list.sort(key=lambda d: -d.work)
        t._dependents = tuple(d_list)
        if len(d_list) > max_fanout:
            max_fanout = len(d_list)
        if len(t._dep_objs) > max_indeg:
            max_indeg = len(t._dep_objs)

    # Chain-like: walk topologically on the calling thread.
    if max_fanout <= 1 and max_indeg <= 1:
        results: dict[str, bytes] = {}
        q = deque(t for t in targets.values() if t._rem_deps == 0)
        while q:
            t = q.popleft()
            dep_results = {d.name: results[d.name] for d in t._dep_objs}
            results[t.name] = t.build(dep_results)
            for nxt in t._dependents:
                nxt._rem_deps -= 1
                if nxt._rem_deps == 0:
                    q.append(nxt)
        return results

    ready: deque = deque()
    sched_lock = threading.Lock()
    sched_cv = threading.Condition(sched_lock)
    remaining = [len(targets)]

    initial = [t for t in targets.values() if t._rem_deps == 0]
    initial.sort(key=lambda t: -t.work)
    ready.extend(initial)

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
