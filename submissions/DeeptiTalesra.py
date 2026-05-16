"""Parallel build scheduler with critical-path scheduling and inline chaining."""

import _thread
import os
import queue

from graph import BuildGraph, Target

NUM_WORKERS = 24


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    n = len(targets)
    if n == 0:
        return {}

    target_list = list(targets.values())
    for idx, t in enumerate(target_list):
        t._idx = idx

    dependents: list[list[Target]] = [[] for _ in range(n)]
    initial: list[Target] = []

    for t in target_list:
        t._in_degree = len(t.deps)
        if t._in_degree == 0:
            initial.append(t)
        for dep in t.deps:
            dependents[dep._idx].append(t)

    num_workers = min(NUM_WORKERS, os.cpu_count() or 4)
    results: list[bytes | None] = [None] * n

    # Compute critical path weight: target's own work + max downstream path
    # Process in reverse topological order (leaves first) via BFS from roots
    cp_weight = [0] * n
    topo_order: list[int] = []
    topo_deg = [t._in_degree for t in target_list]
    topo_q = [t._idx for t in initial]
    head = 0
    while head < len(topo_q):
        idx = topo_q[head]
        head += 1
        topo_order.append(idx)
        for child in dependents[idx]:
            topo_deg[child._idx] -= 1
            if topo_deg[child._idx] == 0:
                topo_q.append(child._idx)

    for idx in reversed(topo_order):
        t = target_list[idx]
        max_child_weight = 0
        for child in dependents[idx]:
            w = cp_weight[child._idx]
            if w > max_child_weight:
                max_child_weight = w
        cp_weight[idx] = t.work + max_child_weight

    # Sort dependents by critical path weight so inline chaining picks the longest path
    for dep_list in dependents:
        if len(dep_list) > 1:
            dep_list.sort(key=lambda t: cp_weight[t._idx], reverse=True)

    if len(initial) == 1 and all(len(d) <= 1 for d in dependents):
        t = initial[0]
        results[t._idx] = t.build({})
        for _ in range(n - 1):
            child = dependents[t._idx][0]
            results[child._idx] = child.build(
                {dep.name: results[dep._idx] for dep in child.deps}
            )
            t = child
        return {target_list[i].name: results[i] for i in range(n)}

    initial.sort(key=lambda t: cp_weight[t._idx], reverse=True)

    q: queue.SimpleQueue = queue.SimpleQueue()
    for t in initial:
        q.put(t)

    lock = _thread.allocate_lock()
    completed = 0

    def worker():
        nonlocal completed
        _get = q.get
        _put = q.put
        _results = results
        _dependents = dependents
        _acquire = lock.acquire
        _release = lock.release
        _nw = num_workers

        while True:
            t = _get()
            if t is None:
                return

            while t is not None:
                dep_results = {dep.name: _results[dep._idx] for dep in t.deps}
                result = t.build(dep_results)

                inline = None
                to_enqueue = None
                _acquire()
                _results[t._idx] = result
                completed += 1
                if completed == n:
                    _release()
                    for _ in range(_nw - 1):
                        _put(None)
                    return
                for child in _dependents[t._idx]:
                    child._in_degree -= 1
                    if child._in_degree == 0:
                        if inline is None:
                            inline = child
                        elif to_enqueue is None:
                            to_enqueue = child
                        else:
                            if not isinstance(to_enqueue, list):
                                to_enqueue = [to_enqueue, child]
                            else:
                                to_enqueue.append(child)
                _release()

                if to_enqueue is not None:
                    if isinstance(to_enqueue, list):
                        for c in to_enqueue:
                            _put(c)
                    else:
                        _put(to_enqueue)

                t = inline

    handles = []
    for _ in range(num_workers - 1):
        h = _thread.start_joinable_thread(worker)
        handles.append(h)

    worker()

    for h in handles:
        _thread.join_thread(h)

    return {target_list[i].name: results[i] for i in range(n)}
