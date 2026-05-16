"""Build graph simulator challenge — parallel scheduler using threads."""

import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from graph import BuildGraph, Target


NUM_WORKERS = os.cpu_count() or 24

# Strategy:
#
# - SimpleQueue to feed workers
# - Attempt to guess graph shape -> use different strategies?
# - Do setup in parallel?

def build_all(graph: BuildGraph) -> dict[str, bytes]:
    results: dict[str, bytes] = {}

    # Assign each target an id
    idx_ctr = 0
    for target in graph.targets.values():
        target.index = idx_ctr
        idx_ctr += 1

    # Precompute reverse deps and in-degree
    num_targets = len(graph.targets)
    dependents: list[list[Target]] = [[] for _ in range(num_targets)]
    for name, target in graph.targets.items():
        target.in_degree = len(target.deps)
        for dep in target.deps:
            dependents[dep.index].append(target)

    # Track remaining count for completion signaling
    remaining = len(graph.targets)
    lock = threading.Lock()
    done = threading.Event()
    queue: deque[Target] = deque()
    queue_not_empty = threading.Condition(lock)

    # Seed with zero-dependency targets
    for target in graph.targets.values():
        if target.in_degree == 0:
            queue.append(target)

    def worker():
        nonlocal remaining
        while True:
            with queue_not_empty:
                while not queue and not done.is_set():
                    queue_not_empty.wait()
                if done.is_set() and not queue:
                    return
                target = queue.popleft()

            target_index = target.index
            dep_results = {d.name: results[d.index] for d in target.deps}
            result = target.build(dep_results)
            results[target_index] = result

            newly_ready = []
            with lock:
                for dep in dependents[target_index]:
                    dep.in_degree -= 1
                    if dep.in_degree == 0:
                        newly_ready.append(dep)
                remaining -= 1
                if remaining == 0:
                    done.set()

            if newly_ready:
                with queue_not_empty:
                    queue.extend(newly_ready)
                    if len(newly_ready) > 1:
                        queue_not_empty.notify_all()
                    else:
                        queue_not_empty.notify()

            if done.is_set():
                with queue_not_empty:
                    queue_not_empty.notify_all()
                return

    threads = []
    for _ in range(NUM_WORKERS):
        t = threading.Thread(target=worker)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return results
