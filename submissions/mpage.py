"""Example parallel submission using ThreadPoolExecutor.

This is a correct but unoptimized implementation that uses a pool of threads. 11
"""

import threading
from concurrent.futures import ThreadPoolExecutor

from graph import BuildGraph


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    results: dict[str, bytes] = {}
    results_lock = threading.Lock()

    # Build reverse dependency map: target -> list of targets that depend on it
    dependents: dict[str, list[Target]] = {name: [] for name in graph.targets}
    for name, target in graph.targets.items():
        for dep in target.deps:
            dependents[dep.name].append(target)

    # Track remaining dependency count per target
    remaining_deps: dict[str, int] = {
        name: len(t.deps) for name, t in graph.targets.items()
    }
    remaining_lock = threading.Lock()

    # Event to signal completion
    done_event = threading.Event()
    targets_remaining = len(graph.targets)
    targets_remaining_lock = threading.Lock()

    def build_target(name: str) -> None:
        nonlocal targets_remaining

        try:
            target = graph.targets[name]
            with results_lock:
                dep_results = {d.name: results[d.name] for d in target.deps}

            result = target.build(dep_results)

            with results_lock:
                results[name] = result

            # Notify dependents
            ready: list[str] = []
            with remaining_lock:
                for dep in dependents[name]:
                    remaining_deps[dep.name] -= 1
                    if remaining_deps[dep.name] == 0:
                        ready.append(dep.name)

            for dep_name in ready:
                executor.submit(build_target, dep_name)

            with targets_remaining_lock:
                targets_remaining -= 1
                if targets_remaining == 0:
                    done_event.set()
        except Exception as e:
            print(e)

    # Find initially ready targets (no dependencies)
    initial_ready = [name for name, count in remaining_deps.items() if count == 0]

    executor = ThreadPoolExecutor()
    try:
        for name in initial_ready:
            executor.submit(build_target, name)
        done_event.wait()
    finally:
        executor.shutdown(wait=True)

    return results
