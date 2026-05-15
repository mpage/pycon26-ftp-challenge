"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from graph import BuildGraph


def _compute_critical(graph, dependents):
    """Return dict mapping target name -> critical path length (work units)."""
    in_deg = {name: len(t.deps) for name, t in graph.targets.items()}
    q: deque = deque(name for name, d in in_deg.items() if d == 0)
    order: list[str] = []
    while q:
        name = q.popleft()
        order.append(name)
        for dep in dependents[name]:
            in_deg[dep.name] -= 1
            if in_deg[dep.name] == 0:
                q.append(dep.name)
    critical: dict[str, int] = {}
    for name in reversed(order):
        t = graph.targets[name]
        best = max((critical[d.name] for d in dependents[name]), default=0)
        critical[name] = t.work + best
    return critical


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order."""
    if not graph.targets:
        return {}

    results: dict[str, bytes] = {}
    lock = threading.Lock()
    in_degree = {name: len(t.deps) for name, t in graph.targets.items()}
    dependents: dict[str, list] = {name: [] for name in graph.targets}
    for target in graph.targets.values():
        for dep in target.deps:
            dependents[dep.name].append(target)

    critical = _compute_critical(graph, dependents)

    remaining = [len(graph.targets)]
    done_event = threading.Event()

    max_workers = min(64, (os.cpu_count() or 1) * 2)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        def run(target):
            while target is not None:
                dep_results = {d.name: results[d.name] for d in target.deps}
                result = target.build(dep_results)
                # Write result before acquiring lock — safe because dependents
                # can only observe this key after we decrement their in_degree.
                results[target.name] = result
                to_submit = []
                with lock:
                    for dep in dependents[target.name]:
                        in_degree[dep.name] -= 1
                        if in_degree[dep.name] == 0:
                            to_submit.append(dep)
                    remaining[0] -= 1
                    is_done = remaining[0] == 0
                if is_done:
                    done_event.set()
                if to_submit:
                    # Execute the highest-priority task inline; submit the rest
                    inline = max(to_submit, key=lambda t: critical[t.name])
                    for dep in to_submit:
                        if dep is not inline:
                            executor.submit(run, dep)
                    target = inline
                else:
                    target = None

        roots = sorted(
            (t for name, t in graph.targets.items() if in_degree[name] == 0),
            key=lambda t: critical[t.name],
            reverse=True,
        )
        for t in roots:
            executor.submit(run, t)

        done_event.wait()

    return results
