"""Parallel build with chain-aware scheduling."""

from __future__ import annotations

from queue import SimpleQueue
from threading import Thread

from graph import BuildGraph, Target

THREADS = 23


def _build_chains(
    graph: BuildGraph,
) -> tuple[dict[str, tuple[Target, ...]], dict[str, list[Target]]]:
    """Build chain_of (target name -> chain) and dependents maps."""
    dependents: dict[str, list[Target]] = {name: [] for name in graph.targets}
    for name, target in graph.targets.items():
        for dep in target.deps:
            dependents[dep.name].append(target)

    def is_chain_continuation(target: Target) -> bool:
        if len(target.deps) != 1:
            return False
        return len(dependents[target.deps[0].name]) == 1

    chain_of: dict[str, tuple[Target, ...]] = {}
    for start in graph.targets.values():
        if is_chain_continuation(start):
            continue
        chain: list[Target] = [start]
        current = start
        while len(dependents[current.name]) == 1:
            nxt = dependents[current.name][0]
            if is_chain_continuation(nxt):
                chain.append(nxt)
                current = nxt
            else:
                break
        t = tuple(chain)
        for target in chain:
            chain_of[target.name] = t

    return chain_of, dependents


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    chain_of, dependents = _build_chains(graph)

    # Unique chains: entries where the target is its chain's start
    chains = [chain for name, chain in chain_of.items() if chain[0].name == name]

    # in_degree per chain keyed by chain[0].name — counts unbuilt external deps of chain[0]
    chain_in_degree: dict[str, int] = {chain[0].name: len(chain[0].deps) for chain in chains}

    # work_queue items: (chain, inputs) where inputs is a tuple of bytes for chain[0].deps
    # built_queue items: (last_name, result) — scheduler owns all result storage
    work_queue: SimpleQueue = SimpleQueue()
    built_queue: SimpleQueue = SimpleQueue()

    # results is owned solely by the scheduler thread — no concurrent access
    results: dict[str, bytes] = {}

    def builder() -> None:
        while True:
            item = work_queue.get()
            if item is None:
                break
            chain, inputs = item
            dep_results = {dep.name: inp for dep, inp in zip(chain[0].deps, inputs)}
            result = chain[0].build(dep_results)
            for target in chain[1:]:
                result = target.build({target.deps[0].name: result})
            built_queue.put((chain[-1].name, result))

    def scheduler() -> None:
        # Seed leaves — chains whose first target has no external deps
        for chain in chains:
            if chain_in_degree[chain[0].name] == 0:
                work_queue.put((chain, ()))

        built = 0
        while built < len(chains):
            last_name, result = built_queue.get()
            results[last_name] = result
            built += 1
            for dependent in dependents[last_name]:
                dep_chain = chain_of[dependent.name]
                chain_in_degree[dep_chain[0].name] -= 1
                if chain_in_degree[dep_chain[0].name] == 0:
                    inputs = tuple(results[d.name] for d in dep_chain[0].deps)
                    work_queue.put((dep_chain, inputs))

        for _ in range(THREADS):
            work_queue.put(None)

    threads = [Thread(target=builder) for _ in range(THREADS)]
    sched = Thread(target=scheduler)
    for t in threads:
        t.start()
    sched.start()

    sched.join()
    for t in threads:
        t.join()

    return results
