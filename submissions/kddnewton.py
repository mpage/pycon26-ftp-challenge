from queue import SimpleQueue
from threading import Condition, Event, Lock, Thread

from graph import BuildGraph


class _BuildGraphState:
    __slots__ = ("remaining", "done", "queue")

    def __init__(self, remaining, done, queue):
        self.remaining = remaining
        self.done = done
        self.queue = queue


_NWORKERS = 24
_lock = Lock()
_condition = Condition()
_results = [None] * 20000

_current_state = None
_current_state_id = 0


def _worker(condition, lock, results):
    state_id = 0

    while True:
        with condition:
            while state_id == _current_state_id:
                condition.wait()
            state_id = _current_state_id
            state = _current_state

        queue = state.queue
        target = queue.get()
        completed = False

        while target is not None:
            results[target._id] = target.build(
                {dep.name: results[dep._id] for dep in target.deps}
            )

            ready = []

            with lock:
                if state.remaining == 1:
                    state.done.set()
                    completed = True
                    break

                state.remaining -= 1
                for dependent in target._dependents:
                    if dependent._in_degree == 1:
                        ready.append(dependent)
                    else:
                        dependent._in_degree -= 1

            if ready:
                if len(ready) >= _NWORKERS:
                    ready.sort(key=lambda d: d.work)
                target = ready.pop()
                for dependent in reversed(ready):
                    queue.put(dependent)
            else:
                target = queue.get()

        if completed:
            for _ in range(_NWORKERS):
                queue.put(None)


for _ in range(_NWORKERS):
    Thread(target=_worker, args=(_condition, _lock, _results), daemon=True).start()


def build_all(graph: BuildGraph):
    global _current_state_id, _current_state

    state = _BuildGraphState(
        len(targets := graph.targets),
        done := Event(),
        queue := SimpleQueue(),
    )

    for target in targets.values():
        target._dependents = []

    has_parallelism = False
    for id, target in enumerate(targets.values()):
        target._id = id
        if deps := target.deps:
            target._in_degree = len(deps)
            for dep in deps:
                if not has_parallelism and dep._dependents:
                    has_parallelism = True
                dep._dependents.append(target)
        else:
            target._in_degree = 0
            if not has_parallelism and not queue.empty():
                has_parallelism = True
            queue.put(target)

    if not has_parallelism:
        target = queue.get()
        (results := _results)[target._id] = target.build({})

        for _ in range(len(targets) - 1):
            dependent = target._dependents[0]
            results[dependent._id] = dependent.build({target.name: results[target._id]})
            target = dependent
    else:
        with _condition:
            _current_state = state
            _current_state_id += 1
            _condition.notify_all()

        done.wait()
