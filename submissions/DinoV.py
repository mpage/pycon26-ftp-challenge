"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

from concurrent.futures import ThreadPoolExecutor
from heapq import heappush, heappop
from threading import Event, Lock
from graph import BuildGraph

NUM_WORKERS = 24


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    targets = graph.targets
    if not targets:
        return {}

    dependents: dict[str, list[str]] = {name: [] for name in targets}
    in_degree: dict[str, int] = {name: 0 for name in targets}

    for name, target in targets.items():
        in_degree[name] = len(target.deps)
        for dep in target.deps:
            dependents[dep.name].append(name)

    results: dict[str, bytes] = {}
    lock = Lock()
    done = Event()
    heap: list[tuple[int, str]] = []
    remaining = len(targets)

    for name in targets:
        if in_degree[name] == 0:
            heappush(heap, (-len(dependents[name]), name))

    def submit_ready(executor: ThreadPoolExecutor) -> None:
        while heap:
            _, name = heappop(heap)
            executor.submit(build_target, executor, name)

    def build_target(executor: ThreadPoolExecutor, name: str) -> None:
        nonlocal remaining
        target = targets[name]
        dep_results = {dep.name: results[dep.name] for dep in target.deps}
        result = target.build(dep_results)

        with lock:
            results[name] = result
            for child in dependents[name]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    heappush(heap, (-len(dependents[child]), child))
            submit_ready(executor)
            remaining -= 1
            if remaining == 0:
                done.set()

    executor = ThreadPoolExecutor(max_workers=NUM_WORKERS)
    with lock:
        submit_ready(executor)
    done.wait()
    executor.shutdown(wait=False)

    return results
