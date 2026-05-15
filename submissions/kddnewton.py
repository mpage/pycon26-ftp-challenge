import _thread
import queue
import threading

from graph import BuildGraph

NUM_WORKERS = 24


def build_all(graph: BuildGraph):
    targets = graph.targets
    n = len(targets)
    in_degree = [0] * n
    dependents = [None] * n
    ready = queue.SimpleQueue()
    dep_info = [None] * n
    has_parallelism = False

    for i, target in enumerate(target_list := list(targets.values())):
        target._id = i
        if target.deps:
            in_degree[i] = len(target.deps)
            dep_info[i] = [(dep.name, dep._id) for dep in target.deps]
            for dep in target.deps:
                dep_id = dep._id
                if dependents[dep_id] is None:
                    dependents[dep_id] = [i]
                else:
                    if not has_parallelism:
                        has_parallelism = True
                    dependents[dep_id].append(i)
        else:
            dep_info[i] = ()
            if not has_parallelism and not ready.empty():
                has_parallelism = True
            ready.put(i)

    if has_parallelism:
        results = [None] * n
        lock = threading.Lock()
        done = threading.Event()

        def worker():
            nonlocal n
            while (tid := ready.get()) != -1:
                results[tid] = target_list[tid].build(
                    {name: results[idx] for name, idx in dep_info[tid]}
                )

                with lock:
                    n -= 1
                    if n == 0:
                        done.set()
                        for _ in range(NUM_WORKERS - 1):
                            ready.put(-1)
                        return
                    succs = dependents[tid]
                    if succs:
                        for succ_id in succs:
                            in_degree[succ_id] -= 1
                            if in_degree[succ_id] == 0:
                                ready.put(succ_id)

        for _ in range(NUM_WORKERS):
            _thread.start_joinable_thread(worker, daemon=True)
        done.wait()
    else:
        results = [None] * n
        result = target_list[tid := ready.get()].build({})
        for _ in range(n - 1):
            results[tid] = result
            name, idx = dep_info[tid := dependents[tid][0]][0]
            result = target_list[tid].build({name: results[idx]})
