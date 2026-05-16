"""Free-threaded build scheduler.

Strategy:
- 24 long-lived worker threads. Free-threaded Python 3.14t removes the
  GIL, so CPU-bound Target.build() calls actually run in parallel.
- Per-target state on Target instances (`_my_result`, `_rem_deps`,
  `_dependents`, `_dep_objs`). No shared scheduling dicts.
- A single sched_lock + Condition. Critical section per task: dep
  decrements, push extras (if any), dec remaining, notify.
- Inline next-task fast path: the first newly-ready successor stays
  on the same thread. Saves a cv-wait+wakeup pair per chain step.
- LPT ordering of dependents (descending by work) — sorted lazily,
  only for targets that actually have multiple dependents. Helps wide
  fanouts (e.g. diamond's expand levels).
- Sequential fast-path for chain-like graphs (every target has
  in-deg ≤ 1 AND fan-out ≤ 1): walk on the calling thread.
- Setup keeps a tight loop: build dependents lists once, track whether
  any high-fanout / high-indeg target exists, and only sort when there
  is something to sort. Tree-shaped graphs skip the sort pass entirely.
"""

from __future__ import annotations

import threading
from collections import deque

from graph import BuildGraph


NUM_WORKERS = 24


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets

    any_high_indeg = False
    for t in targets.values():
        t._rem_deps = len(t.deps)
        t._dependents = []
        t._dep_objs = t.deps
        t._my_result = None
        if t._rem_deps > 1:
            any_high_indeg = True

    any_high_fanout = False
    for t in targets.values():
        for dep in t.deps:
            d_dep = dep._dependents
            d_dep.append(t)
            if len(d_dep) > 1:
                any_high_fanout = True

    # Chain-like graphs (every target has in-deg ≤ 1 AND fan-out ≤ 1)
    # have zero parallelism; skip threading entirely.
    if not any_high_indeg and not any_high_fanout:
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

    # LPT-sort only the dependents lists that actually have >1 entries.
    # Tree-shaped graphs (max fan-out = 1) skip this entire pass.
    if any_high_fanout:
        for t in targets.values():
            d_list = t._dependents
            if len(d_list) > 1:
                d_list.sort(key=lambda d: -d.work)

    ready: deque = deque()
    sched_lock = threading.Lock()
    sched_cv = threading.Condition(sched_lock)
    remaining = [len(targets)]

    initial = [t for t in targets.values() if t._rem_deps == 0]
    if len(initial) > 1:
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
