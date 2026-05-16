import itertools

from graph import BuildGraph, Target
from queue import Queue
from os import cpu_count
from threading import Event, Thread


def process_queue(
    queue: Queue,
    counter: itertools.count,
    num_targets: int,
    done: Event,
    results: dict,
) -> None:
    # Local variable binding for performance
    q_get = queue.get
    q_put = queue.put
    counter_next = counter.__next__
    done_set = done.set
    results_get = results.__getitem__

    while True:
        target, dep_results = q_get()
        result = target.build(dep_results)
        results[target.name] = result

        if counter_next() == num_targets - 1:
            done_set()
            return

        # Notify dependents
        for rev_dep in target.rev_deps:
            if rev_dep.in_degree_counter.__next__() == rev_dep.in_degree - 1:
                # Build dep_results for rev_dep from results dict
                # All deps are now in results
                dep_res = {d.name: results_get(d.name) for d in rev_dep.deps}
                q_put((rev_dep, dep_res))


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    num_targets = len(targets)

    done = Event()
    queue = Queue()
    counter = itertools.count()
    results = {name: None for name in targets}

    # Initialize in_degree counters and rev_deps
    for target in targets.values():
        target.in_degree = len(target.deps)
        target.in_degree_counter = itertools.count()
        target.rev_deps = []

    # Build reverse dependency graph
    for target in targets.values():
        for dep in target.deps:
            dep.rev_deps.append(target)

    # Enqueue ready targets (those with no dependencies)
    for target in targets.values():
        if target.in_degree == 0:
            queue.put((target, {}))

    # Start worker threads
    num_workers = cpu_count() or 1
    for _ in range(num_workers):
        Thread(
            target=process_queue,
            args=(queue, counter, num_targets, done, results),
            daemon=True,
        ).start()

    # Wait for completion
    done.wait()

    return results
