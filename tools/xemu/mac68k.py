"""A small 68k Macintosh for running the stacks' own XCMDs and XFCNs.

The externals inside old stacks are 68000/68020 machine code that calls
two things: HyperCard, through the callback entry point of the XCMD
parameter block, and the Mac Toolbox, through A-line traps. The CPU is
Musashi (MIT license), compiled into _hcxcpu.so from cpu/. This module
gives it a memory, a heap with handles, a stack, the low-memory globals
and QuickDraw globals the code expects, and stops the CPU at a few "stub"
addresses where Python takes over: a trap, a callback, the end of the
external, or a crash. The traps themselves are in toolbox.py and the
callbacks in xcmdrun.py.
"""

import ctypes
import os
import struct
import sys

MEM_SIZE = 0x01000000

# stubs: one NOP each; reaching any of them stops the CPU
STUB_LO = 0x3000
ALINE_STUB = 0x3000
FLINE_STUB = 0x3002
FAULT_STUB = 0x3004
CALLBACK_STUB = 0x3010
EXIT_STUB = 0x3020
STUB_HI = 0x3040

# trap glue: [trap word, RTS] for every trap, for GetTrapAddress
TOOL_GLUE = 0x4000          # 0x400 traps * 4 bytes
OS_GLUE = 0x5000            # 0x100 traps * 4 bytes

A5_WORLD = 0x9000          # A5; (A5) points to the QuickDraw globals' thePort
QD_THEPORT = 0x8F00
HEAP_LO = 0x00020000
HEAP_HI = 0x00E00000
STACK_TOP = 0x00F00000
MP_LO = 0x00010000          # master pointers for handles
MP_HI = 0x0001FFF0

# error codes
noErr = 0
memFullErr = -108
nilHandleErr = -109
memWZErr = -111
resNotFound = -192

_LIB = None


def _lib_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    env = os.environ.get("HCX_CPU_LIB")
    if env:
        yield env
    yield os.path.join(here, "_hcxcpu.so")


def library():
    """The compiled CPU, or None if it isn't there (then externals that
    have no rewrite in xcmds.py are skipped, as before)."""
    global _LIB
    if _LIB is None:
        _LIB = False
        for p in _lib_paths():
            if os.path.exists(p):
                try:
                    lib = ctypes.CDLL(p)
                except OSError:
                    continue
                lib.hcx_mem.restype = ctypes.c_void_p
                lib.hcx_mem_size.restype = ctypes.c_uint
                lib.hcx_run.restype = ctypes.c_uint
                lib.hcx_run.argtypes = [ctypes.c_int]
                lib.hcx_icount.restype = ctypes.c_ulonglong
                lib.m68k_get_reg.restype = ctypes.c_uint
                lib.m68k_get_reg.argtypes = [ctypes.c_void_p, ctypes.c_int]
                lib.m68k_set_reg.argtypes = [ctypes.c_int, ctypes.c_uint]
                lib.hcx_set_stubs.argtypes = [ctypes.c_uint, ctypes.c_uint]
                lib.hcx_disasm.argtypes = [ctypes.c_uint, ctypes.c_char_p]
                if hasattr(lib, "hcx_history"):
                    lib.hcx_history.argtypes = [ctypes.c_void_p]
                lib.hcx_disasm.restype = ctypes.c_uint
                _LIB = lib
                break
    return _LIB or None


def available():
    return library() is not None


class EmuError(Exception):
    pass


class Unimplemented(EmuError):
    """The external called a trap or callback HCX doesn't have."""

    def __init__(self, what):
        super().__init__(what)
        self.what = what


# Musashi register numbers
D0, A0, PC, SR, SP = 0, 8, 16, 17, 18


class Machine:
    def __init__(self):
        self.lib = library()
        if self.lib is None:
            raise EmuError("68k emulator not available")
        self.lib.hcx_init()
        addr = self.lib.hcx_mem()
        self.mem = (ctypes.c_ubyte * MEM_SIZE).from_address(addr)
        self.mv = memoryview(self.mem).cast("B")
        self.lib.hcx_set_stubs(STUB_LO, STUB_HI)
        for a in range(STUB_LO, STUB_HI, 2):
            self.w16(a, 0x4E71)                     # NOP
        for v in range(2, 64):
            self.w32(v * 4, FAULT_STUB)
        self.w32(10 * 4, ALINE_STUB)
        self.w32(11 * 4, FLINE_STUB)
        for n in range(0x400):
            self.w16(TOOL_GLUE + 4 * n, 0xA800 | n)
            self.w16(TOOL_GLUE + 4 * n + 2, 0x4E75)
        for n in range(0x100):
            self.w16(OS_GLUE + 4 * n, 0xA000 | n)
            self.w16(OS_GLUE + 4 * n + 2, 0x4E75)
        self.w32(0, STACK_TOP)
        self.w32(4, EXIT_STUB)
        self.lib.m68k_pulse_reset()
        self.heap = Heap(self)
        self._low_memory()

    # -- registers ----------------------------------------------------------------------------
    def reg(self, n):
        return self.lib.m68k_get_reg(None, n)

    def set_reg(self, n, v):
        self.lib.m68k_set_reg(n, v & 0xFFFFFFFF)

    def d(self, n):
        return self.reg(D0 + n)

    def a(self, n):
        return self.reg(A0 + n)

    def set_d(self, n, v):
        self.set_reg(D0 + n, v)

    def set_a(self, n, v):
        self.set_reg(A0 + n, v)

    @property
    def sp(self):
        return self.reg(A0 + 7)

    @sp.setter
    def sp(self, v):
        self.set_reg(A0 + 7, v)

    # -- memory -----------------------------------------------------------------------------
    def r8(self, a):
        return self.mem[a & 0xFFFFFF]

    def r16(self, a):
        a &= 0xFFFFFF
        return (self.mem[a] << 8) | self.mem[(a + 1) & 0xFFFFFF]

    def r32(self, a):
        return (self.r16(a) << 16) | self.r16(a + 2)

    def s16(self, a):
        v = self.r16(a)
        return v - 0x10000 if v & 0x8000 else v

    def s32(self, a):
        v = self.r32(a)
        return v - 0x100000000 if v & 0x80000000 else v

    def w8(self, a, v):
        self.mem[a & 0xFFFFFF] = v & 0xFF

    def w16(self, a, v):
        a &= 0xFFFFFF
        self.mem[a] = (v >> 8) & 0xFF
        self.mem[a + 1] = v & 0xFF

    def w32(self, a, v):
        self.w16(a, (v >> 16) & 0xFFFF)
        self.w16(a + 2, v & 0xFFFF)

    def rbytes(self, a, n):
        a &= 0xFFFFFF
        return bytes(self.mv[a:a + max(0, n)])

    def wbytes(self, a, data):
        a &= 0xFFFFFF
        self.mv[a:a + len(data)] = data

    def cstr(self, a, limit=1 << 20):
        a &= 0xFFFFFF
        raw = bytes(self.mv[a:min(MEM_SIZE, a + limit)])
        i = raw.find(b"\0")
        return raw[:i] if i >= 0 else raw

    def pstr(self, a):
        n = self.r8(a)
        return self.rbytes(a + 1, n)

    def wpstr(self, a, data, maxlen=255):
        data = data[:maxlen]
        self.w8(a, len(data))
        self.wbytes(a + 1, data)

    # -- stack --------------------------------------------------------------------------------
    def push16(self, v):
        self.sp = self.sp - 2
        self.w16(self.sp, v)

    def push32(self, v):
        self.sp = self.sp - 4
        self.w32(self.sp, v)

    def pop16(self):
        v = self.r16(self.sp)
        self.sp = self.sp + 2
        return v

    def pop32(self):
        v = self.r32(self.sp)
        self.sp = self.sp + 4
        return v

    def pops16(self):
        v = self.pop16()
        return v - 0x10000 if v & 0x8000 else v

    def pops32(self):
        v = self.pop32()
        return v - 0x100000000 if v & 0x80000000 else v

    # -- low memory -----------------------------------------------------------------------------
    def set_mem_err(self, err):
        self.w16(0x220, err & 0xFFFF)

    def _low_memory(self):
        w8, w16, w32 = self.w8, self.w16, self.w32
        w16(0x28E, 0x3FFF)          # ROM85: 128K ROM and Color QuickDraw
        w8(0x12F, 2)                # CPUFlag: 68020
        w16(0x15A, 0x0755)          # SysVersion 7.5.5
        w16(0x3F6, 94)              # FSFCBLen > 0: HFS is there
        w32(0x16A, 0)               # Ticks
        w8(0xCB2, 0)                # MMU32Bit: 24-bit mode
        w16(0x102, 72)              # ScrVRes
        w16(0x104, 72)              # ScrHRes
        w16(0x984, 3)               # ApFontID: Geneva
        w16(0xBA6, 0)               # SysFontFam: Chicago
        w16(0xBA8, 12)              # SysFontSiz
        w8(0x938, 0xFF)             # HiliteMode
        self.wpstr(0x910, b"HyperCard", 31)   # CurApName
        w32(0x904, A5_WORLD)        # CurrentA5
        # zones: fake headers, so code comparing them sees something stable
        w32(0x2A6, HEAP_LO - 0x100)  # SysZone
        w32(0x2AA, HEAP_LO - 0x80)   # ApplZone
        w32(0x118, HEAP_LO - 0x80)   # TheZone
        w32(0x130, HEAP_HI)          # ApplLimit
        w32(0x114, HEAP_HI)          # HeapEnd
        w32(0x108, 64 << 20)         # MemTop
        w32(0x824, 0x00E80000)       # ScrnBase (an unused area)
        # A5 world and QuickDraw globals
        self.set_a(5, A5_WORLD)
        w32(A5_WORLD, QD_THEPORT)
        qd = QD_THEPORT
        w32(qd - 122, 0x00E80000)                 # screenBits.baseAddr
        w16(qd - 118, 80)                         # rowBytes (640 pixels)
        for i, v in enumerate((0, 0, 480, 640)):  # bounds: top left bottom right
            w16(qd - 116 + 2 * i, v)
        pats = {-40: 0x77DD77DD, -32: 0x88228822, -24: 0xAA55AA55, -16: 0xFFFFFFFF, -8: 0}
        for off, v in pats.items():
            w32(qd + off, v)
            w32(qd + off + 4, v)
        w32(qd - 126, 1)                          # randSeed
        self.port = self.heap.new_ptr(108, clear=True)
        self._init_port(self.port)
        w32(qd, self.port)

    def _init_port(self, p):
        w16, w32 = self.w16, self.w32
        w32(p + 2, 0x00E80000)              # portBits.baseAddr
        w16(p + 6, 80)
        for i, v in enumerate((0, 0, 480, 640)):
            w16(p + 8 + 2 * i, v)           # portBits.bounds
            w16(p + 16 + 2 * i, v)          # portRect
        w16(p + 68, 3)                      # txFont Geneva
        w8 = self.w8
        w8(p + 70, 0)                       # txFace
        w16(p + 72, 1)                      # txMode srcOr
        w16(p + 74, 9)                      # txSize
        w16(p + 48, 1)                      # pnSize
        w16(p + 50, 1)

    # -- running --------------------------------------------------------------------------------
    def disasm(self, pc):
        buf = ctypes.create_string_buffer(256)
        n = self.lib.hcx_disasm(pc, buf)
        return n, buf.value.decode("latin-1")

    def call(self, entry, args32=(), trap=None, callback=None, max_instr=50_000_000):
        """Calls the code at `entry` like a Pascal procedure with the given
        long arguments (pushed left to right) and runs it until it returns.
        trap(machine, opcode) handles A-line traps; callback(machine)
        handles a JSR to CALLBACK_STUB."""
        for v in args32:
            self.push32(v)
        self.push32(EXIT_STUB)
        self.set_reg(PC, entry)
        start = self.lib.hcx_icount()
        while True:
            stop = self.lib.hcx_run(200_000)
            if stop == 0:
                if self.lib.hcx_icount() - start > max_instr:
                    raise EmuError("too long (pc %06X)" % self.reg(PC))
                continue
            if stop == EXIT_STUB:
                return
            if stop == ALINE_STUB:
                sp = self.sp
                sr = self.r16(sp)
                pc = self.r32(sp + 2)
                self.sp = sp + 8            # 68020 four-word frame
                self.set_reg(SR, sr)
                op = self.r16(pc)
                self.set_reg(PC, pc + 2)
                if trap is None:
                    raise Unimplemented("trap %04X" % op)
                if op & 0x0800 and op & 0x0400:
                    # Toolbox trap with auto-pop: glue pushed its own return
                    # address on top of the arguments; the routine returns there
                    ret = self.pop32()
                    trap(self, op)
                    self.set_reg(PC, ret)
                else:
                    trap(self, op)
                continue
            if stop == CALLBACK_STUB:
                ret = self.pop32()
                if callback is None:
                    raise Unimplemented("callback")
                callback(self)
                self.set_reg(PC, ret)
                continue
            # a crash: F-line (FPU instruction on a CPU without one) or another exception
            sp = self.sp
            pc = self.r32(sp + 2)
            vec = (self.r16(sp + 6) & 0x0FFF) // 4
            names = {2: "bus error", 3: "address error", 4: "illegal instruction", 5: "divide by zero",
                     6: "CHK", 7: "TRAPV", 8: "privilege violation", 9: "trace", 11: "F-line instruction"}
            what = names.get(vec, "trap #%d" % (vec - 32) if 32 <= vec < 48 else "exception %d" % vec)
            if 32 <= vec < 48:
                raise Unimplemented("TRAP #%d" % (vec - 32))
            if os.environ.get("XEMU_TRACE"):
                hist = self.history()
                low = [i for i, a in enumerate(hist) if a < 0x400]
                if low:     # ran into low memory: show what led there
                    hist = hist[max(0, low[0] - 40):low[0] + 2]
                for a in hist[-60:]:
                    print("   %06X  %s" % (a, self.disasm(a)[1]), file=sys.stderr)
            raise EmuError("%s at %06X (%s)" % (what, pc, self.disasm(pc)[1]))

    def history(self):
        """Addresses of the last instructions run (1024 at most), oldest first."""
        if not hasattr(self.lib, "hcx_history"):
            return []
        buf = (ctypes.c_uint * 1024)()
        self.lib.hcx_history(buf)
        return [a & 0xFFFFFF for a in buf]


class Heap:
    """Pointers and handles in the emulated memory. Blocks don't move
    while locked; an unlocked handle may move when it grows."""

    def __init__(self, m):
        self.m = m
        self.free = [(HEAP_LO, HEAP_HI - HEAP_LO)]    # (addr, size), sorted
        self.blocks = {}                              # addr -> capacity
        self.ptr_size = {}                            # pointer -> logical size
        self.handles = {}                             # handle -> [size, flags]
        self.free_mps = []
        self.next_mp = MP_LO

    # raw blocks
    def _alloc(self, size):
        size = (max(size, 4) + 15) & ~15
        for i, (a, n) in enumerate(self.free):
            if n >= size:
                if n == size:
                    del self.free[i]
                else:
                    self.free[i] = (a + size, n - size)
                self.blocks[a] = size
                return a
        return 0

    def _release(self, a):
        size = self.blocks.pop(a, None)
        if size is None:
            return
        import bisect
        i = bisect.bisect(self.free, (a, 0))
        self.free.insert(i, (a, size))
        # coalesce with neighbours
        if i + 1 < len(self.free) and a + size == self.free[i + 1][0]:
            self.free[i] = (a, size + self.free[i + 1][1])
            del self.free[i + 1]
        if i > 0 and self.free[i - 1][0] + self.free[i - 1][1] == a:
            self.free[i - 1] = (self.free[i - 1][0], self.free[i - 1][1] + self.free[i][1])
            del self.free[i]

    def _grow_in_place(self, a, size):
        cap = self.blocks[a]
        size = (max(size, 4) + 15) & ~15
        if size <= cap:
            return True
        end = a + cap
        for i, (fa, fn) in enumerate(self.free):
            if fa == end and cap + fn >= size:
                take = size - cap
                if fn == take:
                    del self.free[i]
                else:
                    self.free[i] = (fa + take, fn - take)
                self.blocks[a] = size
                return True
        return False

    def free_bytes(self):
        return sum(n for _a, n in self.free)

    def max_block(self):
        return max((n for _a, n in self.free), default=0)

    # pointers
    def new_ptr(self, size, clear=False):
        a = self._alloc(size)
        if a:
            self.ptr_size[a] = size
            if clear:
                self.m.wbytes(a, bytes(size))
        return a

    def dispose_ptr(self, p):
        p &= 0xFFFFFF
        if p in self.ptr_size:
            del self.ptr_size[p]
            self._release(p)

    def set_ptr_size(self, p, size):
        p &= 0xFFFFFF
        if p not in self.ptr_size:
            return nilHandleErr
        if self._grow_in_place(p, size):
            self.ptr_size[p] = size
            return noErr
        return memFullErr

    # handles
    def _mp(self):
        if self.free_mps:
            return self.free_mps.pop()
        if self.next_mp >= MP_HI:
            return 0
        h = self.next_mp
        self.next_mp += 4
        return h

    def new_handle(self, size, clear=False, data=None):
        h = self._mp()
        if not h:
            return 0
        a = self._alloc(size)
        if not a:
            self.free_mps.append(h)
            return 0
        self.m.w32(h, a)
        self.handles[h] = [size, 0]
        if data is not None:
            self.m.wbytes(a, data[:size])
        elif clear:
            self.m.wbytes(a, bytes(size))
        return h

    def new_empty_handle(self):
        h = self._mp()
        if h:
            self.m.w32(h, 0)
            self.handles[h] = [0, 0]
        return h

    def is_handle(self, h):
        return (h & 0xFFFFFF) in self.handles

    def deref(self, h):
        return self.m.r32(h & 0xFFFFFF) & 0xFFFFFF

    def size(self, h):
        e = self.handles.get(h & 0xFFFFFF)
        return e[0] if e else -1

    def dispose(self, h):
        h &= 0xFFFFFF
        if h not in self.handles:
            return nilHandleErr
        a = self.deref(h)
        if a:
            self._release(a)
        del self.handles[h]
        self.m.w32(h, 0)
        self.free_mps.append(h)
        return noErr

    def set_size(self, h, size):
        h &= 0xFFFFFF
        e = self.handles.get(h)
        if e is None:
            return nilHandleErr
        a = self.deref(h)
        if not a:
            a = self._alloc(size)
            if not a:
                return memFullErr
            self.m.w32(h, a)
            e[0] = size
            return noErr
        if size <= e[0] or self._grow_in_place(a, size):
            e[0] = size
            return noErr
        if e[1] & 0x80:            # locked: can't move
            return memFullErr
        b = self._alloc(size)
        if not b:
            return memFullErr
        self.m.wbytes(b, self.m.rbytes(a, e[0]))
        self._release(a)
        self.m.w32(h, b)
        e[0] = size
        return noErr

    def flags(self, h):
        e = self.handles.get(h & 0xFFFFFF)
        return e[1] if e else 0

    def set_flags(self, h, f):
        e = self.handles.get(h & 0xFFFFFF)
        if e:
            e[1] = f & 0xFF

    def recover(self, p):
        p &= 0xFFFFFF
        for h in self.handles:
            if self.deref(h) == p:
                return h
        return 0

    def handle_data(self, h):
        return self.m.rbytes(self.deref(h), max(0, self.size(h)))
