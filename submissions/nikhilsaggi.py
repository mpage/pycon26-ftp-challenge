from __future__ import annotations
import os
import queue
import threading

from graph import BuildGraph

def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build every target in `graph` in parallel, respecting dependencies.

    Algorithm (resembles Kahn's algorithm):
      1. Pre-compute two structures for each target:
         - remaining_deps: a one-element list acting as an atomic counter of
           how many dependencies have not yet finished.
         - dependents: a list of (downstream_target, remaining_deps_ref) pairs
           so that when this target finishes, it can decrement its dependents.
      2. Seed a thread-safe queue with all targets whose remaining_deps is 0
         (i.e., they have no dependencies and can build immediately).
      3. Worker threads loop: dequeue a target, gather its dependency results,
         call target.build(dep_results), store the result, then for each
         dependent decrement its counter. If it hits 0, enqueue it.
      4. The main thread waits on a Semaphore that workers release once per
         completed target. After all N targets finish, it calls
         ready.shutdown() which raises queue.ShutDown in blocked workers.

    Thread-safety (Python 3.14t / free-threading):
      - remaining_deps counters are each a one-element list. Decrementing and
        reading use a per-counter threading.Lock, replacing the old single
        global lock to reduce contention on high-fan-out nodes.
      - results[name] writes target disjoint keys. Safe without a lock in
        CPython (dict bucket writes to distinct keys don't race).
      - queue.Queue is fully thread-safe.
    """
    targets = graph.targets
    n_total = len(targets)
    if n_total == 0:
        return {}

    # --- Dependency bookkeeping ---
    # remaining_deps[name] = [count, Lock] - mutable counter of unfinished deps.
    # Using a per-target lock+list instead of a global lock so workers only
    # contend when they share a downstream target, not on every completion.
    remaining_deps: dict[str, list] = {}
    # dependents[name] → list of (downstream_target, remaining_deps entry)
    # Pre-resolved so workers never touch the shared dicts during the hot loop.
    dependents: dict[str, list[tuple]] = {name: [] for name in targets}

    for name, target in targets.items():
        remaining_deps[name] = [len(target.deps), threading.Lock()]

    for name, target in targets.items():
        for dep in target.deps:
            # Use references to `remaining_deps` entries 
            dependents[dep.name].append((target, remaining_deps[name]))

    # --- Shared state ---
    # Maps target name → build result (bytes). Workers write disjoint keys.
    results: dict[str, bytes] = {}
    # Work queue: holds Target objects ready to build, or _SENTINEL to quit.
    ready: queue.Queue = queue.Queue()
    # Main thread acquires this N times; each worker release signals one done.
    done_signal = threading.Semaphore(0)

    # Seed the queue with all root targets (no dependencies).
    for name, (count, _lock) in remaining_deps.items():
        if count == 0:
            ready.put(targets[name])

    n_workers = min(n_total, os.cpu_count() or 1)

    def worker() -> None:
        """Pull targets from the queue, build them, and enqueue dependents."""
        try:
            while True:
                target = ready.get()

                # Collect results from this target's already-completed dependencies.
                dep_results = {dep.name: results[dep.name] for dep in target.deps}

                # Execute the build (CPU-bound work released by free-threading).
                results[target.name] = target.build(dep_results)

                # Notify each downstream target that one of its deps is done.
                for downstream, (counter_list_ref) in dependents[target.name]:
                    # counter_list_ref is [count, Lock] - decrement under its lock.
                    _cnt, _lk = counter_list_ref
                    enqueue = False
                    with _lk:
                        counter_list_ref[0] -= 1
                        if counter_list_ref[0] == 0:
                            enqueue = True
                    if enqueue:
                        ready.put(downstream)

                # Tell the main thread one more target is done.
                done_signal.release()
        except queue.ShutDown:
            return

    # --- Launch workers using raw threads (lighter than ThreadPoolExecutor
    # since we manage scheduling ourselves and never use futures). ---
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(n_workers)]
    for t in threads:
        t.start()

    # Wait for every target to finish building.
    for _ in range(n_total):
        done_signal.acquire()

    # Shut down the queue; blocked get() calls raise queue.ShutDown.
    ready.shutdown()
    for t in threads:
        t.join()

    return results
