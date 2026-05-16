"""Free-threaded build scheduler.

Strategy:
- Spawn NUM_WORKERS threads (24, matching the eval machine's core count).
  Free-threaded Python 3.14t removes the GIL, so CPU-bound Target.build()
  calls actually run in parallel.
- Per-target scheduling state stored on each Target instance
  (`_my_result`, `_rem_deps`, `_dependents`, `_dep_objs`, `_dec_lock`).
  No shared scheduling dicts: in FT mode every shared dict carries a
  per-object mutex that becomes a bottleneck under contention.
- Lock-free dep decrement for low-in-degree targets. Targets with in-deg
  ≤ 1 can only ever be decremented by a single thread (their lone
  predecessor), so no synchronization is needed. Only high-in-degree
  targets get a `_dec_lock` (drawn from a 64-lock shard pool, so we
  don't pay per-target allocation on large graphs).
  - Diamond.json: 99% of targets have in-deg ≤ 1 → almost all decs are
    lock-free. This is the main lever for diamond's 13.4x → ~16.8x ceiling.
- A single sched_lock + Condition still guards the ready deque, the
  remaining counter, and worker wake-ups. Its critical section shrinks
  to: push extras, dec remaining, notify. No dep loop inside.
- Inline next-task fast path: the heaviest newly-ready successor stays
  on the same thread (no cvwait+wakeup pair per chain step).
- LPT ordering: dependents pre-sorted by work descending. The current
  worker picks the heaviest as its inline next; other workers pop
  heaviest-first from the deque. Minimizes makespan on wide fanouts
  (e.g. diamond's expand levels).
- Sequential fast-path: chain-like graphs (in-deg ≤ 1 AND fan-out ≤ 1
  everywhere) get walked on the calling thread to skip thread-spawn
  and sched_cv handoff costs.
- Extras notification: notify_all when many ready, else N×notify().
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
        # Only targets with >1 predecessor can race on their counter.
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

                # Decrement dep counters outside the global cv lock when
                # the dep can't race (in-deg ≤ 1).
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
                with cv:
                    if extras:
                        # extras is in fanout-sorted order (LPT): heaviest first.
                        next_target = extras[0]
                        if len(extras) > 1:
                            ready.extend(extras[1:])
                            rest = len(extras) - 1
                            if rest >= 4:
                                cv.notify_all()
                            else:
                                for _ in range(rest):
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
