import itertools

from collections.abc import Iterator
from graph import BuildGraph, Target
from queue import Queue
from os import cpu_count
from threading import Event, Thread


def process_queue(
    done: Event,
    queue: Queue,
    counter: Iterator[int],
    num_targets: int,
) -> None:
    while True:
        target = queue.get()
        result = target.build(target.dep_results)
        target.result = result

        if next(counter) == num_targets - 1:
            done.set()
            return

        for rev_dep in target.rev_deps:
            rev_dep.dep_results[target.name] = result

            value = next(rev_dep.in_degree_counter)
            if value == rev_dep.in_degree - 1:
                queue.put(rev_dep)


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    num_targets = len(graph.targets)
    done = Event()
    queue = Queue()
    counter = itertools.count()

    # Start up workers.
    num_workers = cpu_count() or 1
    workers = []
    for _ in range(num_workers):
        workers.append(
            Thread(
                target=process_queue,
                args=(done, queue, counter, num_targets),
                daemon=True,
            )
        )
    for worker in workers:
        worker.start()

    # Initialize the queue with zero-dep targets.
    for target in graph.targets.values():
        target.dep_results = {dep.name: None for dep in target.deps}
        target.in_degree = len(target.deps)
        target.in_degree_counter = itertools.count()
        if not hasattr(target, "rev_deps"):
            target.rev_deps = []
        for dep in target.deps:
            if not hasattr(dep, "rev_deps"):
                dep.rev_deps = []
            dep.rev_deps.append(target)
    for target in graph.targets.values():
        if target.in_degree == 0:
            queue.put(target)

    # Wait for workers to finish, but don't bother joining them, they're daemon
    # threads.
    done.wait()

    return {name: target.result for name, target in graph.targets.items()}
