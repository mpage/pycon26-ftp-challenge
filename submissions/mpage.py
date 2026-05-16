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
# AtomicInt: a class with a single __slots__ field storing a raw int64.
# atomic_fetch_sub: a Python function whose vectorcall is patched with
# hand-assembled x86-64 machine code implementing lock xadd.
# ---------------------------------------------------------------------------

class AtomicInt:
    __slots__ = ('value',)


def _setup_atomics():
    """Patch AtomicInt's slot to store raw int64 and build atomic_fetch_sub."""

    # -- Step 1: Find PyMemberDef for 'value' slot and patch to T_LONGLONG --
    desc = AtomicInt.__dict__['value']
    desc_addr = id(desc)

    # CPython 3.14t: PyObject_HEAD(32) + d_type(8) + d_name(8) + d_qualname(8) = desc+56
    d_member = ctypes.c_void_p.from_address(desc_addr + 56).value

    slot_offset = ctypes.c_ssize_t.from_address(d_member + 16).value

    # Patch type from T_OBJECT_EX (16) to T_LONGLONG (17)
    ctypes.c_int.from_address(d_member + 8).value = 17

    # Now AtomicInt().__init__ can do self.value = n to write raw int64

    # -- Step 2: Get C function addresses --
    pylong_aslong = ctypes.cast(ctypes.pythonapi.PyLong_AsLong, ctypes.c_void_p).value
    pylong_fromlong = ctypes.cast(ctypes.pythonapi.PyLong_FromLong, ctypes.c_void_p).value

    # CPython 3.14t: vectorcall is at offset 152 in PyFunctionObject
    vc_offset = 152

    # -- Step 4: Assemble x86-64 machine code for atomic_fetch_sub --
    #
    # vectorcall(callable=rdi, args=rsi, nargsf=rdx, kwnames=rcx)
    #   args[0] = AtomicInt, args[1] = Python int amount
    #
    # push rbx                         ; save callee-saved, align stack to 16
    # mov  rbx, [rsi]                  ; rbx = args[0] (AtomicInt object)
    # mov  rdi, [rsi + 8]              ; rdi = args[1] (Python int amount)
    # movabs rax, <PyLong_AsLong>
    # call rax                         ; rax = C long value of amount
    # neg  rax                         ; negate (xadd adds, we want sub)
    # lock xadd [rbx + offset], rax    ; atomic fetch-sub; old value -> rax
    # mov  rdi, rax                    ; arg for PyLong_FromLong
    # movabs rax, <PyLong_FromLong>
    # call rax                         ; rax = new Python int (old value)
    # pop  rbx
    # ret

    code = bytearray()
    code += b'\x53'                                                     # push rbx
    code += b'\x48\x8B\x1E'                                            # mov rbx, [rsi]
    code += b'\x48\x8B\x7E\x08'                                        # mov rdi, [rsi+8]
    code += b'\x48\xB8' + pylong_aslong.to_bytes(8, 'little')          # movabs rax, addr
    code += b'\xFF\xD0'                                                 # call rax
    code += b'\x48\xF7\xD8'                                            # neg rax
    if slot_offset < 128:
        code += b'\xF0\x48\x0F\xC1\x43' + bytes([slot_offset])         # lock xadd [rbx+disp8], rax
    else:
        code += b'\xF0\x48\x0F\xC1\x83' + slot_offset.to_bytes(4, 'little')  # lock xadd [rbx+disp32], rax
    code += b'\x48\x89\xC7'                                            # mov rdi, rax
    code += b'\x48\xB8' + pylong_fromlong.to_bytes(8, 'little')        # movabs rax, addr
    code += b'\xFF\xD0'                                                 # call rax
    code += b'\x5B'                                                     # pop rbx
    code += b'\xC3'                                                     # ret

    # -- Step 5: Allocate executable memory and write code --
    exec_page = mmap.mmap(-1, mmap.PAGESIZE,
                          prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
    exec_page.write(bytes(code))
    exec_addr = ctypes.addressof(ctypes.c_char.from_buffer(exec_page))

    # -- Step 6: Create Python function and patch its vectorcall --
    def atomic_fetch_sub(atom, amount):
        raise RuntimeError("vectorcall not patched")

    ctypes.c_void_p.from_address(id(atomic_fetch_sub) + vc_offset).value = exec_addr

    return atomic_fetch_sub, exec_page


atomic_fetch_sub, _exec_page = _setup_atomics()


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
        target.in_degree = AtomicInt()
        target.in_degree.value = len(target.deps)
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
                if atomic_fetch_sub(dep.in_degree, 1) == 1:
                    next_target = dep
            target = next_target

        return results

    # Parallel path — lock-free using atomic_fetch_sub
    remaining = AtomicInt()
    remaining.value = num_targets
    queue: SimpleQueue[Target | None] = SimpleQueue()

    for target in targets.values():
        if target.in_degree.value == 0:
            queue.put(target)

    _fetch_sub = atomic_fetch_sub

    def worker():
        _results = results
        _dependents = dependents
        _queue = queue
        _NW = NUM_WORKERS
        _fsub = _fetch_sub
        _remaining = remaining

        target = _queue.get()
        while target is not _SENTINEL:
            tidx = target.index
            dep_results = {d.name: _results[d.index] for d in target.deps}
            _results[tidx] = target.build(dep_results)

            next_target = None
            for dep in _dependents[tidx]:
                if _fsub(dep.in_degree, 1) == 1:
                    if next_target is None:
                        next_target = dep
                    else:
                        _queue.put(dep)

            if _fsub(_remaining, 1) == 1:
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
