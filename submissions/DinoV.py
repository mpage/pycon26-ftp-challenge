"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

from threading import Thread
from graph import BuildGraph, Target
from threading import Semaphore
import ctypes
import sys

NUM_WORKERS = 24


import gc
gc.freeze()
gc.disable()


import mmap as _mmap_mod
import struct as _struct

# --- Probe ctypes value offset ---

_probe = ctypes.c_int64(0x4142434445464748)
_probe_bytes = _struct.pack('<q', 0x4142434445464748)
_ctypes_val_off = None
for _off in range(16, 200):
    if ctypes.string_at(id(_probe) + _off, 8) == _probe_bytes:
        _ctypes_val_off = _off
        break
del _probe

# --- PyCFunction wrapper for assembly code ---

class _PyMethodDef(ctypes.Structure):
    _fields_ = [
        ("ml_name", ctypes.c_char_p),
        ("ml_meth", ctypes.c_void_p),
        ("ml_flags", ctypes.c_int),
        ("ml_doc", ctypes.c_char_p),
    ]

_METH_FASTCALL = 0x0080

_PyCFunction_NewEx = ctypes.pythonapi.PyCFunction_NewEx
_PyCFunction_NewEx.restype = ctypes.py_object
_PyCFunction_NewEx.argtypes = [ctypes.POINTER(_PyMethodDef), ctypes.py_object, ctypes.py_object]


def _arm64_mov_imm64(rd, value):
    code = bytearray()
    code += _struct.pack('<I', 0xD2800000 | ((value & 0xFFFF) << 5) | rd)
    code += _struct.pack('<I', 0xF2A00000 | (((value >> 16) & 0xFFFF) << 5) | rd)
    code += _struct.pack('<I', 0xF2C00000 | (((value >> 32) & 0xFFFF) << 5) | rd)
    code += _struct.pack('<I', 0xF2E00000 | (((value >> 48) & 0xFFFF) << 5) | rd)
    return code


if sys.platform == 'darwin':
    _libSystem = ctypes.CDLL('/usr/lib/libSystem.B.dylib')

    _os_atomic_add = _libSystem.OSAtomicAdd64Barrier
    _os_atomic_add.argtypes = [ctypes.c_int64, ctypes.POINTER(ctypes.c_int64)]
    _os_atomic_add.restype = ctypes.c_int64

    # ARM64 machine code for atomic dec returning Py_True/Py_False
    # vectorcall: x0=callable, x1=args, x2=nargsf, x3=kwnames
    _code = bytearray()
    _code += _struct.pack('<I', 0xF9400029)                          # ldr x9, [x1]
    _code += _struct.pack('<I', 0x91000129 | (_ctypes_val_off << 10)) # add x9, x9, #offset
    _code += _struct.pack('<I', 0x9280000A)                          # movn x10, #0 (x10 = -1)
    _code += _struct.pack('<I', 0xF8EA012B)                          # ldaddal x10, x11, [x9]
    _code += _struct.pack('<I', 0xF100057F)                          # cmp x11, #1
    _code += _struct.pack('<I', 0x540000C1)                          # b.ne +6 (.not_zero)
    _code += _arm64_mov_imm64(0, id(True))                           # movz/movk x0, Py_True
    _code += _struct.pack('<I', 0xD65F03C0)                          # ret
    _code += _arm64_mov_imm64(0, id(False))                          # movz/movk x0, Py_False
    _code += _struct.pack('<I', 0xD65F03C0)                          # ret

    _libc = ctypes.CDLL(None)
    _c_mmap = _libc.mmap
    _c_mmap.restype = ctypes.c_void_p
    _c_mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                        ctypes.c_int, ctypes.c_int, ctypes.c_long]
    _MAP_JIT = 0x0800
    _MAP_PRIVATE = 0x02
    _MAP_ANON = 0x1000
    _code_mem = _c_mmap(None, 4096, 0x07, _MAP_PRIVATE | _MAP_ANON | _MAP_JIT, -1, 0)

    _jit_write_protect = _libc.pthread_jit_write_protect_np
    _jit_write_protect.argtypes = [ctypes.c_int]
    _jit_write_protect.restype = None

    _jit_write_protect(0)
    ctypes.memmove(_code_mem, bytes(_code), len(_code))
    _jit_write_protect(1)

    _icache_invalidate = _libc.sys_icache_invalidate
    _icache_invalidate.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    _icache_invalidate.restype = None
    _icache_invalidate(_code_mem, len(_code))

    _dec_method_def = _PyMethodDef(b"_atomic_dec", _code_mem, _METH_FASTCALL, None)
    _atomic_dec_asm = _PyCFunction_NewEx(ctypes.byref(_dec_method_def), None, None)

    class AtomicInt:
        __slots__ = ('_val',)

        def __init__(self, value: int, order: int = 0):
            self._val = ctypes.c_int64(value)

        def dec(self):
            return _atomic_dec_asm(self._val)

    class LockFreeQueue:
        __slots__ = ('_data', '_flags', '_head', '_head_ref', '_tail', '_tail_ref')

        def __init__(self, capacity):
            self._data = [None] * capacity
            self._flags = (ctypes.c_int64 * capacity)()
            self._head = ctypes.c_int64(0)
            self._tail = ctypes.c_int64(0)
            self._head_ref = ctypes.byref(self._head)
            self._tail_ref = ctypes.byref(self._tail)

        def push(self, item):
            idx = _os_atomic_add(1, self._tail_ref) - 1
            self._data[idx] = item
            self._flags[idx] = 1

        def pop(self):
            idx = _os_atomic_add(1, self._head_ref) - 1
            while self._flags[idx] == 0:
                pass
            return self._data[idx]

else:
    _libatomic = ctypes.CDLL('libatomic.so.1')

    _atomic_fetch_add = _libatomic.__atomic_fetch_add_8
    _atomic_fetch_add.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.c_int64, ctypes.c_int]
    _atomic_fetch_add.restype = ctypes.c_int64

    _RELAXED = 0

    # x86_64 machine code for atomic dec returning Py_True/Py_False
    # vectorcall: rdi=callable, rsi=args, rdx=nargsf, rcx=kwnames
    _code = bytearray()
    _code += b'\x48\x8b\x06'                                   # mov rax, [rsi]
    if _ctypes_val_off < 128:
        _code += b'\xf0\x48\x83\x68'                           # lock sub qword [rax+disp8], imm8
        _code += bytes([_ctypes_val_off])
    else:
        _code += b'\xf0\x48\x83\xa8'                           # lock sub qword [rax+disp32], imm8
        _code += _struct.pack('<i', _ctypes_val_off)
    _code += b'\x01'                                            # imm8 = 1
    _code += b'\x74\x0b'                                        # jz +11
    _code += b'\x48\xb8' + _struct.pack('<Q', id(False))        # movabs rax, Py_False
    _code += b'\xc3'                                            # ret
    _code += b'\x48\xb8' + _struct.pack('<Q', id(True))         # movabs rax, Py_True
    _code += b'\xc3'                                            # ret

    _code_page = _mmap_mod.mmap(-1, len(_code),
                                prot=_mmap_mod.PROT_READ | _mmap_mod.PROT_WRITE | _mmap_mod.PROT_EXEC)
    _code_page.write(bytes(_code))
    _code_addr = ctypes.addressof(ctypes.c_char.from_buffer(_code_page))

    _dec_method_def = _PyMethodDef(b"_atomic_dec", _code_addr, _METH_FASTCALL, None)
    _atomic_dec_asm = _PyCFunction_NewEx(ctypes.byref(_dec_method_def), None, None)

    class AtomicInt:
        __slots__ = ('_val',)

        def __init__(self, value: int, order: int = 0):
            self._val = ctypes.c_int64(value)

        def dec(self):
            return _atomic_dec_asm(self._val)

    class LockFreeQueue:
        __slots__ = ('_data', '_flags', '_head', '_head_ref', '_tail', '_tail_ref')

        def __init__(self, capacity):
            self._data = immortalize([None] * capacity)
            self._flags = immortalize((ctypes.c_int64 * capacity)())
            self._head = immortalize(ctypes.c_int64(0))
            self._tail = immortalize(ctypes.c_int64(0))
            self._head_ref = immortalize(ctypes.byref(self._head))
            self._tail_ref = immortalize(ctypes.byref(self._tail))

        def push(self, item):
            idx = _atomic_fetch_add(self._tail_ref, 1, _RELAXED)
            self._data[idx] = item
            self._flags[idx] = 1

        def pop(self):
            idx = _atomic_fetch_add(self._head_ref, 1, _RELAXED)
            while self._flags[idx] == 0:
                pass
            return self._data[idx]


def immortalize[T](obj: T) -> T:
    """Make a Python object immortal on 3.14 free-threaded builds."""
    # ob_refcnt is the first field in PyObject, at offset 0
    # On free-threaded builds, ob_refcnt_split[0] (lower 32 bits) must be UINT32_MAX
    addr = id(obj) + 12
    ctypes.c_uint32.from_address(addr).value = 0xFFFFFFFF
    assert sys._is_immortal(obj)
    return obj


def build_all(graph: BuildGraph) -> dict[str, bytes]:
    """Build all targets in the graph, respecting dependency order.

    Args:
        graph: The build graph to execute.

    Returns:
        A dict mapping target name to its build result (bytes).
    """
    targets = graph.targets
    if not targets:
        return {}

    dependents: dict[str, list[str]] = immortalize({name: [] for name in targets})
    in_degree: dict[str, int] = immortalize({name: 0 for name in targets})

    for name, target in targets.items():
        in_degree[name] = len(target.deps)
        immortalize(name)
        immortalize(target)
        for dep in target.deps:
            immortalize(dep)
            dependents[dep.name].append(name)

    results: dict[str, bytes] = immortalize({})
    remaining = immortalize(AtomicInt(len(targets), 0))

    is_chain = all(len(t.deps) <= 1 for t in targets.values())
    if is_chain:
        order = []
        for name in targets:
            if in_degree[name] == 0:
                order.append(name)
        while len(order) < len(targets):
            name = order[-1]
            for child in dependents[name]:
                if in_degree[child] == 1:
                    order.append(child)
                    break
        dep_results = {}
        for name in order:
            results[name] = targets[name].build(dep_results)
            dep_results.clear()
            dep_results[name] = results[name]
        return results

    # Pre-allocate dep_results dicts and atomic pending counts
    dep_results_for: dict[str, dict[str, bytes]] = immortalize({})
    sorted_dep_names: dict[str, list[str]] = immortalize({})
    pending: dict[str, AtomicInt] = immortalize({})
    for name, target in targets.items():
        if target.deps:
            dep_results_for[name] = immortalize({})
            sorted_dep_names[name] = immortalize(sorted(dep.name for dep in target.deps))
            pending[name] = immortalize(AtomicInt(len(target.deps)))

    ready = immortalize(LockFreeQueue(len(targets) + NUM_WORKERS))
    sem = immortalize(Semaphore(0))
    for name in targets:
        if in_degree[name] == 0:
            ready.push(name)
            sem.release()

    def build_target(targets, results, remaining, pending, dep_results_for, dependents, ready, sem, sorted_dep_names) -> None:
        while True:
            sem.acquire()
            name = ready.pop()
            if name is None:
                return
            target = targets[name]
            if name in sorted_dep_names:
                accum = dep_results_for[name]
                dep_results = {dn: accum[dn] for dn in sorted_dep_names[name]}
            else:
                dep_results = ()
            result = target.build(dep_results)
            results[name] = result

            for child in dependents[name]:
                dep_results_for[child][name] = result
                if pending[child].dec():
                    ready.push(child)
                    sem.release()

            if remaining.dec():
                for _ in range(NUM_WORKERS - 1):
                    ready.push(None)
                    sem.release()
                return

    threads = []
    for i in range(NUM_WORKERS):
        t = Thread(target=build_target, args=(targets,results, remaining, pending, dep_results_for, dependents, ready, sem, sorted_dep_names))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return results
