"""SANE, the Mac's floating point package (Pack4, FP68K) and its decimal
conversions (Pack7 selectors 2-4), for mac68k. Arithmetic is done with
Python floats, so extended precision is only as good as a double: enough
to see what an external does with numbers, which is all tools/xemu needs."""

import math
import re
import struct

from .mac68k import SR, Unimplemented

# operand formats, in bits 11-13 of the opword
EXT, DBL, SGL, INT, LONG, COMP = 0x0000, 0x0800, 0x1000, 0x2000, 0x2800, 0x3000
_SIZES = {EXT: 10, DBL: 8, SGL: 4, INT: 2, LONG: 4, COMP: 8}


def read_ext(m, a):
    se = m.r16(a)
    mant = int.from_bytes(m.rbytes(a + 2, 8), "big")
    sign, exp = se >> 15, se & 0x7FFF
    if exp == 0x7FFF:
        v = math.inf if mant & ((1 << 63) - 1) == 0 else math.nan
    else:
        v = math.ldexp(mant, exp - 16383 - 63) if mant else 0.0
    return -v if sign else v


def write_ext(m, a, v):
    sign = 0x8000 if math.copysign(1.0, v) < 0 else 0
    v = abs(v)
    if math.isnan(v):
        se, mant = 0x7FFF, 0x4000000000000000 | 1 << 62
    elif math.isinf(v):
        se, mant = 0x7FFF, 0
    elif v == 0:
        se, mant = 0, 0
    else:
        f, e = math.frexp(v)            # v = f * 2**e, 0.5 <= f < 1
        mant = int(f * (1 << 64))       # exact: a double has 53 bits
        se = e - 1 + 16383
    m.w16(a, se | sign)
    m.wbytes(a + 2, mant.to_bytes(8, "big"))


def read(m, a, fmt):
    if fmt == EXT:
        return read_ext(m, a)
    if fmt == DBL:
        return struct.unpack(">d", m.rbytes(a, 8))[0]
    if fmt == SGL:
        return struct.unpack(">f", m.rbytes(a, 4))[0]
    if fmt == INT:
        return float(m.s16(a))
    if fmt == LONG:
        return float(m.s32(a))
    if fmt == COMP:
        n = int.from_bytes(m.rbytes(a, 8), "big", signed=True)
        return math.nan if n == -(1 << 63) else float(n)
    raise Unimplemented("SANE format %04X" % fmt)


def _to_int(v, bits):
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if math.isnan(v) or math.isinf(v):
        return lo
    n = round(v)                        # round to nearest even, SANE's default
    return lo if n < lo or n > hi else n


def write(m, a, fmt, v):
    if fmt == EXT:
        write_ext(m, a, v)
    elif fmt == DBL:
        m.wbytes(a, struct.pack(">d", v))
    elif fmt == SGL:
        try:
            m.wbytes(a, struct.pack(">f", v))
        except OverflowError:
            m.wbytes(a, struct.pack(">f", math.copysign(math.inf, v)))
    elif fmt == INT:
        m.w16(a, _to_int(v, 16) & 0xFFFF)
    elif fmt == LONG:
        m.w32(a, _to_int(v, 32) & 0xFFFFFFFF)
    elif fmt == COMP:
        m.wbytes(a, (_to_int(v, 64) & ((1 << 64) - 1)).to_bytes(8, "big"))
    else:
        raise Unimplemented("SANE format %04X" % fmt)


# -- decimal records: sgn (word), exp (word), sig (Pascal string, 20 digits) ---------------

def read_decimal(m, a):
    sgn = m.r8(a) & 1
    exp = m.s16(a + 2)
    sig = m.pstr(a + 4).decode("latin-1")
    if sig[:1] == "N":
        v = math.nan
    elif sig[:1] == "I":
        v = math.inf
    elif not sig or not sig.isdigit():
        v = 0.0
    else:
        try:
            v = float("%se%d" % (sig, exp))
        except OverflowError:
            v = math.inf
    return -v if sgn else v


def write_decimal(m, a, sgn, exp, sig):
    m.w16(a, 0x0100 if sgn else 0)
    m.w16(a + 2, exp & 0xFFFF)
    m.wpstr(a + 4, sig.encode("latin-1")[:20], 20)


_NUM = re.compile(r"[+-]?(?:(\d+)\.?(\d*)|\.(\d+))(?:[eE]([+-]?\d+))?")


def str2dec(s, i):
    """SANE's str2dec on s from index i (0-based): returns (new index,
    (sgn, exp, sig), validPrefix)."""
    j = i
    while j < len(s) and s[j] in " \t":
        j += 1
    sgn = 0
    k = j
    if k < len(s) and s[k] in "+-":
        sgn = s[k] == "-"
        k += 1
    word = s[k:k + 3].upper()
    if word == "INF":
        return k + 3, (sgn, 0, "I"), k + 3 >= len(s)
    if word == "NAN":
        e = k + 3
        if e < len(s) and s[e] == "(":
            close = s.find(")", e)
            e = close + 1 if close >= 0 else len(s)
        return e, (sgn, 0, "N0000"), e >= len(s)
    mt = _NUM.match(s, j)
    if not mt:
        rest = s[j:]
        valid = rest in ("", "+", "-", ".", "+.", "-.") or bool(re.fullmatch(r"[+-]?(I|IN|N|NA)", rest.upper()))
        return i, (0, 0, "N0011"), valid
    end = mt.end()
    # an exponent marker with no digits isn't part of the number
    whole, frac, frac_only, e = mt.group(1) or "", mt.group(2) or "", mt.group(3) or "", mt.group(4)
    digits = (whole + (frac or frac_only)).lstrip("0")
    exp = -len(frac or frac_only) + (int(e) if e else 0)
    if not digits:
        digits, exp = "0", 0
    else:
        stripped = digits.rstrip("0")
        exp += len(digits) - len(stripped)
        digits = stripped
    if len(digits) > 19:
        exp += len(digits) - 19
        digits = digits[:19]
    rest = s[end:]
    valid = end >= len(s) or bool(re.fullmatch(r"[eE][+-]?", rest))
    return end, (sgn, exp, digits), valid


def pack7(tb, sel):
    """Pack7 selectors 2 (PStr2Dec) and 4 (CStr2Dec); the caller has
    popped the selector."""
    m = tb.m
    valid_p = m.pop32()
    d = m.pop32()
    idx_p = m.pop32()
    sp = m.pop32()
    s = (m.pstr(sp) if sel == 2 else m.cstr(sp)).decode("mac_roman", "replace")
    base = 1 if sel == 2 else 0
    i = m.s16(idx_p) - base
    end, (sgn, exp, sig), valid = str2dec(s, max(i, 0))
    m.w16(idx_p, (end + base) & 0xFFFF)
    write_decimal(m, d, sgn, exp, sig)
    m.w8(valid_p, 1 if valid else 0)


def _dec_out(v):
    if math.isnan(v):
        return (0, 0, "N0011")
    sgn = 1 if math.copysign(1.0, v) < 0 else 0
    v = abs(v)
    if math.isinf(v):
        return (sgn, 0, "I")
    if v == 0:
        return (sgn, 0, "0")
    s = "%.19e" % v
    mant, e = s.split("e")
    digits = mant.replace(".", "").rstrip("0") or "0"
    return (sgn, int(e) - (len(digits) - 1), digits)


def _set_cc(m, n=0, z=0, v=0, c=0, x=0):
    sr = m.reg(SR) & 0xFFE0
    m.set_reg(SR, sr | (x << 4) | (n << 3) | (z << 2) | (v << 1) | c)


def pack4(tb):
    """FP68K: the opword is on top of the stack, then the destination
    address, then the source (for two-operand operations)."""
    m = tb.m
    opword = m.pop16()
    op, fmt = opword & 0x1F, opword & 0x3800
    if op in (0x01, 0x03, 0x05, 0x07, 0x15, 0x17, 0x1B):    # environment: one address
        dst = m.pop32()
        if op in (0x03, 0x07, 0x15):
            m.w16(dst, 0)
        elif op == 0x1B:                                     # test exception
            m.w16(dst, 0)
        return
    if op in (0x0D, 0x0F, 0x12, 0x14, 0x16, 0x1A):          # one operand, in place
        dst = m.pop32()
        x = read_ext(m, dst)
        if op == 0x0D:
            x = -x
        elif op == 0x0F:
            x = abs(x)
        elif op == 0x12:
            x = math.sqrt(x) if x >= 0 else math.nan
        elif op == 0x14:
            x = float(round(x)) if math.isfinite(x) else x
        elif op == 0x16:
            x = float(math.trunc(x)) if math.isfinite(x) else x
        elif op == 0x1A:
            x = float(math.frexp(x)[1] - 1) if x else -math.inf
        write_ext(m, dst, x)
        return
    if op == 0x0B:                                           # binary to decimal
        dst = m.pop32()
        src = m.pop32()
        m.pop32()                                            # decform
        sgn, exp, sig = _dec_out(read(m, src, fmt))
        write_decimal(m, dst, sgn, exp, sig)
        return
    dst = m.pop32()
    src = m.pop32()
    if op == 0x09:                                           # decimal to binary
        write(m, dst, fmt, read_decimal(m, src))
        return
    if op == 0x1C:                                           # class
        v = read(m, src, fmt)
        cls = 2 if math.isnan(v) else 3 if math.isinf(v) else 4 if v == 0 else 5
        m.w16(dst, (cls if math.copysign(1.0, v) > 0 else -cls) & 0xFFFF)
        return
    if op == 0x10:                                           # extended to format
        write(m, dst, fmt, read_ext(m, src))
        return
    a = read(m, src, fmt)
    if op == 0x0E:                                           # format to extended
        write_ext(m, dst, a)
        return
    b = read_ext(m, dst)
    if op in (0x08, 0x0A):                                   # compare dst with src
        if math.isnan(a) or math.isnan(b):
            _set_cc(m, v=1)
        elif b < a:
            _set_cc(m, n=1, c=1, x=1)
        elif b == a:
            _set_cc(m, z=1)
        else:
            _set_cc(m)
        return
    try:
        if op == 0x00:
            r = b + a
        elif op == 0x02:
            r = b - a
        elif op == 0x04:
            r = b * a
        elif op == 0x06:
            r = b / a if a else (math.nan if b == 0 or math.isnan(b) else math.copysign(math.inf, b) * math.copysign(1, a))
        elif op == 0x0C:
            r = math.remainder(b, a) if a else math.nan
        elif op == 0x11:
            r = math.copysign(a, b)
        elif op == 0x18:
            r = math.ldexp(b, int(a))
        else:
            raise Unimplemented("Pack4 (SANE) opword %04X" % opword)
    except OverflowError:
        r = math.inf
    write_ext(m, dst, r)
