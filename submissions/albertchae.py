"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

from graph import BuildGraph


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    results: dict[str, bytes] = {}

    remaining = {name: len(t.deps) for name, t in graph.targets.items()}
    dependents: dict[str, list[str]] = {name: [] for name in graph.targets}
    for name, target in graph.targets.items():
        for dep in target.deps:
            dependents[dep.name].append(name)

    if not graph.targets:
        return results

    lock = threading.Lock()
    done = threading.Event()
    pending = len(graph.targets)
    workers = os.cpu_count() or 1

    with ThreadPoolExecutor(max_workers=workers) as executor:

        def run(name: str | None) -> None:
            nonlocal pending
            while name is not None:
                target = graph.targets[name]
                # Safe to read without the lock: name was only submitted
                # after every dep's result was written under `lock`, and
                # the release/acquire establishes happens-before.
                dep_results = {d.name: results[d.name] for d in target.deps}
                result = target.build(dep_results)

                newly_ready: list[str] = []
                with lock:
                    results[name] = result
                    pending -= 1
                    finished = pending == 0
                    for dep in dependents[name]:
                        remaining[dep] -= 1
                        if remaining[dep] == 0:
                            newly_ready.append(dep)

                if finished:
                    done.set()

                # Keep one ready dependent on this thread (chains stay
                # in-thread, no executor round-trip); fan the rest out.
                name = newly_ready.pop() if newly_ready else None
                for dep in newly_ready:
                    executor.submit(run, dep)

        # Snapshot the initial roots before submitting anything — once we
        # start submitting, workers begin decrementing `remaining` and the
        # naive iteration would re-pick targets that just became ready.
        roots = [name for name, n in remaining.items() if n == 0]
        # Main thread runs one root inline; others go to the pool.
        for name in roots[1:]:
            executor.submit(run, name)
        run(roots[0])
        done.wait()

    return results
