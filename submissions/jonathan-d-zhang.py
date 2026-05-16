from __future__ import annotations

import itertools
import queue
import threading

from graph import BuildGraph

N_WORKERS = 24

def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    results: dict[str, bytes] = {}

    dependents: dict[str, list[str]] = {name: [] for name in targets}
    for name, target in targets.items():
        for dep in target.deps:
            dependents[dep.name].append(name)

    d_in = {name: len(target.deps) for name, target in targets.items()}
    d_in_lock = threading.Lock()

    Q: queue.Queue[str] = queue.Queue()
    for name, count in d_in.items():
        if count == 0:
            Q.put(name)

    def worker():
        while True:
            target_name = Q.get()

            while True:
                target = targets[target_name]
                dep_results = {dep.name: results[dep.name] for dep in target.deps}
                results[target_name] = target.build(dep_results)

                newly_ready = []
                with d_in_lock:
                    for n in dependents[target_name]:
                        d_in[n] -= 1
                        if d_in[n] == 0:
                            newly_ready.append(n)

                if newly_ready:
                    if len(newly_ready) > 1:
                        newly_ready.sort(key=lambda n: targets[n].work, reverse=True)
                    target_name = newly_ready[0]
                    for n in itertools.islice(newly_ready, 1, None):
                        Q.put(n)
                else:
                    break

            Q.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(N_WORKERS)]
    for t in threads:
        t.start()
    Q.join()

    return results
