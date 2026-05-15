"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

from graph import BuildGraph


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import os

    results: dict[str, bytes] = {}
    
    in_degree = {name: len(t.deps) for name, t in graph.targets.items()}
    dependents = {name: [] for name in graph.targets}
    for name, target in graph.targets.items():
        for dep in target.deps:
            dependents[dep.name].append(target)
            
    # We will use threading events to coordinate readiness
    degree_locks = {name: threading.Lock() for name in graph.targets}
    
    remaining = len(graph.targets)
    if remaining == 0:
        return results
        
    remaining_lock = threading.Lock()
    done_event = threading.Event()
    
    def worker(target):
        # Dependencies are guaranteed to be complete
        dep_results = {d.name: results[d.name] for d in target.deps}
        res = target.build(dep_results)
        results[target.name] = res
        
        ready = []
        for dep in dependents[target.name]:
            with degree_locks[dep.name]:
                in_degree[dep.name] -= 1
                if in_degree[dep.name] == 0:
                    ready.append(dep)
        
        nonlocal remaining
        with remaining_lock:
            remaining -= 1
            if remaining == 0:
                done_event.set()
                
        for r in ready:
            executor.submit(worker, r)

    workers = min(os.cpu_count() or 8, len(graph.targets))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for name, t in graph.targets.items():
            if in_degree[name] == 0:
                executor.submit(worker, t)
        
        done_event.wait()
        
    return results
