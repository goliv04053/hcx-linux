"""The part of the Mac Toolbox that externals call, for mac68k.

Each trap is a Python function. OS traps (A0xx) take and return values in
registers; Toolbox traps (A8xx-ABxx) use the Pascal convention: space for
the result first, then the arguments left to right, all on the stack. The
Resource Manager reads the stack's resource chain; drawing traps do
nothing.
Anything else stops the external with Unimplemented, so the log says
exactly what's missing."""

import collections
import os
import time

from . import sane
from .mac68k import Unimplemented, noErr, memFullErr, nilHandleErr, resNotFound

MAC_EPOCH = 2082844800      # seconds from 1904 to 1970

_OS = {}        # trap number (low byte) -> function(tb, op)
_TB = {}        # trap number (low 10 bits) -> function(tb, op)


def os_trap(*nums):
    def deco(f):
        for n in nums:
            _OS[n & 0xFF] = f
        return f
    return deco


def tb_trap(*nums):
    def deco(f):
        for n in nums:
            _TB[n & 0x3FF] = f
        return f
    return deco


def _ostype(v):
    return v.to_bytes(4, "big").decode("mac_roman")


def _ostype_int(s):
    return int.from_bytes(s.encode("mac_roman", "replace")[:4].ljust(4, b" "), "big")


class Toolbox:
    def __init__(self, m, it):
        self.m = m
        self.it = it
        self.heap = m.heap
        self.res_err = 0
        self.used = collections.Counter()   # trap word -> calls, for tools/xemu/compare.py
        self.res_load = True
        self.cur_res = 1
        self.res_handles = {}       # (file, type, id) -> handle
        self.handle_res = {}        # handle -> (file, type, id, name)
        self.files = self._res_files()
        self.t0 = time.monotonic()
        self.log = it.host.log if it is not None else (lambda *_a: None)
        self._screen()
        self._drive_queue()

    def _drive_queue(self):
        """One hard disk in the drive queue (DrvQHdr): drive 1, the drive
        every volume of the File Manager is on. The 4 bytes before an
        element are its flags: not locked, a disk that can't be ejected."""
        m = self.m
        p = self.heap.deref(self.heap.new_handle(4 + 16, clear=True))
        m.wbytes(p, bytes([0, 8, 0, 0]))
        q = p + 4
        m.w16(q + 4, 0)                     # qType
        m.w16(q + 6, 1)                     # dQDrive
        m.w16(q + 8, -33 & 0xFFFF)          # dQRefNum: the SCSI disk driver
        m.w16(q + 10, 0)                    # dQFSID: HFS
        m.w16(0x308, 0)
        m.w32(0x30A, q)                     # qHead
        m.w32(0x30E, q)                     # qTail

    def _screen(self):
        """The main screen: QuickDraw's screenBits and a GDevice, sized
        like the screen HCX reports in `the screenRect`."""
        m = self.m
        try:
            l, t, r, b = (int(v) for v in str(self.it.host.screen_rect()).split(","))
        except Exception:  # noqa: BLE001
            l, t, r, b = 0, 0, 640, 480
        qd = m.r32(m.a(5))
        for i, v in enumerate((t, l, b, r)):
            m.w16(qd - 116 + 2 * i, v)
        pm = self.heap.new_handle(50, clear=True)
        p = self.heap.deref(pm)
        m.w32(p, 0x00E80000)                # baseAddr
        m.w16(p + 4, 0x8000 | ((r - l) & 0x3FFF))
        for i, v in enumerate((t, l, b, r)):
            m.w16(p + 6 + 2 * i, v)         # bounds
        m.w16(p + 32, 16)                   # pixelSize: thousands of colours, the depth
        m.w16(p + 30, 16)                   # pixelType: RGBDirect   HCX's GetDepth gives
        m.w16(p + 34, 3)                    # cmpCount
        m.w16(p + 36, 5)                    # cmpSize
        gd = self.heap.new_handle(62, clear=True)
        g = self.heap.deref(gd)
        m.w16(g + 4, 2)                     # gdType: directType
        m.w32(g + 42, 132)                  # gdMode (a long): the 16-bit mode
        m.w32(g + 22, pm)                   # gdPMap
        for i, v in enumerate((t, l, b, r)):
            m.w16(g + 34 + 2 * i, v)        # gdRect
        m.w16(g + 20, 0x3C00 | 0x8000 | 1)  # gdFlags: main screen, active, screen device, colour
        m.w32(g + 30, 0)                    # gdNextGD
        self.gdevice = gd
        rgn = self.heap.new_handle(10, clear=True)
        r_p = self.heap.deref(rgn)
        m.w16(r_p, 10)                      # the desktop: the screen below the menu bar
        for i, v in enumerate((t + 20, l, b, r)):
            m.w16(r_p + 2 + 2 * i, v)
        m.w32(0x9EE, rgn)                   # GrayRgn
        m.w16(0xBAA, 20)                    # MBarHeight
        m.w32(0x8A4, gd)                    # MainDevice
        m.w32(0x8A8, gd)                    # DeviceList
        m.w32(0xCC8, gd)                    # TheGDevice

    def _res_files(self):
        """refNum -> list of (type, id, name, data): 1 = the stack, then the
        stacks in use; 0 = System (empty)."""
        files = {0: []}
        try:
            chain = self.it.host.resource_chain() if self.it is not None else []
        except Exception:  # noqa: BLE001
            chain = []
        for i, res in enumerate(chain):
            files[i + 1] = list(res)
        if 1 not in files:
            files[1] = []
        return files

    def register_code(self, h, kind, name):
        self.handle_res[h] = (1, kind, 0, name)

    # -- dispatch ---------------------------------------------------------------------------------
    def trap(self, m, op):
        self.used[op & 0xFBFF if op & 0x0800 else op] += 1
        if op & 0x0800:
            f = _TB.get(op & 0x3FF)
        else:
            f = _OS.get(op & 0xFF)
        if f is None:
            raise Unimplemented("trap %04X" % op)
        f(self, op)

    # -- Pascal helpers -------------------------------------------------------------------------
    def ret16(self, v):
        self.m.w16(self.m.sp, v & 0xFFFF)

    def ret32(self, v):
        self.m.w32(self.m.sp, v & 0xFFFFFFFF)

    def ret_bool(self, b):
        self.m.w16(self.m.sp, 0x0100 if b else 0)

    def ticks(self):
        return int((time.monotonic() - self.t0) * 60)

    # -- resources ------------------------------------------------------------------------------
    def search_files(self, one):
        if one:
            return [self.cur_res]
        return [f for f in sorted(self.files) if f >= self.cur_res or f == 0] if self.cur_res else [0]

    def find_res(self, rtype, rid=None, name=None, one=False):
        for f in self.search_files(one):
            for r in self.files.get(f, []):
                if r[0] == rtype and ((rid is not None and r[1] == rid) or
                                      (name is not None and r[2].lower() == name.lower())):
                    return f, r
        return None, None

    def res_handle(self, f, r):
        key = (f, r[0], r[1])
        h = self.res_handles.get(key)
        if h and self.heap.is_handle(h):
            return h
        if self.res_load:
            h = self.heap.new_handle(len(r[3]), data=r[3])
        else:
            h = self.heap.new_empty_handle()
        if not h:
            self.res_err = memFullErr
            return 0
        self.heap.set_flags(h, 0x20)
        self.res_handles[key] = h
        self.handle_res[h] = (f, r[0], r[1], r[2])
        return h

    def get_resource(self, rtype, rid=None, name=None, one=False):
        f, r = self.find_res(rtype, rid, name, one)
        if r is None:
            self.res_err = resNotFound
            return 0
        self.res_err = noErr
        return self.res_handle(f, r)

    def indexed(self, rtype, index, one):
        n = 0
        for f in self.search_files(one):
            for r in self.files.get(f, []):
                if r[0] == rtype:
                    n += 1
                    if n == index:
                        return f, r
        return None, None


# -- Memory Manager (OS traps) ----------------------------------------------------------------------

def _clear(op):
    return bool(op & 0x0200)


@os_trap(0xA11E)
def _new_ptr(tb, op):
    p = tb.heap.new_ptr(tb.m.d(0), clear=_clear(op))
    tb.m.set_a(0, p)
    err = noErr if p else memFullErr
    tb.m.set_d(0, err)
    tb.m.set_mem_err(err)


@os_trap(0xA01F)
def _dispose_ptr(tb, op):
    tb.heap.dispose_ptr(tb.m.a(0))
    tb.m.set_d(0, 0)
    tb.m.set_mem_err(0)


@os_trap(0xA020)
def _set_ptr_size(tb, op):
    err = tb.heap.set_ptr_size(tb.m.a(0), tb.m.d(0))
    tb.m.set_d(0, err)
    tb.m.set_mem_err(err)


@os_trap(0xA021)
def _get_ptr_size(tb, op):
    n = tb.heap.ptr_size.get(tb.m.a(0) & 0xFFFFFF)
    tb.m.set_d(0, n if n is not None else nilHandleErr)
    tb.m.set_mem_err(0 if n is not None else nilHandleErr)


@os_trap(0xA122)
def _new_handle(tb, op):
    h = tb.heap.new_handle(tb.m.d(0), clear=_clear(op))
    tb.m.set_a(0, h)
    err = noErr if h else memFullErr
    tb.m.set_d(0, err)
    tb.m.set_mem_err(err)


@os_trap(0xA166)
def _new_empty_handle(tb, op):
    h = tb.heap.new_empty_handle()
    tb.m.set_a(0, h)
    tb.m.set_d(0, 0 if h else memFullErr)
    tb.m.set_mem_err(0 if h else memFullErr)


@os_trap(0xA023)
def _dispose_handle(tb, op):
    h = tb.m.a(0)
    tb.handle_res.pop(h & 0xFFFFFF, None)
    err = tb.heap.dispose(h)
    tb.m.set_d(0, err)
    tb.m.set_mem_err(err)


@os_trap(0xA024)
def _set_handle_size(tb, op):
    err = tb.heap.set_size(tb.m.a(0), tb.m.d(0))
    tb.m.set_d(0, err)
    tb.m.set_mem_err(err)


@os_trap(0xA025)
def _get_handle_size(tb, op):
    n = tb.heap.size(tb.m.a(0))
    tb.m.set_d(0, n if n >= 0 else nilHandleErr)
    tb.m.set_mem_err(0 if n >= 0 else nilHandleErr)


@os_trap(0xA027)
def _realloc_handle(tb, op):
    h = tb.m.a(0)
    err = tb.heap.set_size(h, tb.m.d(0))
    tb.m.set_d(0, err)
    tb.m.set_mem_err(err)


@os_trap(0xA02B)
def _empty_handle(tb, op):
    h = tb.m.a(0) & 0xFFFFFF
    if tb.heap.is_handle(h):
        a = tb.heap.deref(h)
        if a:
            tb.heap._release(a)
        tb.m.w32(h, 0)
        tb.heap.handles[h][0] = 0
    tb.m.set_d(0, 0)
    tb.m.set_mem_err(0)


@os_trap(0xA128)
def _recover_handle(tb, op):
    h = tb.heap.recover(tb.m.a(0))
    tb.m.set_a(0, h)
    tb.m.set_mem_err(0 if h else nilHandleErr)


def _set_flag(tb, mask, on):
    h = tb.m.a(0)
    if tb.heap.is_handle(h):
        f = tb.heap.flags(h)
        tb.heap.set_flags(h, (f | mask) if on else (f & ~mask))
        err = 0
    else:
        err = nilHandleErr
    tb.m.set_d(0, err)
    tb.m.set_mem_err(err)


@os_trap(0xA029)
def _hlock(tb, op):
    _set_flag(tb, 0x80, True)


@os_trap(0xA02A)
def _hunlock(tb, op):
    _set_flag(tb, 0x80, False)


@os_trap(0xA049)
def _hpurge(tb, op):
    _set_flag(tb, 0x40, True)


@os_trap(0xA04A)
def _hnopurge(tb, op):
    _set_flag(tb, 0x40, False)


@os_trap(0xA067)
def _hset_rbit(tb, op):
    _set_flag(tb, 0x20, True)


@os_trap(0xA068)
def _hclr_rbit(tb, op):
    _set_flag(tb, 0x20, False)


@os_trap(0xA069)
def _hget_state(tb, op):
    h = tb.m.a(0)
    tb.m.set_d(0, tb.heap.flags(h) if tb.heap.is_handle(h) else nilHandleErr)
    tb.m.set_mem_err(0)


@os_trap(0xA06A)
def _hset_state(tb, op):
    h = tb.m.a(0)
    if tb.heap.is_handle(h):
        tb.heap.set_flags(h, tb.m.d(0))
    tb.m.set_d(0, 0)
    tb.m.set_mem_err(0)


@os_trap(0xA064, 0xA04C, 0xA04D, 0xA04E, 0xA036, 0xA063, 0xA02C, 0xA02D, 0xA057, 0xA04B, 0xA040)
def _mem_noop(tb, op):
    """MoveHHi, CompactMem, ResrvMem, PurgeMem, MoreMasters, MaxApplZone and
    friends: nothing to do in this heap."""
    if op & 0xFF == 0x4C:       # CompactMem returns the largest free block
        tb.m.set_d(0, tb.heap.max_block())
    else:
        tb.m.set_d(0, 0)
    tb.m.set_mem_err(0)


@os_trap(0xA01C)
def _free_mem(tb, op):
    tb.m.set_d(0, tb.heap.free_bytes())


@os_trap(0xA01D)
def _max_mem(tb, op):
    tb.m.set_d(0, tb.heap.max_block())
    tb.m.set_a(0, 0)


@os_trap(0xA062)
def _purge_space(tb, op):
    tb.m.set_d(0, tb.heap.free_bytes())
    tb.m.set_a(0, tb.heap.max_block())


@os_trap(0xA065)
def _stack_space(tb, op):
    tb.m.set_d(0, 0x8000)


@os_trap(0xA01A)
def _get_zone(tb, op):
    tb.m.set_a(0, tb.m.r32(0x118))
    tb.m.set_d(0, 0)


@os_trap(0xA01B)
def _set_zone(tb, op):
    tb.m.set_d(0, 0)


@os_trap(0xA126, 0xA048)
def _handle_zone(tb, op):
    tb.m.set_a(0, tb.m.r32(0x118))
    tb.m.set_d(0, 0)


@os_trap(0xA02E)
def _block_move(tb, op):
    src, dst, n = tb.m.a(0), tb.m.a(1), tb.m.d(0)
    if 0 < n < 1 << 24:
        tb.m.wbytes(dst, tb.m.rbytes(src, n))
    tb.m.set_d(0, 0)


@os_trap(0xA055)
def _strip_address(tb, op):
    tb.m.set_d(0, tb.m.d(0) & 0xFFFFFF)


# Memory Manager utilities with Toolbox numbers but register arguments

@tb_trap(0xA9E1)
def _hand_to_hand(tb, op):
    h = tb.m.a(0)
    if not tb.heap.is_handle(h):
        tb.m.set_d(0, nilHandleErr)
        return
    data = tb.heap.handle_data(h)
    n = tb.heap.new_handle(len(data), data=data)
    tb.m.set_a(0, n)
    tb.m.set_d(0, 0 if n else memFullErr)


@tb_trap(0xA9E3)
def _ptr_to_hand(tb, op):
    data = tb.m.rbytes(tb.m.a(0), tb.m.d(0))
    n = tb.heap.new_handle(len(data), data=data)
    tb.m.set_a(0, n)
    tb.m.set_d(0, 0 if n else memFullErr)


@tb_trap(0xA9E2)
def _ptr_to_xhand(tb, op):
    data = tb.m.rbytes(tb.m.a(0), tb.m.d(0))
    h = tb.m.a(1)
    err = tb.heap.set_size(h, len(data))
    if not err:
        tb.m.wbytes(tb.heap.deref(h), data)
    tb.m.set_a(0, h)
    tb.m.set_d(0, err)


@tb_trap(0xA9E4)
def _hand_and_hand(tb, op):
    src, dst = tb.m.a(0), tb.m.a(1)
    data = tb.heap.handle_data(src)
    n = tb.heap.size(dst)
    err = tb.heap.set_size(dst, n + len(data))
    if not err:
        tb.m.wbytes(tb.heap.deref(dst) + n, data)
    tb.m.set_a(0, dst)
    tb.m.set_d(0, err)


@tb_trap(0xA9EF)
def _ptr_and_hand(tb, op):
    data = tb.m.rbytes(tb.m.a(0), tb.m.d(0))
    dst = tb.m.a(1)
    n = tb.heap.size(dst)
    err = tb.heap.set_size(dst, n + len(data))
    if not err:
        tb.m.wbytes(tb.heap.deref(dst) + n, data)
    tb.m.set_a(0, dst)
    tb.m.set_d(0, err)


@tb_trap(0xA9E0)
def _munger(tb, op):
    """Munger(h, offset, ptr1, len1, ptr2, len2): find ptr1 in the handle
    from offset and replace it by ptr2 (or just find, if ptr2 is nil)."""
    m = tb.m
    len2, ptr2, len1, ptr1, offset, h = (m.pops32(), m.pop32(), m.pops32(), m.pop32(), m.pop32(), m.pop32())
    data = tb.heap.handle_data(h)
    if ptr1 == 0:
        start = offset
        end = len(data) if len1 < 0 else min(len(data), offset + len1)
    else:
        pat = m.rbytes(ptr1, len1)
        i = data.find(pat, offset)
        if i < 0:
            tb.ret32(-1)
            return
        start, end = i, i + len1
    if ptr2 == 0:
        tb.ret32(start)
        return
    new = data[:start] + m.rbytes(ptr2, max(0, len2)) + data[end:]
    if tb.heap.set_size(h, len(new)):
        tb.ret32(-1)
        return
    m.wbytes(tb.heap.deref(h), new)
    tb.ret32(start + max(0, len2))


# -- strings, dates, time -------------------------------------------------------------------------

def _fold(b, case, marks):
    s = b.decode("mac_roman", "replace")
    if not case:
        s = s.upper()
    if not marks:
        import unicodedata
        s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    return s


@os_trap(0xA03C)
def _cmp_string(tb, op):
    m = tb.m
    d0 = m.d(0)
    a = m.rbytes(m.a(0), d0 >> 16)
    b = m.rbytes(m.a(1), d0 & 0xFFFF)
    case, marks = bool(op & 0x0400), not (op & 0x0200)
    m.set_d(0, 0 if _fold(a, case, marks) == _fold(b, case, marks) else 1)


@os_trap(0xA050)
def _rel_string(tb, op):
    m = tb.m
    d0 = m.d(0)
    a = _fold(m.rbytes(m.a(0), d0 >> 16), bool(op & 0x0400), not (op & 0x0200))
    b = _fold(m.rbytes(m.a(1), d0 & 0xFFFF), bool(op & 0x0400), not (op & 0x0200))
    m.set_d(0, (a > b) - (a < b))


@os_trap(0xA054)
def _upr_string(tb, op):
    m = tb.m
    p, n = m.a(0), m.d(0) & 0xFFFF
    s = _fold(m.rbytes(p, n), False, not (op & 0x0200))
    m.wbytes(p, s.encode("mac_roman", "replace")[:n].ljust(n))


@os_trap(0xA039)
def _read_date_time(tb, op):
    secs = int(time.time() - time.timezone + (3600 if time.localtime().tm_isdst else 0)) + MAC_EPOCH
    tb.m.w32(tb.m.a(0), secs)
    tb.m.w32(0x20C, secs)
    tb.m.set_d(0, 0)


@tb_trap(0xA9C6)
def _secs2date(tb, op):
    m = tb.m
    t = time.gmtime(m.d(0) - MAC_EPOCH)
    p = m.a(0)
    for i, v in enumerate((t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, (t.tm_wday + 1) % 7 + 1)):
        m.w16(p + 2 * i, v)


@tb_trap(0xA9C7)
def _date2secs(tb, op):
    import calendar
    m = tb.m
    p = m.a(0)
    y, mo, d, h, mi, s = (m.r16(p + 2 * i) for i in range(6))
    try:
        secs = calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) + MAC_EPOCH
    except (ValueError, OverflowError):
        secs = 0
    m.set_d(0, secs)


@tb_trap(0xA975)
def _tick_count(tb, op):
    tb.ret32(tb.ticks())


@os_trap(0xA03B)
def _delay(tb, op):
    n = tb.m.a(0)
    time.sleep(min(n, 600) / 60.0)
    tb.m.set_d(0, tb.ticks())


@tb_trap(0xA9C8)
def _sys_beep(tb, op):
    tb.m.pop16()
    try:
        tb.it.host.beep()
    except Exception:  # noqa: BLE001
        pass


@tb_trap(0xA9EE)
def _pack7(tb, op):
    m = tb.m
    sel = m.pop16()
    if sel == 0:            # NumToString(D0, A0)
        v = m.d(0)
        m.wpstr(m.a(0), str(v - (1 << 32) if v & 0x80000000 else v).encode())
    elif sel == 1:          # StringToNum(A0) -> D0
        s = m.pstr(m.a(0)).decode("mac_roman", "replace").strip()
        neg = s.startswith("-")
        s = s.lstrip("+-")
        n = 0
        for c in s:
            if c.isdigit():
                n = n * 10 + int(c)
        m.set_d(0, -n if neg else n)
    elif sel in (2, 4):     # PStr2Dec / CStr2Dec
        sane.pack7(tb, sel)
    else:
        raise Unimplemented("Pack7 selector %d" % sel)


# -- environment ------------------------------------------------------------------------------------

def _implemented(tool, n):
    return n in (_TB if tool else _OS)


@os_trap(0xA146, 0xA346, 0xA746)
def _get_trap_address(tb, op):
    """GetTrapAddress / GetOSTrapAddress / GetToolTrapAddress: the address of
    a small glue (trap word, RTS), or of _Unimplemented for traps HCX
    doesn't have, so the external's own "is this trap there?" test works."""
    from .mac68k import TOOL_GLUE, OS_GLUE
    n = tb.m.d(0) & 0xFFFF
    if op & 0x0200:
        tool = bool(op & 0x0400)
    else:
        tool = bool(n & 0x0800) or not ((n & 0x1FF) < 0x50 or (n & 0x1FF) in (0x54, 0x57))
    num = n & (0x3FF if tool else 0xFF)
    if not _implemented(tool, num):
        tool, num = True, 0x09F
    tb.m.set_a(0, (TOOL_GLUE + 4 * num) if tool else (OS_GLUE + 4 * num))
    tb.m.set_d(0, 0)


@os_trap(0xA047, 0xA247, 0xA647)
def _set_trap_address(tb, op):
    tb.log("XCMD tried to patch trap %04X; ignored" % (tb.m.d(0) & 0xFFFF))
    tb.m.set_d(0, 0)


@tb_trap(0xA89F)
def _unimplemented_trap(tb, op):
    raise Unimplemented("_Unimplemented")


@os_trap(0xA090)
def _sys_environs(tb, op):
    m = tb.m
    p = m.a(0)
    m.w16(p, 2)                 # environsVersion
    m.w16(p + 2, 9)             # machineType: Mac IIx
    m.w16(p + 4, 0x0755)        # systemVersion
    m.w16(p + 6, 3)             # processor: 68020
    m.w8(p + 8, 1)              # hasFPU
    m.w8(p + 9, 1)              # hasColorQD
    m.w16(p + 10, 2)            # keyBoardType
    m.w16(p + 12, 1)            # atDrvrVersNum
    m.w16(p + 14, 0)            # sysVRefNum
    m.set_d(0, 0)


# -- bit operations: bit 0 is the high bit of the first byte -------------------------------------

@tb_trap(0xA85D, 0xA85E, 0xA85F)
def _bit_tst_set_clr(tb, op):
    m = tb.m
    n = m.pops32()
    p = m.pop32() + (n >> 3)
    mask = 0x80 >> (n & 7)
    if op & 0x3FF == 0x05D:
        tb.ret_bool(m.r8(p) & mask)
    elif op & 0x3FF == 0x05E:
        m.w8(p, m.r8(p) | mask)
    else:
        m.w8(p, m.r8(p) & ~mask & 0xFF)


@tb_trap(0xA858, 0xA859, 0xA85B)
def _bit_and_xor_or(tb, op):
    m = tb.m
    b, a = m.pop32(), m.pop32()
    k = op & 0x3FF
    tb.ret32(a & b if k == 0x058 else a ^ b if k == 0x059 else a | b)


@tb_trap(0xA85A)
def _bit_not(tb, op):
    tb.ret32(~tb.m.pop32())


@tb_trap(0xA85C)
def _bit_shift(tb, op):
    m = tb.m
    count = m.pops16()
    v = m.pop32()
    tb.ret32((v << count) if count >= 0 else (v >> -count))


@tb_trap(0xA815)
def _scsi_dispatch(tb, op):
    """The SCSI Manager of a Mac with nothing on its SCSI bus: selecting
    any device fails with scCommErr."""
    m = tb.m
    sel = m.pop16()
    if sel in (0, 1):           # SCSIReset, SCSIGet
        tb.ret16(0)
    elif sel in (2, 11):        # SCSISelect / SCSISelAtn(targetID)
        m.pop16()
        tb.ret16(2)
    elif sel == 10:             # SCSIStat
        tb.ret16(0)
    else:
        raise Unimplemented("SCSIDispatch selector %d" % sel)


@tb_trap(0xAAA2)
def _palette_dispatch(tb, op):
    """HasDepth(gd, depth, whichFlags, flags): the screen takes every depth
    from 1 to 32 bits, colour or grey; the answer is the mode's number."""
    m = tb.m
    sel = m.d(0) & 0xFFFF
    if sel & 0xFF == 0x14:     # HasDepth (0x0A14; early glue passes 0x0014)
        m.pop16()
        m.pop16()
        depth = m.pop16()
        m.pop32()
        modes = {1: 128, 2: 129, 4: 130, 8: 131, 16: 132, 32: 133}
        tb.ret16(modes.get(depth, 0))
    else:
        raise Unimplemented("PaletteDispatch selector %04X" % sel)


_GESTALT = {
    "sysv": 0x0755, "qd  ": 0x0230, "proc": 3, "fpu ": 1, "mmu ": 2, "ram ": 64 << 20,
    "lram": 64 << 20, "vm  ": 0, "addr": 0, "tabl": 0, "font": 0, "fs  ": 0, "hfs ": 1,
    "evnt": 0, "sysa": 1, "cput": 2, "mach": 9, "kbd ": 2, "ppc ": 0, "alis": 1, "fold": 1,
    "drag": 0, "appr": 0, "dict": 0, "edtn": 0, "help": 1, "icmp": 0, "qtim": 0,
}


@os_trap(0xA1AD)
def _gestalt(tb, op):
    m = tb.m
    sel = _ostype(m.d(0))
    if sel in _GESTALT:
        m.set_a(0, _GESTALT[sel])
        m.set_d(0, 0)
    else:
        m.set_a(0, 0)
        m.set_d(0, -5551 & 0xFFFFFFFF)      # gestaltUndefSelectorErr


@os_trap(0xA198)
def _hw_priv(tb, op):
    tb.m.set_d(0, 0)


# -- Resource Manager -------------------------------------------------------------------------------

@tb_trap(0xA9A0)
def _get_resource(tb, op):
    m = tb.m
    rid = m.pops16()
    rtype = _ostype(m.pop32())
    tb.ret32(tb.get_resource(rtype, rid=rid))


@tb_trap(0xA81F)
def _get1_resource(tb, op):
    m = tb.m
    rid = m.pops16()
    rtype = _ostype(m.pop32())
    tb.ret32(tb.get_resource(rtype, rid=rid, one=True))


@tb_trap(0xA9A1)
def _get_named_resource(tb, op):
    m = tb.m
    name = m.pstr(m.pop32()).decode("mac_roman", "replace")
    rtype = _ostype(m.pop32())
    tb.ret32(tb.get_resource(rtype, name=name))


@tb_trap(0xA820)
def _get1_named_resource(tb, op):
    m = tb.m
    name = m.pstr(m.pop32()).decode("mac_roman", "replace")
    rtype = _ostype(m.pop32())
    tb.ret32(tb.get_resource(rtype, name=name, one=True))


@tb_trap(0xA80C)
def _rget_resource(tb, op):
    _get_resource(tb, op)


@tb_trap(0xA99D)
def _get_ind_resource(tb, op):
    m = tb.m
    idx = m.pops16()
    rtype = _ostype(m.pop32())
    f, r = tb.indexed(rtype, idx, False)
    tb.res_err = noErr if r else resNotFound
    tb.ret32(tb.res_handle(f, r) if r else 0)


@tb_trap(0xA80E)
def _get1_ind_resource(tb, op):
    m = tb.m
    idx = m.pops16()
    rtype = _ostype(m.pop32())
    f, r = tb.indexed(rtype, idx, True)
    tb.res_err = noErr if r else resNotFound
    tb.ret32(tb.res_handle(f, r) if r else 0)


@tb_trap(0xA99C)
def _count_resources(tb, op):
    rtype = _ostype(tb.m.pop32())
    tb.ret16(sum(1 for f in tb.search_files(False) for r in tb.files.get(f, []) if r[0] == rtype))


@tb_trap(0xA80D)
def _count1_resources(tb, op):
    rtype = _ostype(tb.m.pop32())
    tb.ret16(sum(1 for r in tb.files.get(tb.cur_res, []) if r[0] == rtype))


def _types(tb, one):
    out = []
    for f in tb.search_files(one):
        for r in tb.files.get(f, []):
            if r[0] not in out:
                out.append(r[0])
    return out


@tb_trap(0xA99E)
def _count_types(tb, op):
    tb.ret16(len(_types(tb, False)))


@tb_trap(0xA81C)
def _count1_types(tb, op):
    tb.ret16(len(_types(tb, True)))


@tb_trap(0xA99F)
def _get_ind_type(tb, op):
    m = tb.m
    idx = m.pops16()
    p = m.pop32()
    t = _types(tb, False)
    m.w32(p, _ostype_int(t[idx - 1]) if 0 < idx <= len(t) else 0)


@tb_trap(0xA80F)
def _get1_ind_type(tb, op):
    m = tb.m
    idx = m.pops16()
    p = m.pop32()
    t = _types(tb, True)
    m.w32(p, _ostype_int(t[idx - 1]) if 0 < idx <= len(t) else 0)


@tb_trap(0xA9A3)
def _release_resource(tb, op):
    h = tb.m.pop32() & 0xFFFFFF
    info = tb.handle_res.pop(h, None)
    if info:
        tb.res_handles.pop(info[:3], None)
        tb.heap.dispose(h)
    tb.res_err = noErr


@tb_trap(0xA992)
def _detach_resource(tb, op):
    h = tb.m.pop32() & 0xFFFFFF
    info = tb.handle_res.pop(h, None)
    if info:
        tb.res_handles.pop(info[:3], None)
        tb.heap.set_flags(h, tb.heap.flags(h) & ~0x20)
    tb.res_err = noErr


@tb_trap(0xA9A2)
def _load_resource(tb, op):
    h = tb.m.pop32() & 0xFFFFFF
    info = tb.handle_res.get(h)
    if info and not tb.heap.deref(h):
        f, rtype, rid, _n = info
        for r in tb.files.get(f, []):
            if r[0] == rtype and r[1] == rid:
                tb.heap.set_size(h, len(r[3]))
                tb.m.wbytes(tb.heap.deref(h), r[3])
    tb.res_err = noErr


@tb_trap(0xA997, 0xA9C4, 0xA81A)
def _open_res_file(tb, op):
    """OpenResFile, OpenRFPerm and HOpenResFile. Files in the sandbox have
    no resource fork, so this fails like opening an empty one (eofErr), or
    with fnfErr when there is no such file."""
    from . import filemgr
    m = tb.m
    k = op & 0x3FF
    dirid, vref = 0, 0
    if k == 0x1C4:          # OpenRFPerm(name, vRefNum, permission)
        m.pop16()
        vref = m.pops16()
        name_p = m.pop32()
    elif k == 0x01A:        # HOpenResFile(vRefNum, dirID, name, permission)
        m.pop16()
        name_p = m.pop32()
        dirid = m.pop32()
        vref = m.pops16()
    else:
        name_p = m.pop32()
    fm = filemgr._fm(tb)
    err, path, _v = fm.resolve(vref, dirid, m.pstr(name_p))
    tb.res_err = err or (-39 if os.path.isfile(path) else filemgr.fnfErr)
    tb.ret16(-1)


@tb_trap(0xA99A)
def _close_res_file(tb, op):
    tb.m.pop16()
    tb.res_err = noErr


@tb_trap(0xA9AF)
def _res_error(tb, op):
    tb.ret16(tb.res_err)


@tb_trap(0xA99B)
def _set_res_load(tb, op):
    tb.res_load = bool(tb.m.pop16() & 0xFF00)


@tb_trap(0xA994)
def _cur_res_file(tb, op):
    tb.ret16(tb.cur_res)


@tb_trap(0xA998)
def _use_res_file(tb, op):
    f = tb.m.pops16()
    if f in tb.files:
        tb.cur_res = f
        tb.res_err = noErr
    else:
        tb.res_err = -193       # resFNotFound


@tb_trap(0xA9A4)
def _home_res_file(tb, op):
    info = tb.handle_res.get(tb.m.pop32() & 0xFFFFFF)
    tb.res_err = noErr if info else -192
    tb.ret16(info[0] if info else -1)


@tb_trap(0xA9A8)
def _get_res_info(tb, op):
    m = tb.m
    name_p, type_p, id_p, h = m.pop32(), m.pop32(), m.pop32(), m.pop32()
    info = tb.handle_res.get(h & 0xFFFFFF)
    if not info:
        tb.res_err = -192
        return
    _f, rtype, rid, name = info
    if id_p:
        m.w16(id_p, rid)
    if type_p:
        m.w32(type_p, _ostype_int(rtype))
    if name_p:
        m.wpstr(name_p, name.encode("mac_roman", "replace"))
    tb.res_err = noErr


@tb_trap(0xA9A6)
def _get_res_attrs(tb, op):
    h = tb.m.pop32()
    tb.ret16(0 if (h & 0xFFFFFF) in tb.handle_res else 0)


@tb_trap(0xA9A5, 0xA821)
def _size_resource(tb, op):
    h = tb.m.pop32() & 0xFFFFFF
    info = tb.handle_res.get(h)
    n = -1
    if info:
        for r in tb.files.get(info[0], []):
            if r[0] == info[1] and r[1] == info[2]:
                n = len(r[3])
    tb.ret32(n)


# writing resources: kept in memory for this run only (the stack file isn't changed)

@tb_trap(0xA9AB)
def _add_resource(tb, op):
    m = tb.m
    name = m.pstr(m.pop32()).decode("mac_roman", "replace")
    rid = m.pops16()
    rtype = _ostype(m.pop32())
    h = m.pop32() & 0xFFFFFF
    entry = (rtype, rid, name, tb.heap.handle_data(h))
    tb.files.setdefault(tb.cur_res, []).insert(0, entry)
    tb.res_handles[(tb.cur_res, rtype, rid)] = h
    tb.handle_res[h] = (tb.cur_res, rtype, rid, name)
    tb.res_err = noErr


@tb_trap(0xA9AA, 0xA9B0, 0xA999, 0xA9A9, 0xA9A7)
def _res_noop(tb, op):
    """ChangedResource, WriteResource, UpdateResFile, SetResInfo, SetResAttrs."""
    m = tb.m
    n = op & 0x3FF
    if n in (0x1AA, 0x1B0):
        m.pop32()
    elif n == 0x199:
        m.pop16()
    elif n == 0x1A9:
        m.pop32(), m.pop16(), m.pop32()
    elif n == 0x1A7:
        m.pop16(), m.pop32()
    tb.res_err = noErr


@tb_trap(0xA9AD)
def _rmve_resource(tb, op):
    h = tb.m.pop32() & 0xFFFFFF
    info = tb.handle_res.pop(h, None)
    if info:
        f, rtype, rid, _n = info
        tb.files[f] = [r for r in tb.files.get(f, []) if not (r[0] == rtype and r[1] == rid)]
        tb.res_handles.pop((f, rtype, rid), None)
    tb.res_err = noErr if info else -196


@tb_trap(0xA810, 0xA9C1)
def _unique_id(tb, op):
    rtype = _ostype(tb.m.pop32())
    used = {r[1] for f in tb.files for r in tb.files[f] if r[0] == rtype}
    n = 128
    while n in used:
        n += 1
    tb.ret16(n)


@tb_trap(0xA9BA)
def _get_string(tb, op):
    rid = tb.m.pops16()
    tb.ret32(tb.get_resource("STR ", rid=rid))


# -- QuickDraw text and the cursor --------------------------------------------------------------------

def _port(tb):
    return tb.m.r32(tb.m.r32(tb.m.a(5)))


def _font_ids():
    try:
        from hcx.render import MAC_FONT_IDS
        return MAC_FONT_IDS
    except Exception:  # noqa: BLE001
        return {0: "Chicago", 1: "Geneva", 2: "New York", 3: "Geneva", 4: "Monaco", 20: "Times",
                21: "Helvetica", 22: "Courier", 16: "Palatino"}


def _port_font(tb):
    MAC_FONT_IDS = _font_ids()
    p = _port(tb)
    fid = tb.m.s16(p + 68)
    size = tb.m.s16(p + 74) or 12
    return MAC_FONT_IDS.get(fid, "Geneva"), size


def _width(tb, raw):
    font, size = _port_font(tb)
    text = raw.decode("mac_roman", "replace")
    try:
        return int(tb.it.host.text_width(text, font, size))
    except Exception:  # noqa: BLE001
        return int(len(text) * size * 0.55)


@tb_trap(0xA887)
def _text_font(tb, op):
    tb.m.w16(_port(tb) + 68, tb.m.pop16())


@tb_trap(0xA888)
def _text_face(tb, op):
    tb.m.w8(_port(tb) + 70, tb.m.pop16() >> 8)


@tb_trap(0xA889)
def _text_mode(tb, op):
    tb.m.w16(_port(tb) + 72, tb.m.pop16())


@tb_trap(0xA88A)
def _text_size(tb, op):
    tb.m.w16(_port(tb) + 74, tb.m.pop16())


@tb_trap(0xA88C)
def _string_width(tb, op):
    tb.ret16(_width(tb, tb.m.pstr(tb.m.pop32())))


@tb_trap(0xA88D)
def _char_width(tb, op):
    tb.ret16(_width(tb, bytes([tb.m.pop16() & 0xFF])))


@tb_trap(0xA886)
def _text_width(tb, op):
    m = tb.m
    count, first, p = m.pops16(), m.pops16(), m.pop32()
    tb.ret16(_width(tb, m.rbytes(p + first, max(0, count))))


@tb_trap(0xA88B)
def _get_font_info(tb, op):
    p = tb.m.pop32()
    _font, size = _port_font(tb)
    asc = max(1, round(size * 0.8))
    for i, v in enumerate((asc, max(1, size - asc), size, max(1, size // 6))):
        tb.m.w16(p + 2 * i, v)


@tb_trap(0xA874)
def _get_port(tb, op):
    p = tb.m.pop32()
    tb.m.w32(p, _port(tb))


@tb_trap(0xA873)
def _set_port(tb, op):
    port = tb.m.pop32()
    if port:
        tb.m.w32(tb.m.r32(tb.m.a(5)), port)


@tb_trap(0xA900)
def _get_fnum(tb, op):
    MAC_FONT_IDS = _font_ids()
    m = tb.m
    p = m.pop32()
    name = m.pstr(m.pop32()).decode("mac_roman", "replace").lower()
    num = 0
    for fid, n in MAC_FONT_IDS.items():
        if n.lower() == name:
            num = fid
    m.w16(p, num)


@tb_trap(0xA8FF)
def _get_font_name(tb, op):
    MAC_FONT_IDS = _font_ids()
    m = tb.m
    p = m.pop32()
    fid = m.pops16()
    m.wpstr(p, MAC_FONT_IDS.get(fid, "").encode("mac_roman"))


@tb_trap(0xA902)
def _real_font(tb, op):
    tb.m.pop16()
    tb.m.pop16()
    tb.ret_bool(True)


# no-op QuickDraw and cursor calls: (trap, argument bytes)
_NOOPS = {
    0xA850: 0, 0xA851: 4, 0xA852: 0, 0xA853: 0, 0xA856: 0, 0xA86E: 4,     # cursor, InitGraf
    0xA893: 4, 0xA894: 4, 0xA891: 4, 0xA892: 4,                         # MoveTo Move LineTo Line
    0xA8A1: 4, 0xA8A2: 4, 0xA8A3: 4, 0xA8A4: 4, 0xA8A5: 8,               # Frame/Paint/Erase/Invert/FillRect
    0xA884: 8, 0xA885: 4, 0xA883: 2,                                     # DrawText DrawString DrawChar
    0xA89B: 2, 0xA89C: 4, 0xA89D: 0, 0xA89E: 0, 0xA89F: 8,              # PenMode PenPat PenNormal ...
    0xA9B4: 0, 0xA9F1: 4, 0xA9F0: 0,                                     # SystemTask UnloadSeg
    0xA903: 2, 0xA8FE: 0,                                                # SetFontLock InitFonts
}


def _noop_factory(nbytes):
    def f(tb, op):
        tb.m.sp = tb.m.sp + nbytes
    return f


for _t, _n in _NOOPS.items():
    _TB[_t & 0x3FF] = _noop_factory(_n)


@tb_trap(0xAA2A, 0xAA29, 0xAA32)
def _get_main_device(tb, op):
    """GetMainDevice, GetDeviceList, GetGDevice."""
    tb.ret32(tb.gdevice)


@tb_trap(0xAA2B)
def _get_next_device(tb, op):
    tb.m.pop32()
    tb.ret32(0)


@tb_trap(0xAA2C)
def _test_device_attribute(tb, op):
    m = tb.m
    attr = m.pops16()
    gd = m.pop32()
    flags = m.r16(tb.heap.deref(gd) + 20) if tb.heap.is_handle(gd) else 0
    tb.ret_bool(bool(flags & (1 << attr)))


# -- QuickDraw rectangles and points ---------------------------------------------------------------

def _rd_rect(m, p):
    return [m.s16(p + 2 * i) for i in range(4)]        # top, left, bottom, right


def _wr_rect(m, p, r):
    for i, v in enumerate(r):
        m.w16(p + 2 * i, v)


def _empty(r):
    return r[2] <= r[0] or r[3] <= r[1]


def _pt(v):
    return ((v >> 16) & 0xFFFF) - (0x10000 if v & 0x80000000 else 0), (v & 0xFFFF) - (0x10000 if v & 0x8000 else 0)


@tb_trap(0xA8A7)
def _set_rect(tb, op):
    m = tb.m
    b, r, t, l = m.pops16(), m.pops16(), m.pops16(), m.pops16()
    _wr_rect(m, m.pop32(), (t, l, b, r))


@tb_trap(0xA8A8, 0xA8A9)
def _offset_inset_rect(tb, op):
    m = tb.m
    dv, dh = m.pops16(), m.pops16()
    p = m.pop32()
    t, l, b, r = _rd_rect(m, p)
    if op & 0x3FF == 0x0A8:
        _wr_rect(m, p, (t + dv, l + dh, b + dv, r + dh))
    else:
        n = (t + dv, l + dh, b - dv, r - dh)
        _wr_rect(m, p, (0, 0, 0, 0) if _empty(n) else n)


@tb_trap(0xA8AA, 0xA8AB)
def _sect_union_rect(tb, op):
    m = tb.m
    dst, b, a = m.pop32(), m.pop32(), m.pop32()
    ra, rb = _rd_rect(m, a), _rd_rect(m, b)
    if op & 0x3FF == 0x0AA:
        n = (max(ra[0], rb[0]), max(ra[1], rb[1]), min(ra[2], rb[2]), min(ra[3], rb[3]))
        ok = not _empty(n)
        _wr_rect(m, dst, n if ok else (0, 0, 0, 0))
        tb.ret_bool(ok)
    else:
        _wr_rect(m, dst, (min(ra[0], rb[0]), min(ra[1], rb[1]), max(ra[2], rb[2]), max(ra[3], rb[3])))


@tb_trap(0xA8AC)
def _pt2rect(tb, op):
    m = tb.m
    dst = m.pop32()
    v2, h2 = _pt(m.pop32())
    v1, h1 = _pt(m.pop32())
    _wr_rect(m, dst, (min(v1, v2), min(h1, h2), max(v1, v2), max(h1, h2)))


@tb_trap(0xA8AD)
def _pt_in_rect(tb, op):
    m = tb.m
    r = _rd_rect(m, m.pop32())
    v, h = _pt(m.pop32())
    tb.ret_bool(r[0] <= v < r[2] and r[1] <= h < r[3])


@tb_trap(0xA8A6)
def _equal_rect(tb, op):
    m = tb.m
    b, a = m.pop32(), m.pop32()
    tb.ret_bool(_rd_rect(m, a) == _rd_rect(m, b))


@tb_trap(0xA8AE)
def _empty_rect(tb, op):
    m = tb.m
    tb.ret_bool(_empty(_rd_rect(m, m.pop32())))


@tb_trap(0xA880)
def _set_pt(tb, op):
    m = tb.m
    v, h = m.pops16(), m.pops16()
    p = m.pop32()
    m.w16(p, v)
    m.w16(p + 2, h)


@tb_trap(0xA87E, 0xA87F)
def _add_sub_pt(tb, op):
    m = tb.m
    dst = m.pop32()
    v, h = _pt(m.pop32())
    sign = 1 if op & 0x3FF == 0x07E else -1
    m.w16(dst, m.s16(dst) + sign * v)
    m.w16(dst + 2, m.s16(dst + 2) + sign * h)


@tb_trap(0xA881)
def _equal_pt(tb, op):
    m = tb.m
    b, a = m.pop32(), m.pop32()
    tb.ret_bool(a == b)


# -- TextEdit (the records that HyperCard's GetFieldTE makes) -------------------------------------

def _te_style(tb, hte):
    m = tb.m
    t = tb.heap.deref(hte)
    return m.r32(t + 74) if m.s16(t + 80) == -1 else 0


def _te_read(tb, hte):
    """Per-character styles of a styled TE record: a list of 12-byte
    TextStyle-like tuples (font, face, size, color bytes) plus the 18-byte
    STElement heights to keep."""
    m, heap = tb.m, tb.heap
    t = heap.deref(hte)
    n = m.r16(t + 60)
    hs = _te_style(tb, hte)
    if not hs:
        return n, [(m.s16(t + 74), m.r8(t + 76), m.s16(t + 80), bytes(6), 0, 0)] * n
    sp = heap.deref(hs)
    nruns, nstyles = m.r16(sp), m.r16(sp + 2)
    tab = heap.deref(m.r32(sp + 4))
    styles = []
    for k in range(nstyles):
        e = tab + 18 * k
        styles.append((m.s16(e + 6), m.r8(e + 8), m.s16(e + 10), m.rbytes(e + 12, 6), m.s16(e + 2), m.s16(e + 4)))
    out = [styles[0] if styles else (0, 0, 12, bytes(6), 16, 12)] * n
    for i in range(nruns):
        start, k = m.r16(sp + 20 + 4 * i), m.r16(sp + 20 + 4 * i + 4 - 2)
        end = m.r16(sp + 20 + 4 * (i + 1))
        if k < len(styles):
            for c in range(start, min(end, n)):
                out[c] = styles[k]
    return n, out


def _te_write(tb, hte, n, chars):
    m, heap = tb.m, tb.heap
    hs = _te_style(tb, hte)
    if not hs:
        return
    styles, index, runs = [], {}, []
    prev = None
    for i, st in enumerate(chars or [None]):
        if st is None or st == prev:
            continue
        prev = st
        key = st[:4]
        if key not in index:
            index[key] = len(styles)
            styles.append([0, st])
        styles[index[key]][0] += 1
        runs.append((i, index[key]))
    if not runs:
        runs = [(0, 0)]
        styles = [[1, (0, 0, 12, bytes(6), 16, 12)]]
    heap.set_size(hs, 20 + 4 * (len(runs) + 1))
    sp = heap.deref(hs)
    m.w16(sp, len(runs))
    m.w16(sp + 2, len(styles))
    for i, (start, k) in enumerate(runs + [(n + 1, 0)]):
        m.w16(sp + 20 + 4 * i, start)
        m.w16(sp + 22 + 4 * i, k)
    th = m.r32(sp + 4)
    heap.set_size(th, 18 * len(styles))
    tab = heap.deref(th)
    for k, (count, (font, face, size, color, height, ascent)) in enumerate(styles):
        e = tab + 18 * k
        m.w16(e, count)
        m.w16(e + 2, height or size + max(2, size // 4) + 1)
        m.w16(e + 4, ascent or size)
        m.w16(e + 6, font)
        m.w8(e + 8, face)
        m.w8(e + 9, 0)
        m.w16(e + 10, size)
        m.wbytes(e + 12, color)


@tb_trap(0xA83D)
def _te_dispatch(tb, op):
    m = tb.m
    sel = m.pop16()
    if sel == 1:                # TESetStyle(mode, newStyle, redraw, hTE)
        hte = m.pop32()
        m.pop16()
        p = m.pop32()
        mode = m.pop16()
        font, face, size, color = m.s16(p), m.r8(p + 2), m.s16(p + 4), m.rbytes(p + 6, 6)
        t = tb.heap.deref(hte)
        start, end = m.r16(t + 32), m.r16(t + 34)
        n, chars = _te_read(tb, hte)
        for i in range(start, min(end, n)):
            f0, fa0, s0, c0, h0, a0 = chars[i]
            if mode & 1:
                f0 = font
            if mode & 2:
                fa0 = (fa0 ^ face) if mode & 32 and face and fa0 & face == face else (fa0 | face if face and mode & 32 else face)
            if mode & 16:
                s0 = s0 + size
            elif mode & 4:
                s0 = size
            if mode & 8:
                c0 = color
            chars[i] = (f0, fa0, s0, c0, 0 if (mode & 20) else h0, 0 if (mode & 20) else a0)
        _te_write(tb, hte, n, chars)
        return
    if sel == 3:                # TEGetStyle(offset, VAR theStyle, VAR lineHeight, VAR fontAscent, hTE)
        hte, pa, ph, ps = m.pop32(), m.pop32(), m.pop32(), m.pop32()
        off = m.pop16()
        n, chars = _te_read(tb, hte)
        f0, fa0, s0, c0, h0, a0 = chars[min(off, n - 1)] if n else (0, 0, 12, bytes(6), 16, 12)
        m.w16(ps, f0)
        m.w8(ps + 2, fa0)
        m.w8(ps + 3, 0)
        m.w16(ps + 4, s0)
        m.wbytes(ps + 6, c0)
        m.w16(ph, h0 or s0 + 4)
        m.w16(pa, a0 or s0)
        return
    if sel == 4:                # TEGetStyleHandle(hTE): TEStyleHandle
        hte = m.pop32()
        tb.ret32(_te_style(tb, hte))
    elif sel == 5:              # TESetStyleHandle(theHandle, hTE)
        hte, h = m.pop32(), m.pop32()
        t = tb.heap.deref(hte)
        if m.s16(t + 80) == -1:
            m.w32(t + 74, h)
    elif sel == 13:             # TENumStyles(rangeStart, rangeEnd, hTE): LONGINT
        hte = m.pop32()
        end, start = m.s32(m.sp), m.s32(m.sp + 4)
        m.sp = m.sp + 8
        hs = _te_style(tb, hte)
        n = 1
        if hs:
            sp = tb.heap.deref(hs)
            starts = [m.r16(sp + 20 + 4 * i) for i in range(m.r16(sp))]
            n = max(1, sum(1 for x in starts if start <= x < end) + (0 if start in starts else 1))
        tb.ret32(n)
    else:
        raise Unimplemented("TEDispatch selector %d" % sel)


@tb_trap(0xA9D1)
def _te_set_select(tb, op):
    m = tb.m
    hte, end, start = m.pop32(), m.pop32(), m.pop32()
    t = tb.heap.deref(hte)
    n = m.r16(t + 60)
    m.w16(t + 32, max(0, min(start, n)))
    m.w16(t + 34, max(0, min(end, n)))


@tb_trap(0xA9D0, 0xA9D8, 0xA9D9, 0xA9DA)
def _te_one_handle_noop(tb, op):
    """TECalText, TEActivate, TEDeactivate, TEIdle: nothing to draw."""
    tb.m.pop32()


@tb_trap(0xA9D3)
def _te_update(tb, op):
    tb.m.pop32()
    tb.m.pop32()


@tb_trap(0xA9CB)
def _te_get_text(tb, op):
    hte = tb.m.pop32()
    tb.ret32(tb.m.r32(tb.heap.deref(hte) + 62))


@tb_trap(0xA9CF)
def _te_set_text(tb, op):
    m, heap = tb.m, tb.heap
    hte, n, p = m.pop32(), m.pop32(), m.pop32()
    t = heap.deref(hte)
    h = m.r32(t + 62)
    heap.set_size(h, n)
    m.wbytes(heap.deref(h), m.rbytes(p, n))
    m.w16(t + 60, n)


@tb_trap(0xA9CD)
def _te_dispose(tb, op):
    heap = tb.heap
    hte = tb.m.pop32()
    if not heap.is_handle(hte):
        return
    t = heap.deref(hte)
    hs = _te_style(tb, hte)
    if hs and heap.is_handle(hs):
        sp = heap.deref(hs)
        for off in (4, 8, 16):
            h = tb.m.r32(sp + off)
            if heap.is_handle(h):
                heap.dispose(h)
        heap.dispose(hs)
    h = tb.m.r32(t + 62)
    if heap.is_handle(h):
        heap.dispose(h)
    heap.dispose(hte)


@tb_trap(0xA861)
def _random(tb, op):
    """Random: QuickDraw's Park-Miller generator on randSeed."""
    m = tb.m
    seed_at = m.r32(m.r32(0x904)) - 126       # (CurrentA5) -> thePort; randSeed before it
    seed = m.r32(seed_at) or 1
    seed = seed * 16807 % 0x7FFFFFFF
    m.w32(seed_at, seed)
    v = seed & 0xFFFF
    tb.ret16(0 if v == 0x8000 else v)


@tb_trap(0xA9B9)
def _get_cursor(tb, op):
    rid = tb.m.pops16()
    tb.ret32(tb.get_resource("CURS", rid=rid))


@tb_trap(0xA974)
def _button(tb, op):
    tb.ret_bool(False)


@tb_trap(0xA973, 0xA977)
def _still_down(tb, op):
    tb.ret_bool(False)


@tb_trap(0xA972)
def _get_mouse(tb, op):
    p = tb.m.pop32()
    tb.m.w32(p, 0)


@tb_trap(0xA976)
def _get_keys(tb, op):
    p = tb.m.pop32()
    tb.m.wbytes(p, bytes(16))


@tb_trap(0xA9FF)
def _debugger(tb, op):
    pass


@tb_trap(0xABFF)
def _debug_str(tb, op):
    s = tb.m.pstr(tb.m.pop32()).decode("mac_roman", "replace")
    tb.log("XCMD DebugStr: " + s)


# -- International Utilities and Script Manager --------------------------------------------------------

def _primary(b):
    """A Mac Roman string's primary sort key: case and accents ignored."""
    import unicodedata
    t = b.decode("mac_roman", "replace")
    return "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.combining(c)).upper()


def _mag_compare(a, b):
    pa, pb = _primary(a), _primary(b)
    if pa != pb:
        return -1 if pa < pb else 1
    return 0 if a == b else (-1 if a < b else 1)


@tb_trap(0xA9EB)
def _pack4(tb, op):
    sane.pack4(tb)


@tb_trap(0xA9ED)
def _pack6(tb, op):
    m = tb.m
    sel = m.pop16()
    if sel in (0, 2):       # IUDateString(dateTime, form, VAR s) / IUTimeString(dateTime, wantSeconds, VAR s)
        out = m.pop32()
        form = m.pop16() >> 8
        t = time.gmtime(m.pop32() - MAC_EPOCH)
        if sel == 0:
            fmt = ("%-m/%-d/%y", "%A, %B %-d, %Y", "%a, %b %-d, %Y")[min(form, 2)]
        else:
            fmt = "%-I:%M:%S %p" if form else "%-I:%M %p"
        m.wpstr(out, time.strftime(fmt, t).encode("mac_roman"), 255)
    elif sel == 6:          # IUGetIntl(theID): the US international resources
        n = m.pops16()
        if n == 0:
            data = b".,;$\0\0" + bytes([0xD0, 0, 0, ord("/"), 0xFF, 0x60]) + b" AM\0 PM\0:" + \
                b"\0" * 8 + bytes([0, 0, 0])     # Intl0Rec: metricSys false, intl0Vers 0 (US)
        elif n == 1:
            days = b"".join(bytes([len(d)]) + d.ljust(15, b"\0") for d in
                            (b"Sunday", b"Monday", b"Tuesday", b"Wednesday", b"Thursday", b"Friday", b"Saturday"))
            months = b"".join(bytes([len(x)]) + x.ljust(15, b"\0") for x in
                              (b"January", b"February", b"March", b"April", b"May", b"June", b"July",
                               b"August", b"September", b"October", b"November", b"December"))
            data = days + months + bytes([0, 0, 0]) + b", , ,\0\0".ljust(20, b"\0") + bytes([0, 3, 0, 0, 0, 0x4E, 0x71])
        else:
            raise Unimplemented("IUGetIntl(%d)" % n)
        tb.ret32(m.heap.new_handle(len(data), data=data))
    elif sel in (10, 12):     # IUMagString / IUMagIDString(aPtr, bPtr, aLen, bLen)
        blen, alen = m.pops16(), m.pops16()
        bp, ap = m.pop32(), m.pop32()
        a, b = m.rbytes(ap, max(alen, 0)), m.rbytes(bp, max(blen, 0))
        tb.ret16((0 if a == b else 1) if sel == 12 else _mag_compare(a, b) & 0xFFFF)
    else:
        raise Unimplemented("Pack6 selector %d" % sel)


def _char_type(c):
    ch = bytes([c]).decode("mac_roman")
    if ch.isalpha():
        return (1 if c < 0x80 else 7) | (0x4000 if ch.isupper() else 0)
    if ch.isdigit():
        return 0x0100                   # smPunctNumber
    if ch.isspace() or c < 0x20:
        return 0x0300                   # smPunctBlank
    return 0x0200 if c >= 0x80 else 0   # smPunctSymbol / smPunctNormal


def _is_word_char(c):
    t = _char_type(c)
    return t & 0x0F != 0 or t == 0x0100


@tb_trap(0xA8B5)
def _script_util(tb, op):
    """ScriptUtil for the Roman script only. The selector says how many
    bytes of arguments and of result there are; with bit 30 set (the System
    7 calls such as FindWordBreaks) a script code comes first."""
    m = tb.m
    sel = m.pop32()
    routine, nargs, nres = sel & 0xFFFF, (sel >> 16) & 0xFF, (sel >> 24) & 0x0F
    if sel & 0x40000000:        # the System 7 forms take a script code on top
        m.sp += 2
    args = m.rbytes(m.sp, nargs)
    m.sp += nargs

    def arg16(off):
        v = int.from_bytes(args[off:off + 2], "big")
        return v - 0x10000 if v & 0x8000 else v

    def arg32(off):
        return int.from_bytes(args[off:off + 4], "big")

    res = 0
    if routine in (0, 2, 4, 6, 14, 16, 10):    # FontScript, IntlScript, KybdScript, Font2Script,
        res = 0                                # SetScript, CharByte, SetEnvirons: Roman, one byte
    elif routine == 8:                         # GetEnvirons(verb)
        res = {0: 0x0710, 4: 1}.get(arg16(0), 0)
    elif routine == 12:                        # GetScript(script, verb)
        res = 0
    elif routine == 18:                        # CharType(textBuf, textOffset)
        res = _char_type(m.rbytes(arg32(2) + arg16(0), 1)[0])
    elif routine == 26:                        # FindWord(text, length, offset, leadingEdge, breaks, offsets)
        offsets, text, n = arg32(0), arg32(14), arg16(12)
        off, lead = arg16(10), args[8] != 0
        buf = m.rbytes(text, max(n, 0))
        i = off if lead else off - 1
        i = min(max(i, 0), max(n - 1, 0))
        a = b = i
        if n and _is_word_char(buf[i]):
            while a > 0 and _is_word_char(buf[a - 1]):
                a -= 1
            while b < n and _is_word_char(buf[b]):
                b += 1
        else:
            b = min(i + 1, n)
        m.wbytes(offsets, a.to_bytes(2, "big") + b.to_bytes(2, "big") + bytes(8))
    elif routine == 0xFFF8:                    # InitDateCache(cache)
        res = 0
    else:
        raise Unimplemented("ScriptUtil selector %08X" % sel)
    if nres == 4:
        m.w32(m.sp, res & 0xFFFFFFFF)
    elif nres == 2:
        m.w16(m.sp, res & 0xFFFF)


# -- File Manager -----------------------------------------------------------------------------------

from . import filemgr  # noqa: E402

filemgr.install(os_trap, tb_trap)
