"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

from queue import SimpleQueue

from graph import BuildGraph, Target
import gc
from collections import deque
import threading
import concurrent.futures
import os


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    gc.disable()  # worth a try
    results: dict[str, bytes] = {}

    ID_TO_TARGET: list[None | Target] = [None] * len(graph.targets)
    DEPENDENTS = [[] for i in range(len(graph.targets))]
    QUEUE = deque()
    REMAINING = [0] * len(graph.targets)

    ready = SimpleQueue()
    done = threading.Event()
    doing = len(graph.targets)
    state_lock = threading.Lock()

    NUM_THREADS = os.cpu_count()

    def worker():
        nonlocal doing

        while True:
            target_id = ready.get()

            if target_id is None:
                return

            target = ID_TO_TARGET[target_id]

            with state_lock:
                tmp = {dep.name: results[dep.name] for dep in target.deps}
            result = target.build(tmp)

            with state_lock:
                results[target.name] = result

                for dependent_id in DEPENDENTS[target_id]:
                    REMAINING[dependent_id] -= 1
                    if REMAINING[dependent_id] == 0:
                        ready.put(dependent_id)

                doing -= 1
                if doing == 0:
                    done.set()
                    for _ in range(NUM_THREADS):
                        ready.put(None)
                    return

    for i, target in enumerate(graph.targets.values()):
        target._id = i

    for i, target in enumerate(graph.targets.values()):
        ID_TO_TARGET[i] = target
        if not target.deps:
            QUEUE.appendleft(i)
        REMAINING[i] = len(target.deps)
        for dep in target.deps:
            DEPENDENTS[dep._id].append(i)

    for q in QUEUE:
        ready.put(q)

    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_THREADS - 1) as executor:
        [executor.submit(worker) for _ in range(NUM_THREADS - 1)]
        worker()
    gc.enable()
    return results
