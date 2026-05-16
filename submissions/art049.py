"""Free-threaded build scheduler.

Strategy:
- 24 long-lived worker threads.
- Per-target state on Target instances (`_my_result`, `_rem_deps`,
  `_dependents`, `_dep_objs`, `_dec_lock`).
- Lock-free dep decrement for low-in-degree targets (in-deg ≤ 1 can't
  race). High-in-degree targets share a 64-lock shard pool.
- Lock-free deque popleft via best-effort attempt; fall back to
  Condition.wait only when the queue is genuinely empty.
- Separate `done_lock` for the remaining counter so the sched_cv is
  only acquired when we actually need to wake or be woken — most
  tasks do 0 cv acquires.
- Inline next-task fast path on the heaviest newly-ready successor.
- LPT ordering of dependents (descending by work).
- Sequential fast-path for chain-like graphs.
"""

from __future__ import annotations

import threading
from collections import deque

from graph import BuildGraph


NUM_WORKERS = 24
SHARD_COUNT = 64
_SHARD_MASK = SHARD_COUNT - 1


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets

    shard_locks = [threading.Lock() for _ in range(SHARD_COUNT)]

    for t in targets.values():
        t._rem_deps = len(t.deps)
        t._dependents = []
        t._dep_objs = tuple(t.deps)
        t._my_result = None
        t._dec_lock = (
            shard_locks[id(t) & _SHARD_MASK] if t._rem_deps > 1 else None
        )
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
    done_lock = threading.Lock()
    remaining = [len(targets)]
    all_done = threading.Event()

    initial = [t for t in targets.values() if t._rem_deps == 0]
    initial.sort(key=lambda t: -t.work)
    ready.extend(initial)

    def worker():
        cv = sched_cv
        while True:
            target = None
            # Lock-free best-effort pop. deque.popleft is atomic in CPython.
            try:
                target = ready.popleft()
            except IndexError:
                pass

            if target is None:
                if all_done.is_set():
                    return
                with cv:
                    while not ready and not all_done.is_set():
                        cv.wait()
                    if all_done.is_set():
                        return
                    try:
                        target = ready.popleft()
                    except IndexError:
                        continue

            while target is not None:
                dep_results = {d.name: d._my_result for d in target._dep_objs}
                target._my_result = target.build(dep_results)

                extras = []
                for dep in target._dependents:
                    dl = dep._dec_lock
                    if dl is None:
                        dep._rem_deps -= 1
                        if dep._rem_deps == 0:
                            extras.append(dep)
                    else:
                        with dl:
                            dep._rem_deps -= 1
                            ready_now = dep._rem_deps == 0
                        if ready_now:
                            extras.append(dep)

                next_target = None
                if extras:
                    next_target = extras[0]  # LPT: heaviest
                    if len(extras) > 1:
                        # deque.extend is atomic; signal waiters separately.
                        ready.extend(extras[1:])
                        rest = len(extras) - 1
                        with cv:
                            if rest >= 4:
                                cv.notify_all()
                            else:
                                for _ in range(rest):
                                    cv.notify()

                with done_lock:
                    remaining[0] -= 1
                    done_now = remaining[0] == 0
                if done_now:
                    all_done.set()
                    with cv:
                        cv.notify_all()

                target = next_target

    threads = [threading.Thread(target=worker) for _ in range(NUM_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return {t.name: t._my_result for t in targets.values()}
