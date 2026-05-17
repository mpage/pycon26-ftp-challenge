"""Parallel build scheduler using Python 3.14t (no GIL).

How it works:
  1. Find all targets with no dependencies. These will be the entrypoint of the build.
  2. Queue up all targets with no dependencies.
  3. When a target finishes, store its build result in a shared state and check its dependents. \
     If a dependent has all its deps now built, it becomes ready too.
  4. Repeat until everything is built.
"""

import os
import queue
import threading

from graph import BuildGraph


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    number_of_targets = len(targets)
    if number_of_targets == 0:
        return {}

    target_to_dependents: dict[str, list[str]] = {name: [] for name in targets}
    target_to_number_of_dependencies: dict[str, int] = {}

    for name, target in targets.items():
        target_to_number_of_dependencies[name] = len(target.deps)
        for dep in target.deps:
            target_to_dependents[dep.name].append(name)

    task_queue: queue.Queue[str | None] = queue.Queue()

    for name, count in target_to_number_of_dependencies.items():
        if count == 0:
            task_queue.put(name)

    builds: dict[str, bytes] = {}
    lock = threading.Lock()
    number_of_targets_built = [0]
    number_of_workers = os.cpu_count() or 24

    def worker():
        while True:
            target_name = task_queue.get()
            if target_name == "ALL_TARGETS_BUILT":
                return

            target = targets[target_name]
            dependency_builds = {dependency.name: builds[dependency.name] for dependency in target.deps}
            result = target.build(dependency_builds)

            with lock:
                builds[target_name] = result
                number_of_targets_built[0] += 1

                for dependency_name in target_to_dependents[target_name]:
                    target_to_number_of_dependencies[dependency_name] -= 1
                    if target_to_number_of_dependencies[dependency_name] == 0:
                        task_queue.put(dependency_name)

                if number_of_targets_built[0] == number_of_targets:
                    for _ in range(number_of_workers):
                        task_queue.put("ALL_TARGETS_BUILT")

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(number_of_workers)]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return builds
