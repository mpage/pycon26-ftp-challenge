"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""
from graph import BuildGraph, Target
from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from queue import SimpleQueue
from threading import Lock

def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    # TODO: implement your build scheduler here
    # future_to_node = {}
    visited: set[str] = set()
    todo: list[Target] = []
    dependents: dict[str, set[str]] = {}
    # name -> (waiting_on, results)
    dep_results: dict[str, tuple[int, dict[str, bytes]]] = {}
    for name, target in graph.targets.items():
        assert name == target.name
        if name not in visited:
            dfs(target, visited, dependents, todo, dep_results)

    # find the # of roots
    root_cnt = len(graph.targets) - len(dependents)

    ready = SimpleQueue()
    lock = Lock()
    for e in todo:
        ready.put(e)

    def worker(target: Target):
        nonlocal root_cnt
        # build
        arg = dep_results[target.name][1] if target.name in dep_results else {}
        res = target.build(arg)

        # update state
        lock.acquire()
        if target.name not in dependents:
            # no dependers (in_deg), this is the root
            root_cnt -= 1
            if root_cnt == 0:
                ready.put((None, target.name))
            lock.release()
            return
        for d in dependents.get(target.name, []):
            dep_results[d][1][target.name] = res
            dep_results[d] = (dep_results[d][0]-1, dep_results[d][1])
            if dep_results[d][0] == 0:
                ready.put(graph.targets[d])
        lock.release()
    
    with ThreadPoolExecutor(max_workers=24) as executor:
        while True:
            # submit new work
            target = ready.get()
            if isinstance(target, tuple):
                assert target[0] is None
                return dep_results[target[1]][1]
            executor.submit(worker, target)

    return None

def dfs(node: Target, visited: set[str], dependents: dict[str, set[str]], todo: list[Target], dep_results: dict[str, tuple[int, dict[str, bytes]]]):
    if node.name in visited:
        return
    visited.add(node.name)
    # build the depended -> dependent graph
    dep_results[node.name] = (len(node.deps), {})
    for child in node.deps:
        if child.name not in dependents:
            dependents[child.name] = set()
        dependents[child.name].add(node.name)
        dfs(child, visited, dependents, todo, dep_results)
    if len(node.deps) == 0:
        todo.append(node)

