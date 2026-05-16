"""Build graph simulator challenge — submission template.

Implement build_all() to build all targets in the graph as fast as possible.

Rules:
- You must call target.build() for each target — do not skip or replace it.
- Every target must be built exactly once.
- A target must not be built until all of its dependencies have completed.
"""

from threading import Condition, Thread
from graph import BuildGraph, Target
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

# --- Derive list ob_item offset from PyObject header size ---
# Find ob_type offset by scanning for the type pointer in a known object
_probe2 = ctypes.c_int64(0)
_type_addr = id(type(_probe2))
_ob_type_off = None
for _off in range(0, 48, 8):
    if ctypes.c_uint64.from_address(id(_probe2) + _off).value == _type_addr:
        _ob_type_off = _off
        break
del _probe2
# PyListObject.ob_item is the first field after PyObject_VAR_HEAD
_list_ob_item_off = _ob_type_off + 16

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
    # ARM64: generate _atomic_dec and _atomic_inc_indexed into one page
    # METH_FASTCALL: x0=self, x1=args, x2=nargs

    # _atomic_dec(counter): atomic sub 1, return True if reached zero
    _code_dec = bytearray()
    _code_dec += _struct.pack('<I', 0xF9400029)                          # ldr x9, [x1]
    _code_dec += _struct.pack('<I', 0x91000129 | (_ctypes_val_off << 10)) # add x9, x9, #offset
    _code_dec += _struct.pack('<I', 0x9280000A)                          # movn x10, #0 (x10 = -1)
    _code_dec += _struct.pack('<I', 0xF8EA012B)                          # ldaddal x10, x11, [x9]
    _code_dec += _struct.pack('<I', 0xF100057F)                          # cmp x11, #1
    _code_dec += _struct.pack('<I', 0x540000C1)                          # b.ne +6
    _code_dec += _arm64_mov_imm64(0, id(True))
    _code_dec += _struct.pack('<I', 0xD65F03C0)                          # ret
    _code_dec += _arm64_mov_imm64(0, id(False))
    _code_dec += _struct.pack('<I', 0xD65F03C0)                          # ret

    # _atomic_inc_indexed(counter, pool): atomic add 1, return pool[old_value]
    # METH_FASTCALL: x0=self, x1=args, x2=nargs
    _code_inc = bytearray()
    _code_inc += _struct.pack('<I', 0xF9400029)                          # ldr x9, [x1]       ; args[0] = counter
    _code_inc += _struct.pack('<I', 0x91000129 | (_ctypes_val_off << 10)) # add x9, x9, #off
    _code_inc += _struct.pack('<I', 0xD280002A)                          # movz x10, #1
    _code_inc += _struct.pack('<I', 0xF8EA012B)                          # ldaddal x10, x11, [x9] ; x11=old, *x9+=1
    _code_inc += _struct.pack('<I', 0xF9400428)                          # ldr x8, [x1, #8]   ; args[1] = pool list
    _code_inc += _struct.pack('<I', 0xF9400108 | ((_list_ob_item_off // 8) << 10))  # ldr x8, [x8, #ob_item]
    _code_inc += _struct.pack('<I', 0xF86B7900)                          # ldr x0, [x8, x11, lsl #3]
    _code_inc += _struct.pack('<I', 0xD65F03C0)                          # ret

    _code_all = bytes(_code_dec) + bytes(_code_inc)

    _libc = ctypes.CDLL(None)
    _c_mmap = _libc.mmap
    _c_mmap.restype = ctypes.c_void_p
    _c_mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                        ctypes.c_int, ctypes.c_int, ctypes.c_long]
    _code_mem = _c_mmap(None, 4096, 0x07, 0x02 | 0x1000 | 0x0800, -1, 0)

    _jit_write_protect = _libc.pthread_jit_write_protect_np
    _jit_write_protect.argtypes = [ctypes.c_int]
    _jit_write_protect.restype = None

    _jit_write_protect(0)
    ctypes.memmove(_code_mem, _code_all, len(_code_all))
    _jit_write_protect(1)

    _icache_invalidate = _libc.sys_icache_invalidate
    _icache_invalidate.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    _icache_invalidate.restype = None
    _icache_invalidate(_code_mem, len(_code_all))

    _dec_method_def = _PyMethodDef(b"_atomic_dec", _code_mem, _METH_FASTCALL, None)
    _atomic_dec_asm = _PyCFunction_NewEx(ctypes.byref(_dec_method_def), None, None)

    _inc_code_addr = _code_mem + len(_code_dec)
    _inc_method_def = _PyMethodDef(b"_atomic_inc_idx", _inc_code_addr, _METH_FASTCALL, None)
    _atomic_inc_indexed = _PyCFunction_NewEx(ctypes.byref(_inc_method_def), None, None)

    class AtomicInt:
        __slots__ = ('_val',)

        def __init__(self, value: int, order: int = 0):
            self._val = ctypes.c_int64(value)

        def dec(self):
            return _atomic_dec_asm(self._val)

    class LockFreeQueue:
        __slots__ = ('_data', '_flags', '_head', '_tail', '_pool')

        def __init__(self, capacity):
            self._data = [None] * capacity
            self._flags = (ctypes.c_int64 * capacity)()
            self._head = ctypes.c_int64(0)
            self._tail = ctypes.c_int64(0)
            self._pool = list(range(capacity))
            for item in self._pool:
                immortalize(item)

        def push(self, item):
            idx = _atomic_inc_indexed(self._tail, self._pool)
            self._data[idx] = item
            self._flags[idx] = 1

        def pop(self):
            idx = _atomic_inc_indexed(self._head, self._pool)
            while self._flags[idx] == 0:
                pass
            return self._data[idx]

else:
    # x86_64: _atomic_dec + _atomic_inc_indexed
    # METH_FASTCALL: rdi=self, rsi=args, rdx=nargs

    # _atomic_dec(counter): lock sub, return True/False
    _code_dec = bytearray()
    _code_dec += b'\x48\x8b\x06'                                   # mov rax, [rsi]
    if _ctypes_val_off < 128:
        _code_dec += b'\xf0\x48\x83\x68'                           # lock sub qword [rax+disp8], imm8
        _code_dec += bytes([_ctypes_val_off])
    else:
        _code_dec += b'\xf0\x48\x83\xa8'                           # lock sub qword [rax+disp32], imm8
        _code_dec += _struct.pack('<i', _ctypes_val_off)
    _code_dec += b'\x01'                                            # imm8 = 1
    _code_dec += b'\x74\x0b'                                        # jz +11
    _code_dec += b'\x48\xb8' + _struct.pack('<Q', id(False))        # movabs rax, Py_False
    _code_dec += b'\xc3'                                            # ret
    _code_dec += b'\x48\xb8' + _struct.pack('<Q', id(True))         # movabs rax, Py_True
    _code_dec += b'\xc3'                                            # ret

    # _atomic_inc_indexed(counter, pool): lock xadd +1, return pool[old]
    _code_inc = bytearray()
    _code_inc += b'\x48\x8b\x06'                                    # mov rax, [rsi]       ; args[0] = counter
    _code_inc += b'\x48\x8b\x4e\x08'                                # mov rcx, [rsi+8]     ; args[1] = pool list
    # lock xadd [rax+ctypes_off], r8  (r8 starts as 1, gets old value)
    _code_inc += b'\x49\xc7\xc0\x01\x00\x00\x00'                    # mov r8, 1
    if _ctypes_val_off < 128:
        _code_inc += b'\xf0\x4c\x0f\xc1\x40'                        # lock xadd [rax+disp8], r8
        _code_inc += bytes([_ctypes_val_off])
    else:
        _code_inc += b'\xf0\x4c\x0f\xc1\x80'                        # lock xadd [rax+disp32], r8
        _code_inc += _struct.pack('<i', _ctypes_val_off)
    # r8 = old value (the index). Load pool.ob_item[r8]
    if _list_ob_item_off < 128:
        _code_inc += b'\x48\x8b\x49'                                # mov rcx, [rcx+disp8]  ; ob_item
        _code_inc += bytes([_list_ob_item_off])
    else:
        _code_inc += b'\x48\x8b\x89'                                # mov rcx, [rcx+disp32] ; ob_item
        _code_inc += _struct.pack('<i', _list_ob_item_off)
    _code_inc += b'\x4a\x8b\x04\xc1'                                # mov rax, [rcx+r8*8]
    _code_inc += b'\xc3'                                             # ret

    _code_all = bytes(_code_dec) + bytes(_code_inc)
    _code_page = _mmap_mod.mmap(-1, len(_code_all),
                                prot=_mmap_mod.PROT_READ | _mmap_mod.PROT_WRITE | _mmap_mod.PROT_EXEC)
    _code_page.write(_code_all)
    _code_base = ctypes.addressof(ctypes.c_char.from_buffer(_code_page))

    _dec_method_def = _PyMethodDef(b"_atomic_dec", _code_base, _METH_FASTCALL, None)
    _atomic_dec_asm = _PyCFunction_NewEx(ctypes.byref(_dec_method_def), None, None)

    _inc_code_addr = _code_base + len(_code_dec)
    _inc_method_def = _PyMethodDef(b"_atomic_inc_idx", _inc_code_addr, _METH_FASTCALL, None)
    _atomic_inc_indexed = _PyCFunction_NewEx(ctypes.byref(_inc_method_def), None, None)

    class AtomicInt:
        __slots__ = ('_val',)

        def __init__(self, value: int, order: int = 0):
            self._val = ctypes.c_int64(value)

        def dec(self):
            return _atomic_dec_asm(self._val)

    class LockFreeQueue:
        __slots__ = ('_data', '_flags', '_head', '_tail', '_pool')

        def __init__(self, capacity):
            self._data = immortalize([None] * capacity)
            self._flags = immortalize((ctypes.c_int64 * capacity)())
            self._head = immortalize(ctypes.c_int64(0))
            self._tail = immortalize(ctypes.c_int64(0))
            self._pool = immortalize(list(range(capacity)))
            for item in self._pool:
                immortalize(item)

        def push(self, item):
            idx = _atomic_inc_indexed(self._tail, self._pool)
            self._data[idx] = item
            self._flags[idx] = 1

        def pop(self):
            idx = _atomic_inc_indexed(self._head, self._pool)
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
    cond = Condition()
    avail = [0]
    for name in targets:
        if in_degree[name] == 0:
            ready.push(name)
            avail[0] += 1

    _SPIN = 50

    def build_target(targets, results, remaining, pending, dep_results_for, dependents, ready, sorted_dep_names, cond) -> None:
        while True:
            spun = False
            for _ in range(_SPIN):
                if avail[0] > 0:
                    with cond:
                        if avail[0] > 0:
                            avail[0] -= 1
                            spun = True
                            break
            if not spun:
                with cond:
                    while avail[0] == 0:
                        cond.wait()
                    avail[0] -= 1
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

            newly_ready = []
            for child in dependents[name]:
                dep_results_for[child][name] = result
                if pending[child].dec():
                    ready.push(child)
                    newly_ready.append(child)

            done = remaining.dec()

            if done or newly_ready:
                with cond:
                    if done:
                        for _ in range(NUM_WORKERS - 1):
                            ready.push(None)
                        avail[0] += NUM_WORKERS - 1
                        cond.notify_all()
                        return
                    avail[0] += len(newly_ready)
                    if len(newly_ready) == 1:
                        cond.notify()
                    else:
                        cond.notify(len(newly_ready))

    threads = []
    for i in range(NUM_WORKERS):
        t = Thread(target=build_target, args=(targets, results, remaining, pending, dep_results_for, dependents, ready, sorted_dep_names, cond))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return results
