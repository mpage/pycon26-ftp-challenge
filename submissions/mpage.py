"""Build graph simulator challenge — parallel scheduler using threads."""

import ctypes
import mmap
import os
import threading
from queue import SimpleQueue

from graph import BuildGraph, Target


NUM_WORKERS = os.cpu_count() or 24
_SENTINEL = None


# ---------------------------------------------------------------------------
# AtomicInt with a raw uint64 slot and a fetch_sub method whose vectorcall
# is patched with x86-64 assembly: lock xadd, cmp, cmovz → True/False.
# ---------------------------------------------------------------------------

class AtomicInt:
    __slots__ = ('value',)

    def __init__(self, val=0):
        self.value = val

    def fetch_sub(self):
        raise RuntimeError("vectorcall not patched")


def _setup_atomics():
    # Patch 'value' slot from T_OBJECT_EX to T_LONGLONG so it stores raw int64.
    # CPython 3.14t: d_member pointer is at descriptor + 56.
    desc = AtomicInt.__dict__['value']
    d_member = ctypes.c_void_p.from_address(id(desc) + 56).value
    slot_offset = ctypes.c_ssize_t.from_address(d_member + 16).value
    ctypes.c_int.from_address(d_member + 8).value = 17  # T_LONGLONG

    # Get addresses of Py_True and Py_False (immortal, no refcounting needed).
    py_false = id(False)
    py_true = id(True)

    # Assemble fetch_sub: decrements [self + slot_offset] by 1,
    # returns True if old value was 1 (counter hit zero), False otherwise.
    #
    # vectorcall(callable=rdi, args=rsi, nargsf=rdx, kwnames=rcx)
    #   args[0] = self (AtomicInt)
    #
    #   mov  rax, [rsi]                ; rax = self
    #   mov  rcx, -1                   ; add -1 = subtract 1
    #   lock xadd [rax + offset], rcx  ; rcx = old value, [slot] -= 1
    #   cmp  rcx, 1                    ; was old value 1?
    #   movabs rax, <Py_False>
    #   movabs rcx, <Py_True>
    #   cmovz rax, rcx                 ; if old == 1: return True
    #   ret
    code = bytearray()
    code += b'\x48\x8B\x06'                                     # mov rax, [rsi]
    code += b'\x48\xC7\xC1\xFF\xFF\xFF\xFF'                     # mov rcx, -1
    if slot_offset < 128:
        code += b'\xF0\x48\x0F\xC1\x48' + bytes([slot_offset])  # lock xadd [rax+disp8], rcx
    else:
        code += b'\xF0\x48\x0F\xC1\x88' + slot_offset.to_bytes(4, 'little')
    code += b'\x48\x83\xF9\x01'                                 # cmp rcx, 1
    code += b'\x48\xB8' + py_false.to_bytes(8, 'little')        # movabs rax, Py_False
    code += b'\x48\xB9' + py_true.to_bytes(8, 'little')         # movabs rcx, Py_True
    code += b'\x48\x0F\x44\xC1'                                 # cmovz rax, rcx
    code += b'\xC3'                                              # ret

    exec_page = mmap.mmap(-1, mmap.PAGESIZE,
                          prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
    exec_page.write(bytes(code))
    exec_addr = ctypes.addressof(ctypes.c_char.from_buffer(exec_page))

    # Patch fetch_sub's vectorcall (offset 152 in PyFunctionObject on 3.14t).
    func = AtomicInt.__dict__['fetch_sub']
    ctypes.c_void_p.from_address(id(func) + 152).value = exec_addr

    return exec_page


_exec_page = _setup_atomics()


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def build_all(graph: BuildGraph) -> dict[str, bytes]:
    targets = graph.targets
    num_targets = len(targets)

    idx = 0
    for target in targets.values():
        target.index = idx
        idx += 1

    results: list[bytes | None] = [None] * num_targets

    dependents: list[list[Target]] = [[] for _ in range(num_targets)]
    num_sources = 0
    for target in targets.values():
        target.in_degree = AtomicInt(len(target.deps))
        if target.in_degree.value == 0:
            num_sources += 1
        for dep in target.deps:
            dependents[dep.index].append(target)

    max_fan_out = max((len(d) for d in dependents), default=0)

    # Sort dependents heaviest-first for better load balancing
    for dep_list in dependents:
        if len(dep_list) > 1:
            dep_list.sort(key=lambda t: t.work, reverse=True)

    # Sequential fast path for chain-like graphs
    if max_fan_out <= 1 and num_sources <= 1:
        target = None
        for t in targets.values():
            if t.in_degree.value == 0:
                target = t
                break

        while target is not None:
            tidx = target.index
            dep_results = {d.name: results[d.index] for d in target.deps}
            results[tidx] = target.build(dep_results)
            next_target = None
            for dep in dependents[tidx]:
                if dep.in_degree.fetch_sub():
                    next_target = dep
            target = next_target

        return results

    # Parallel path — lock-free using atomic fetch_sub
    remaining = AtomicInt(num_targets)
    queue: SimpleQueue[Target | None] = SimpleQueue()

    for target in targets.values():
        if target.in_degree.value == 0:
            queue.put(target)

    def worker():
        _results = results
        _dependents = dependents
        _queue = queue
        _NW = NUM_WORKERS
        _remaining = remaining

        target = _queue.get()
        while target is not _SENTINEL:
            tidx = target.index
            dep_results = {d.name: _results[d.index] for d in target.deps}
            _results[tidx] = target.build(dep_results)

            next_target = None
            for dep in _dependents[tidx]:
                if dep.in_degree.fetch_sub():
                    if next_target is None:
                        next_target = dep
                    else:
                        _queue.put(dep)

            if _remaining.fetch_sub():
                for _ in range(_NW):
                    _queue.put(_SENTINEL)

            if next_target is not None:
                target = next_target
            else:
                target = _queue.get()

    for _ in range(NUM_WORKERS - 1):
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    worker()

    return results
