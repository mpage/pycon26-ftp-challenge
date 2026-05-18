import os
from queue import SimpleQueue

from concurrent.futures import ThreadPoolExecutor

from graph import BuildGraph, Target

def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """

    total = len(graph.targets)
    results: dict[str, bytes] = {}

    in_degree = {name: len(t.deps) for name, t in graph.targets.items()}
    dependents: dict[str, list[Target]] = {name: [] for name in graph.targets}
    for name, target in graph.targets.items():
        for dep in target.deps:
            dependents[dep.name].append(target)

    roots = [t for t in graph.targets.values() if in_degree[t.name] == 0]
    max_in = max(in_degree.values()) if in_degree else 0
    if len(roots) == 1 and max_in <= 1:
        cur = roots[0]
        while True:
            results[cur.name] = cur.build({d.name: results[d.name] for d in cur.deps})
            nxts = dependents[cur.name]
            if not nxts:
                break
            cur = nxts[0]
        return results
    done_queue: SimpleQueue[tuple[str, bytes]] = SimpleQueue()

    def run(target: Target, dep_results: dict[str, bytes]) -> None:
        done_queue.put((target.name, target.build(dep_results)))

    with ThreadPoolExecutor(max_workers=os.cpu_count()*3) as executor:
        for name, target in graph.targets.items():
            if in_degree[name] == 0:
                executor.submit(run, target, {})

        for _ in range(total):
            name, result = done_queue.get()
            results[name] = result
            for dependent in dependents[name]:
                in_degree[dependent.name] -= 1
                if in_degree[dependent.name] == 0:
                    dep_results = {d.name: results[d.name] for d in dependent.deps}
                    executor.submit(run, dependent, dep_results)

    return results