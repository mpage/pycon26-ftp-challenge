import queue
import threading

from graph import BuildGraph

N_WORKERS = 24


def build_all(graph: BuildGraph):
    targets = graph.targets
    n = len(targets)

    in_deg = {name: len(target.deps) for name, target in targets.items()}
    downstreams = {name: [] for name in targets}

    initial = []
    for name, target in targets.items():
        if not target.deps:
            initial.append(name)
        else:
            for dep in target.deps:
                downstreams[dep.name].append(name)
    initial.sort(key=lambda name: targets[name].work, reverse=True)

    results = {}
    q = queue.SimpleQueue()
    put = q.put
    for name in initial:
        put(name)

    # Chain fast path
    if q.qsize() == 1 and all(d <= 1 for d in in_deg.values()):
        name = q.get()
        results[name] = targets[name].build({})
        for _ in range(n - 1):
            child = downstreams[name][0]
            results[child] = targets[child].build({name: results[name]})
            name = child
        return results

    lock = threading.Lock()
    cnt = 0

    def worker():
        nonlocal cnt
        get = q.get
        put = q.put
        acquire = lock.acquire
        release = lock.release

        while True:
            name = get()
            if name is None:
                return

            target = targets[name]
            result = target.build({dep.name: results[dep.name] for dep in target.deps})

            acquire()
            results[name] = result
            cnt += 1
            if cnt == n:
                for _ in range(N_WORKERS - 1):
                    put(None)
                release()
                return

            for child in downstreams[name]:
                in_deg[child] -= 1
                if in_deg[child] == 0:
                    put(child)
                    release()
                    acquire()
            release()

    threads = [threading.Thread(target=worker) for _ in range(N_WORKERS - 1)]
    for t in threads:
        t.start()
    worker()
    for t in threads:
        t.join()

    return results
