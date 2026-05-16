"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""
from graph import BuildGraph, Target
from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED

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
    todo: list[tuple[Target, dict]] = []
    dependents: dict[str, set[str]] = {}
    # name -> (waiting_on, results)
    dep_results: dict[str, tuple[int, dict[str, bytes]]] = {}
    for name, target in graph.targets.items():
        assert name == target.name
        if name not in visited:
            dfs(target, visited, dependents, todo, dep_results)

    latest = ""
    with ThreadPoolExecutor(max_workers=24) as executor:
        # e[0] is the target
        future_to_node: dict[Future[bytes], str] = {}
        pending = set()
        for target_and_dep_res in todo:
            future = executor.submit(target_and_dep_res[0].build, target_and_dep_res[1])
            future_to_node[future] = target_and_dep_res[0].name
            pending.add(future)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                name = future_to_node[future]
                latest = name
                res = future.result()
                dependant = dependents.get(name, [])
                for child in dependant:
                    dep_results[child][1][name] = res
                    dep_results[child] = (dep_results[child][0]-1, dep_results[child][1])
                    if dep_results[child][0] == 0:
                        target=graph.targets[child]
                        new_future = executor.submit(target.build, dep_results[child][1])
                        future_to_node[new_future] = target.name
                        pending.add(new_future)
    return dep_results[latest][1]

def dfs(node: Target, visited: set[str], dependents: dict[str, set[str]], todo: list[tuple], dep_results: dict[str, tuple[int, dict[str, bytes]]]):
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
        todo.append((node, {}))

