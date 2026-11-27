"""Running a stack's own XCMD or XFCN resource in the 68k emulator.

HyperCard calls an external with a pointer to an XCmdBlock:

  0  paramCount   short
  2  params       16 handles to zero-terminated strings
  66 returnValue  handle
  70 passFlag     Boolean
  72 entryPoint   the address to JSR to for a callback
  76 request      short, the callback wanted
  78 result       short, 0 = done, 1 = failed, 2 = not implemented
  80 inArgs       8 longs
  112 outArgs     4 longs

The callbacks are answered here with the interpreter, and the Toolbox
traps by toolbox.py. Text goes in and out as Mac Roman with CR line ends."""

import collections
import os
import struct

from hcx import hypertalk as ht
from . import mac68k
from .mac68k import Machine, EmuError, Unimplemented

BLOCK_SIZE = 128
TRACE = bool(os.environ.get("XEMU_TRACE"))

# callback request codes (HyperXCmd.h)
SEND_CARD_MESSAGE = 1
EVAL_EXPR = 2
STRING_LENGTH = 3
STRING_MATCH = 4
SEND_HC_MESSAGE = 5
ZERO_BYTES = 6
PAS_TO_ZERO = 7
ZERO_TO_PAS = 8
STR_TO_LONG = 9
STR_TO_NUM = 10
STR_TO_BOOL = 11
STR_TO_EXT = 12
LONG_TO_STR = 13
NUM_TO_STR = 14
NUM_TO_HEX = 15
BOOL_TO_STR = 16
EXT_TO_STR = 17
GET_GLOBAL = 18
SET_GLOBAL = 19
GET_FIELD_BY_NAME = 20
GET_FIELD_BY_NUM = 21
GET_FIELD_BY_ID = 22
SET_FIELD_BY_NAME = 23
SET_FIELD_BY_NUM = 24
SET_FIELD_BY_ID = 25
STRING_EQUAL = 26
RETURN_TO_PAS = 27
SCAN_TO_RETURN = 28
GET_MASK_AND_DATA = 29
CHANGED_MASK_AND_DATA = 30
SCAN_TO_ZERO = 39
# HyperCard 2.x. The numbers were read off the glue routines inside the
# externals of the test collection (their MacsBug names say which is which);
# the ones marked ? are seen in a single external only.
ZERO_TERM_HANDLE = 32
SEND_HC_EVENT = 34
BEGIN_X_SOUND = 36
END_X_SOUND = 37
RUN_HANDLER = 38
GET_X_RES_INFO = 40         # ?
GET_FILE_PATH = 41
FRONT_DOC_WINDOW = 42
POINT_TO_STR = 43
RECT_TO_STR = 44
STR_TO_POINT = 45
STR_TO_RECT = 46
GET_FIELD_TE = 47           # ?
SET_FIELD_TE = 48           # ?
NOTIFY = 53
SHOW_HC_ALERT = 54


def to_mac(text):
    return str(text).replace("\r\n", "\r").replace("\n", "\r").encode("mac_roman", "replace")


def from_mac(raw):
    return raw.decode("mac_roman", "replace").replace("\r", "\n")


def _ext80_to_float(b):
    exp, mant = struct.unpack(">HQ", b[:10])
    sign = -1.0 if exp & 0x8000 else 1.0
    exp &= 0x7FFF
    if exp == 0 and mant == 0:
        return 0.0 * sign
    if exp == 0x7FFF:
        return sign * (float("inf") if (mant << 1) & 0xFFFFFFFFFFFFFFFF == 0 else float("nan"))
    return sign * mant * 2.0 ** (exp - 16383 - 63)


def _float_to_ext80(x):
    import math
    if x != x:
        return struct.pack(">HQ", 0x7FFF, 0xC000000000000000)
    sign = 0x8000 if math.copysign(1.0, x) < 0 else 0
    x = abs(x)
    if x == float("inf"):
        return struct.pack(">HQ", sign | 0x7FFF, 0)
    if x == 0:
        return struct.pack(">HQ", sign, 0)
    m, e = math.frexp(x)            # x = m * 2**e, 0.5 <= m < 1
    mant = int(m * (1 << 64)) & 0xFFFFFFFFFFFFFFFF
    return struct.pack(">HQ", sign | (e - 1 + 16383), mant)


class XCmdRunner:
    def __init__(self, it, name, kind):
        self.it = it
        self.name = name
        self.kind = kind
        self.m = Machine()
        from .toolbox import Toolbox
        self.tb = Toolbox(self.m, it)
        self.callbacks = collections.Counter()

    # -- strings in emulated memory ------------------------------------------------------------
    def zhandle(self, text):
        data = to_mac(text) + b"\0"
        return self.m.heap.new_handle(len(data), data=data)

    def handle_text(self, h):
        if not h or not self.m.heap.is_handle(h):
            return ""
        p = self.m.heap.deref(h)
        n = self.m.heap.size(h)
        raw = self.m.rbytes(p, n)
        i = raw.find(b"\0")
        return from_mac(raw[:i] if i >= 0 else raw)

    # -- running ------------------------------------------------------------------------------
    def run(self, code, params):
        m = self.m
        ch = m.heap.new_handle(len(code), data=code)
        m.heap.set_flags(ch, 0x80 | 0x20)       # locked resource
        self.tb.register_code(ch, self.kind, self.name)
        blk = m.heap.new_ptr(BLOCK_SIZE, clear=True)
        self.blk = blk
        params = list(params)[:16]
        m.w16(blk, len(params))
        for i, p in enumerate(params):
            m.w32(blk + 2 + 4 * i, self.zhandle(p))
        m.w32(blk + 72, mac68k.CALLBACK_STUB)
        try:
            m.call(m.heap.deref(ch), (blk,), trap=self.tb.trap, callback=self.callback)
        finally:
            fm = getattr(self.tb, "fm", None)
            if fm is not None:
                fm.close_all()
        rv = m.r32(blk + 66)
        result = self.handle_text(rv) if rv else None
        passed = bool(m.r8(blk + 70))
        return result, passed

    # -- callbacks --------------------------------------------------------------------------------
    def arg(self, i):
        return self.m.r32(self.blk + 80 + 4 * i)

    def s32(self, i):
        v = self.arg(i)
        return v - (1 << 32) if v & 0x80000000 else v

    def out(self, i, v):
        self.m.w32(self.blk + 112 + 4 * i, v & 0xFFFFFFFF)

    def callback(self, m):
        blk = self.blk
        req = m.s16(blk + 76)
        m.w16(blk + 78, 0)
        self.callbacks[req] += 1
        if TRACE:
            a = [self.arg(i) for i in range(4)]
            extra = ""
            if req in (EVAL_EXPR, SEND_CARD_MESSAGE, SEND_HC_MESSAGE, GET_GLOBAL, SET_GLOBAL):
                extra = repr(self.pas(a[0]))
                if req == SET_GLOBAL:
                    extra += " = " + repr(self.zstr(self.m.heap.deref(a[1])))
            self.it.host.log("callback %d %s %s" % (req, " ".join("%08X" % v for v in a), extra))
        f = _CALLBACKS.get(req)
        if f is None:
            self.it.host.log("XCMD %s: HyperCard callback %d not implemented" % (self.name, req))
            m.w16(blk + 78, 2)
            return
        try:
            f(self)
        except (ht.HTError, ValueError) as e:
            self.it.host.log("XCMD %s: callback %d: %s" % (self.name, req, e))
            m.w16(blk + 78, 1)

    def pas(self, a):
        return from_mac(self.m.pstr(a))

    def zstr(self, a):
        return from_mac(self.m.cstr(a))

    def put_pas(self, a, text):
        self.m.wpstr(a, to_mac(text))


_CALLBACKS = {}


def _cb(*codes):
    def deco(f):
        for c in codes:
            _CALLBACKS[c] = f
        return f
    return deco


def _statement(it, src):
    """A message from the external runs in the handler that called it, as
    in HyperCard: after SendCardMessage("ask ..."), EvalExpr("it") sees the
    answer."""
    sc = ht.Script("on __xcmd__\n%s\nend __xcmd__" % src)
    h = sc.handlers.get("__xcmd__")
    if h:
        it.run_block(h.body)


def _eval(it, src):
    return it.eval_toks(ht.tokenize(src))


@_cb(SEND_CARD_MESSAGE)
def _send_card_message(r):
    _statement(r.it, r.pas(r.arg(0)))


@_cb(SEND_HC_MESSAGE)
def _send_hc_message(r):
    _statement(r.it, r.pas(r.arg(0)))


@_cb(EVAL_EXPR)
def _eval_expr(r):
    r.out(0, r.zhandle(_eval(r.it, r.pas(r.arg(0)))))


@_cb(STRING_LENGTH)
def _string_length(r):
    r.out(0, len(r.m.cstr(r.arg(0))))


@_cb(STRING_MATCH)
def _string_match(r):
    pat = r.m.pstr(r.arg(0)).lower()
    p = r.arg(1)
    text = r.m.cstr(p).lower()
    i = text.find(pat) if pat else -1
    r.out(0, p + i if i >= 0 else 0)


@_cb(ZERO_BYTES)
def _zero_bytes(r):
    n = r.arg(1)
    if 0 < n < 1 << 24:
        r.m.wbytes(r.arg(0), bytes(n))


@_cb(PAS_TO_ZERO)
def _pas_to_zero(r):
    raw = r.m.pstr(r.arg(0)) + b"\0"
    r.out(0, r.m.heap.new_handle(len(raw), data=raw))


@_cb(ZERO_TO_PAS)
def _zero_to_pas(r):
    r.m.wpstr(r.arg(1), r.m.cstr(r.arg(0)))


def _num_of(r, text):
    text = text.strip()
    r.out(0, 0)
    if not text:
        r.m.w16(r.blk + 78, 1)
        return 0
    if not ht.is_number(text):
        raise ValueError("not a number: %r" % text)
    return ht.to_num(text)


@_cb(STR_TO_LONG)
def _str_to_long(r):
    r.out(0, int(_num_of(r, r.pas(r.arg(0)))))


@_cb(STR_TO_NUM)
def _str_to_num(r):
    r.out(0, int(_num_of(r, r.pas(r.arg(0)))))


@_cb(STR_TO_BOOL)
def _str_to_bool(r):
    s = r.pas(r.arg(0)).strip().lower()
    if s not in ("true", "false"):
        raise ValueError("not true or false")
    r.out(0, 1 if s == "true" else 0)


@_cb(STR_TO_EXT)
def _str_to_ext(r):
    """Like SANE's str2num: the longest number at the start of the string
    (after spaces) counts, and a string with none is a NaN."""
    from . import sane
    raw = r.m.pstr(r.arg(0)).decode("mac_roman", "replace")
    _end, (sgn, exp, sig), _ok = sane.str2dec(raw, 0)
    if sig[:1] == "N":
        x = float("nan")
    elif sig[:1] == "I":
        x = float("inf")
    else:
        x = float("%se%d" % (sig, exp))
    r.m.wbytes(r.arg(1), _float_to_ext80(-x if sgn else x))


@_cb(LONG_TO_STR)
def _long_to_str(r):
    v = r.arg(0)
    r.put_pas(r.arg(1), str(v - (1 << 32) if v & 0x80000000 else v))


@_cb(NUM_TO_STR)
def _num_to_str(r):
    r.put_pas(r.arg(1), str(r.arg(0)))


@_cb(NUM_TO_HEX)
def _num_to_hex(r):
    v, n = r.arg(0), r.arg(1) & 0xFFFF
    r.put_pas(r.arg(2), ("%X" % v).rjust(n, "0")[-n:] if n else "%X" % v)


@_cb(BOOL_TO_STR)
def _bool_to_str(r):
    r.put_pas(r.arg(1), "true" if r.arg(0) & 0xFFFF else "false")


@_cb(EXT_TO_STR)
def _ext_to_str(r):
    x = _ext80_to_float(r.m.rbytes(r.arg(0), 10))
    r.put_pas(r.arg(1), ht.fmt_num(x))


@_cb(GET_GLOBAL)
def _get_global(r):
    name = r.pas(r.arg(0))
    r.out(0, r.zhandle(r.it.globals.get(name.lower(), "")))


@_cb(SET_GLOBAL)
def _set_global(r):
    name = r.pas(r.arg(0))
    r.it.globals[name.lower()] = r.handle_text(r.arg(1))


def _field_expr(r, which, v):
    # inArgs[0]: cardFieldFlag, a Boolean that Pascal glue stores in the
    # high byte and C glue in the low byte
    layer = "card" if r.arg(0) & 0xFF0000FF else "background"
    return "%s field %s" % (layer, v) if which != "id" else "%s field id %s" % (layer, v)


def _get_field(r, which, v):
    r.out(0, r.zhandle(_eval(r.it, _field_expr(r, which, v))))


def _set_field(r, which, v, h):
    it = r.it
    saved = it.globals.get("__hcx_xcmd_text")
    it.globals["__hcx_xcmd_text"] = r.handle_text(h)
    try:
        _statement(it, "global __hcx_xcmd_text\nput __hcx_xcmd_text into " + _field_expr(r, which, v))
    finally:
        if saved is None:
            it.globals.pop("__hcx_xcmd_text", None)
        else:
            it.globals["__hcx_xcmd_text"] = saved


@_cb(GET_FIELD_BY_NAME)
def _get_field_by_name(r):
    _get_field(r, "name", '"%s"' % r.pas(r.arg(1)))


@_cb(GET_FIELD_BY_NUM)
def _get_field_by_num(r):
    _get_field(r, "num", r.m.s16(r.blk + 80 + 4 + 2))


@_cb(GET_FIELD_BY_ID)
def _get_field_by_id(r):
    _get_field(r, "id", r.m.s16(r.blk + 80 + 4 + 2))


@_cb(SET_FIELD_BY_NAME)
def _set_field_by_name(r):
    _set_field(r, "name", '"%s"' % r.pas(r.arg(1)), r.arg(2))


@_cb(SET_FIELD_BY_NUM)
def _set_field_by_num(r):
    _set_field(r, "num", r.m.s16(r.blk + 80 + 4 + 2), r.arg(2))


@_cb(SET_FIELD_BY_ID)
def _set_field_by_id(r):
    _set_field(r, "id", r.m.s16(r.blk + 80 + 4 + 2), r.arg(2))


@_cb(STRING_EQUAL)
def _string_equal(r):
    a = r.m.pstr(r.arg(0)).lower()
    b = r.m.pstr(r.arg(1)).lower()
    r.out(0, 1 if a == b else 0)


@_cb(RETURN_TO_PAS)
def _return_to_pas(r):
    p = r.arg(0)
    raw = r.m.cstr(p, 256)
    i = raw.find(b"\r")
    r.m.wpstr(r.arg(1), raw[:i] if i >= 0 else raw)


@_cb(SCAN_TO_RETURN)
def _scan_to_return(r):
    pp = r.arg(0)
    p = r.m.r32(pp)
    raw = r.m.cstr(p)
    i = raw.find(b"\r")
    r.m.w32(pp, p + (i if i >= 0 else len(raw)))


@_cb(SCAN_TO_ZERO)
def _scan_to_zero(r):
    pp = r.arg(0)
    p = r.m.r32(pp)
    r.m.w32(pp, p + len(r.m.cstr(p)))


# -- HyperCard 2.x -----------------------------------------------------------------------------------

def _nums(text, n):
    parts = [p.strip() for p in str(text).split(",")]
    if len(parts) != n or not all(ht.is_number(p) for p in parts):
        raise ValueError("not %d numbers: %r" % (n, text))
    return [int(ht.to_num(p)) for p in parts]


@_cb(ZERO_TERM_HANDLE)
def _zero_term_handle(r):
    heap, h = r.m.heap, r.arg(0)
    n = heap.size(h)
    if n == 0 or r.m.r8(heap.deref(h) + n - 1) != 0:
        heap.set_size(h, n + 1)
        r.m.w8(heap.deref(h) + n, 0)


@_cb(SEND_HC_EVENT, BEGIN_X_SOUND, END_X_SOUND, NOTIFY)
def _nothing(r):
    pass


@_cb(RUN_HANDLER)
def _run_handler(r):
    h = r.arg(0)
    text = from_mac(r.m.cstr(r.m.heap.deref(h), r.m.heap.size(h)))
    sc = ht.Script(text)
    for hd in sc.handlers.values():
        r.it.call(hd, None, [], "do")
        break


@_cb(FRONT_DOC_WINDOW)
def _front_doc_window(r):
    r.out(0, r.m.r32(r.m.r32(r.m.a(5))))       # thePort: the card window


@_cb(POINT_TO_STR)
def _point_to_str(r):
    p = r.arg(0)
    r.put_pas(r.arg(1), "%d,%d" % (r.m.s16(p + 2), r.m.s16(p)))


@_cb(RECT_TO_STR)
def _rect_to_str(r):
    p = r.arg(0)
    t, l, b, rt = (r.m.s16(p + 2 * i) for i in range(4))
    r.put_pas(r.arg(1), "%d,%d,%d,%d" % (l, t, rt, b))


@_cb(STR_TO_POINT)
def _str_to_point(r):
    h, v = _nums(r.pas(r.arg(0)), 2)
    p = r.arg(1)
    r.m.w16(p, v)
    r.m.w16(p + 2, h)


@_cb(STR_TO_RECT)
def _str_to_rect(r):
    l, t, rt, b = _nums(r.pas(r.arg(0)), 4)
    p = r.arg(1)
    for i, v in enumerate((t, l, b, rt)):
        r.m.w16(p + 2 * i, v)


def _field_for(r, card, fid, num, name):
    kind = "card" if card else "bkgnd"
    if fid:
        src = "%s field id %d" % (kind, fid)
    elif num:
        src = "%s field %d" % (kind, num)
    else:
        src = '%s field "%s"' % (kind, name.replace('"', ""))
    try:
        ref = ht.parse_ref(r.it, src)
    except ht.HTError:
        return None
    return ref if isinstance(ref, ht.Obj) and ref.t == "part" else None


@_cb(GET_FIELD_TE)
def _get_field_te(r):
    """A styled TextEdit record with a copy of the field's text and styles."""
    from hcx import xcmds
    m, heap = r.m, r.m.heap
    ref = _field_for(r, bool(r.arg(0) & 0xFF0000FF), r.s32(1), r.s32(2),
                     r.pas(r.arg(3)) if r.arg(3) else "")
    r.out(0, 0)
    if ref is None:
        return
    c = r.it.host.content_of(ref.a, ref.b, ref.c, card=ref.d)
    text = to_mac(c.text if c is not None else "")
    rec = xcmds._style_record(r.it, ref)
    nruns = int.from_bytes(rec[:2], "big")
    runs = rec[2:2 + 4 * nruns]
    o = 2 + 4 * nruns
    nstyles = int.from_bytes(rec[o:o + 2], "big")
    table = rec[o + 2:o + 2 + 18 * nstyles]
    p = ref.b
    hte = heap.new_handle(104, clear=True)
    htext = heap.new_handle(len(text), data=text)
    styletab = heap.new_handle(max(len(table), 18), data=table or bytes(18))
    lhtab = heap.new_handle(4 * max(nstyles, 1), clear=True)
    nullst = heap.new_handle(8, clear=True)
    hst = heap.new_handle(20 + len(runs), clear=True)
    sp = heap.deref(hst)
    m.w16(sp, nruns - 1)
    m.w16(sp + 2, nstyles)
    m.w32(sp + 4, styletab)
    m.w32(sp + 8, lhtab)
    m.w32(sp + 16, nullst)
    m.wbytes(sp + 20, runs)
    t = heap.deref(hte)
    rect = (p.top, p.left, p.bottom, p.right)
    for base in (0, 8):
        for i, v in enumerate(rect):
            m.w16(t + base + 2 * i, v)
    m.w16(t + 24, 0xFFFF)               # lineHeight, fontAscent: -1 = styled
    m.w16(t + 26, 0xFFFF)
    m.w16(t + 60, len(text))            # teLength
    m.w32(t + 62, htext)                # hText
    m.w8(t + 72, 0)                     # crOnly
    m.w32(t + 74, hst)                  # txFont+txFace: the style handle
    m.w16(t + 78, 1)                    # txMode
    m.w16(t + 80, 0xFFFF)               # txSize -1: styled
    m.w32(t + 82, m.r32(m.r32(m.a(5))))  # inPort
    r.out(0, hte)


@_cb(SET_FIELD_TE)
def _set_field_te(r):
    """The field gets the text and styles of a TextEdit record."""
    from hcx import xcmds
    m, heap = r.m, r.m.heap
    ref = _field_for(r, bool(r.arg(0) & 0xFF0000FF), r.s32(1), r.s32(2),
                     r.pas(r.arg(3)) if r.arg(3) else "")
    hte = r.arg(4)
    if ref is None or not heap.is_handle(hte):
        m.w16(r.blk + 78, 1)
        return
    t = heap.deref(hte)
    n = m.r16(t + 60)
    text = from_mac(m.rbytes(heap.deref(m.r32(t + 62)), n))
    r.it.put_into(ref, text)
    if m.s16(t + 80) == -1:
        sp = heap.deref(m.r32(t + 74))
        nruns, nstyles = m.r16(sp), m.r16(sp + 2)
        rec = ((nruns + 1).to_bytes(2, "big") + m.rbytes(sp + 20, 4 * (nruns + 1)) +
               nstyles.to_bytes(2, "big") + m.rbytes(heap.deref(m.r32(sp + 4)), 18 * nstyles))
        xcmds._apply_style_record(r.it, ref, rec)


@_cb(SHOW_HC_ALERT)
def _show_hc_alert(r):
    r.it.host.log("XCMD %s: HyperCard alert %d %r" % (r.name, r.arg(0), r.pas(r.arg(1))))
    r.out(0, 1)


# -- entry point for the interpreter ------------------------------------------------------------

def find_external(it, name, kind):
    """The code of the stack's XCMD/XFCN named `name`, searching the
    resource chain, or None."""
    low = name.lower()
    try:
        chain = it.host.resource_chain()
    except Exception:  # noqa: BLE001
        return None
    for res in chain:
        for r in res:
            if r[0] == kind and r[2].lower() == low:
                return r[3]
    return None


def emulate(it, name, kind, params, code=None):
    """Runs the stack's own external in the emulator. Returns a dict with
    result, passed, error (None when it ran to the end), the traps and
    callbacks it used, and how many instructions it took; None when the
    stack has no external of that name."""
    if code is None:
        code = find_external(it, name, kind)
    if code is None:
        return None
    runner = XCmdRunner(it, name, kind)
    out = {"result": None, "passed": False, "error": None}
    saved = it.frame
    if saved is None:       # run from a tool, not from a handler: give it one
        it.frame = ht.Frame(None, [], name)
    try:
        out["result"], out["passed"] = runner.run(code, params)
    except Unimplemented as e:
        out["error"] = "not emulated: %s" % e.what
    except EmuError as e:
        out["error"] = "stopped: %s" % e
    finally:
        it.frame = saved
    out["traps"] = dict(runner.tb.used)
    out["callbacks"] = dict(runner.callbacks)
    out["instructions"] = runner.m.lib.hcx_icount()
    return out
