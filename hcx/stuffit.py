"""StuffIt archives (.sit), the usual way Mac files were shared in the 1990s.

Reads classic StuffIt archives ("SIT!", StuffIt 1.5 to 4.x) and StuffIt 5
archives, with the compression methods they use: none (0), RLE (1),
LZW (2), Huffman (3), LZAH (5), MW (8), LZ+Huffman (13) and Arsenic (15).
Encrypted entries and the rare method 14 are skipped.

The decoders are a Python translation of the ones in The Unarchiver's
XADMaster library (XADStuffItParser, XADStuffIt5Parser,
XADStuffIt13Handle, XADStuffItArsenicHandle, XADStuffItHuffmanHandle,
XADCompressHandle, XADRLE90Handle, XADPrefixCode, BWT.c and the libxad
SIT_lzah and SIT_mw code in XADStuffItOldHandles; Copyright
(c) MacPaw Way Ltd. and Dag Agren; LGPL 2.1 or later), including the
tables of method 13 and Arsenic's randomization table."""

import struct
import zlib

SIT5_MAGIC = b"StuffIt (c)1997-"


class StuffItError(Exception):
    pass


def is_stuffit(data):
    if len(data) >= 14 and data[10:14] == b"rLau":
        if data[:4] == b"SIT!":
            return True
        if data[:2] == b"ST" and (data[2:3].isdigit() and data[3:4].isdigit()
                                 or data[2:3] == b"i" and (data[3:4] == b"n" or data[3:4].isdigit())):
            return True
    return len(data) >= 100 and data[:16] == SIT5_MAGIC and b"Aladdin Systems" in data[20:60]


# -- CRC ------------------------------------------------------------------------------------------

def _crc_table(poly):
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ poly if c & 1 else c >> 1
        t.append(c)
    return t


_CRC16 = _crc_table(0xA001)


def crc16(data, crc=0):
    t = _CRC16
    for b in data:
        crc = t[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc


# -- bit readers ----------------------------------------------------------------------------------

class _BitsLE:
    """Bits taken from the low end of each byte first."""

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.buf = 0
        self.n = 0

    def _fill(self, k):
        while self.n < k:
            b = self.data[self.pos] if self.pos < len(self.data) else 0
            self.pos += 1
            self.buf |= b << self.n
            self.n += 8

    def peek(self, k):
        if self.n < k:
            self._fill(k)
        return self.buf & ((1 << k) - 1)

    def skip(self, k):
        if self.n < k:
            self._fill(k)
        self.buf >>= k
        self.n -= k

    def read(self, k):
        if k == 0:
            return 0
        v = self.peek(k)
        self.skip(k)
        return v

    def eof(self):
        return self.pos >= len(self.data) + 4


class _BitsBE:
    """Bits taken from the high end of each byte first."""

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.buf = 0
        self.n = 0

    def read(self, k):
        n = self.n
        if n < k:
            need = ((k - n + 7) >> 3) + 8
            chunk = self.data[self.pos:self.pos + need]
            self.pos += need
            if len(chunk) < need:
                chunk = bytes(chunk) + bytes(need - len(chunk))
            self.buf = (self.buf << (need << 3)) | int.from_bytes(chunk, "big")
            n += need << 3
        n -= k
        self.n = n
        v = self.buf >> n
        self.buf &= (1 << n) - 1
        return v

    def bit(self):
        return self.read(1)


# -- prefix codes ---------------------------------------------------------------------------------

class _Code:
    """A prefix code read from a low-bit-first stream. Codes are stored high bit first
    (the first bit read is the code's highest bit), as in XADPrefixCode."""

    TABLE_BITS = 10

    def __init__(self):
        self.codes = {}      # (length, code) -> value
        self.maxlen = 0
        self.table = None

    @classmethod
    def from_lengths(cls, lengths, maxlength=32):
        c = cls()
        code = 0
        left = sum(1 for x in lengths if x > 0)
        for length in range(1, maxlength + 1):
            for i, l in enumerate(lengths):
                if l != length:
                    continue
                c.add(i, code, length)
                code += 1
                left -= 1
                if left == 0:
                    return c
            code <<= 1
        return c

    def add(self, value, code, length):
        self.codes[(length, code)] = value
        self.maxlen = max(self.maxlen, length)
        self.table = None

    def add_low_bit_first(self, value, code, length):
        rev = 0
        for i in range(length):
            rev = (rev << 1) | ((code >> i) & 1)
        self.add(value, rev, length)

    def _build(self):
        tb = min(self.maxlen, self.TABLE_BITS) or 1
        table = [None] * (1 << tb)
        for (length, code), value in self.codes.items():
            if length > tb:
                continue
            rev = 0
            for i in range(length):
                rev |= ((code >> (length - 1 - i)) & 1) << i
            step = 1 << length
            for k in range(rev, 1 << tb, step):
                table[k] = (length, value)
        self.table = table
        self.tb = tb

    def decode(self, bits):
        if self.table is None:
            self._build()
        e = self.table[bits.peek(self.tb)]
        if e is not None:
            bits.skip(e[0])
            return e[1]
        code = 0
        for length in range(1, self.maxlen + 1):
            code = (code << 1) | bits.read(1)
            v = self.codes.get((length, code))
            if v is not None:
                return v
        raise StuffItError("bad prefix code")


# -- method 1: RLE --------------------------------------------------------------------------------

def _rle90(data, size):
    out = bytearray()
    last = 0
    i, n = 0, len(data)
    while i < n and len(out) < size:
        b = data[i]
        i += 1
        if b != 0x90:
            out.append(b)
            last = b
            continue
        c = data[i] if i < n else 0
        i += 1
        if c == 0:
            out.append(0x90)
            last = 0x90
        elif c == 1:
            raise StuffItError("bad RLE data")
        else:
            out += bytes((last,)) * (c - 1)
    return bytes(out[:size])


# -- method 2: LZW (Unix compress, block mode, 14 bits) --------------------------------------------

def _lzw(data, size, maxbits=14):
    bits = _BitsLE(data)
    out = bytearray()
    maxcode = 1 << maxbits

    def reset():
        return [bytes((i,)) for i in range(256)] + [b""]

    table = reset()
    width = 9
    prev = None
    count = 0
    while len(out) < size and not bits.eof():
        code = bits.read(width)
        count += 1
        if code == 256:
            # codes after a clear start at a multiple of 8 codes of the old width
            if count % 8:
                bits.read(width * (8 - count % 8))
            table = reset()
            width = 9
            prev = None
            count = 0
            continue
        if code < len(table):
            entry = table[code]
            if prev is not None and len(table) < maxcode:
                table.append(prev + entry[:1])
        elif code == len(table) and prev is not None:
            entry = prev + prev[:1]
            if len(table) < maxcode:
                table.append(entry)
        else:
            raise StuffItError("bad LZW code")
        out += entry
        prev = entry
        if len(table) >= (1 << width) and width < maxbits:
            width += 1
            count = 0
    return bytes(out[:size])


# -- method 3: Huffman with the tree in the stream --------------------------------------------------

def _huffman(data, size):
    bits = _BitsBE(data)
    # tree as nested lists: leaf = int, node = [zero, one]

    def parse():
        if bits.bit():
            return bits.read(8)
        zero = parse()
        one = parse()
        return [zero, one]

    tree = parse()
    out = bytearray()
    if isinstance(tree, int):
        return bytes((tree,)) * size
    while len(out) < size:
        node = tree
        while not isinstance(node, int):
            node = node[bits.bit()]
        out.append(node)
    return bytes(out)


# -- method 13: LZSS + Huffman ----------------------------------------------------------------------

from .stuffit13 import (FIRST_LENGTHS, SECOND_LENGTHS, OFFSET_LENGTHS,  # noqa: E402
                        META_CODES, META_LENGTHS)

_STATIC13 = {}


def _static13(n):
    if n not in _STATIC13:
        _STATIC13[n] = (_Code.from_lengths(FIRST_LENGTHS[n]), _Code.from_lengths(SECOND_LENGTHS[n]),
                        _Code.from_lengths(OFFSET_LENGTHS[n]))
    return _STATIC13[n]


def _meta13():
    if "meta" not in _STATIC13:
        m = _Code()
        for i in range(37):
            m.add_low_bit_first(i, META_CODES[i], META_LENGTHS[i])
        _STATIC13["meta"] = m
    return _STATIC13["meta"]


def _parse_code13(bits, n, meta):
    lengths = [0] * (n + 70)
    length = 0
    i = 0
    while i < n:
        val = meta.decode(bits)
        if val == 31:
            length = -1
        elif val == 32:
            length += 1
        elif val == 33:
            length -= 1
        elif val == 34:
            if bits.read(1):
                lengths[i] = length
                i += 1
        elif val == 35:
            k = bits.read(3) + 2
            for _ in range(k):
                lengths[i] = length
                i += 1
        elif val == 36:
            k = bits.read(6) + 10
            for _ in range(k):
                lengths[i] = length
                i += 1
        else:
            length = val + 1
        lengths[i] = length
        i += 1
    return _Code.from_lengths(lengths[:n])


def _method13(data, size):
    bits = _BitsLE(data)
    val = bits.read(8)
    kind = val >> 4
    if kind == 0:
        meta = _meta13()
        first = _parse_code13(bits, 321, meta)
        second = first if val & 0x08 else _parse_code13(bits, 321, meta)
        offset = _parse_code13(bits, (val & 0x07) + 10, meta)
    elif kind < 6:
        first, second, offset = _static13(kind - 1)
    else:
        raise StuffItError("bad method 13 header")
    out = bytearray()
    code = first
    while len(out) < size:
        v = code.decode(bits)
        if v < 0x100:
            out.append(v)
            code = first
            continue
        code = second
        if v < 0x13E:
            length = v - 0x100 + 3
        elif v == 0x13E:
            length = bits.read(10) + 65
        elif v == 0x13F:
            length = bits.read(15) + 65
        else:
            break
        bl = offset.decode(bits)
        if bl == 0:
            dist = 1
        elif bl == 1:
            dist = 2
        else:
            dist = (1 << (bl - 1)) + bits.read(bl - 1) + 1
        start = len(out) - dist
        if start < 0:
            # the window starts filled with zeros
            pad = min(-start, length)
            out += bytes(pad)
            length -= pad
            start = len(out) - dist
        if dist >= length:
            out += out[start:start + length]
        else:
            for k in range(length):
                out.append(out[start + k])
    return bytes(out[:size])


# -- method 15: Arsenic (arithmetic coding + BWT) -----------------------------------------------------

from .stuffit13 import ARSENIC_RANDOM  # noqa: E402


class _Model:
    __slots__ = ("increment", "limit", "syms", "freq", "total")

    def __init__(self, first, last, increment, limit):
        self.increment = increment
        self.limit = limit
        self.syms = list(range(first, last + 1))
        self.reset()

    def reset(self):
        self.freq = [self.increment] * len(self.syms)
        self.total = self.increment * len(self.syms)


class _Arith:
    NUMBITS = 26
    ONE = 1 << 25
    HALF = 1 << 24

    def __init__(self, bits):
        self.bits = bits
        self.range = self.ONE
        self.code = bits.read(self.NUMBITS)

    def symbol(self, m):
        rng = self.range
        code = self.code
        freq = m.freq
        total = m.total
        renorm = rng // total
        target = code // renorm
        cum = 0
        n = 0
        last = len(freq) - 1
        while n < last:
            f = freq[n]
            if cum + f > target:
                break
            cum += f
            n += 1
        size = freq[n]
        low = renorm * cum
        code -= low
        if cum + size == total:
            rng -= low
        else:
            rng = size * renorm
        if rng <= 0x1000000:
            # renormalize: shift in all the bits needed at once
            k = 25 - rng.bit_length()
            if (rng << k) <= 0x1000000:
                k += 1
            rng <<= k
            code = (code << k) | self.bits.read(k)
        self.range = rng
        self.code = code
        inc = m.increment
        freq[n] += inc
        total += inc
        if total > m.limit:
            total = 0
            for i in range(len(freq)):
                freq[i] = (freq[i] + 1) >> 1
                total += freq[i]
        m.total = total
        return m.syms[n]

    def bitstring(self, m, k):
        r = 0
        for i in range(k):
            if self.symbol(m):
                r |= 1 << i
        return r


def _arsenic(data, size):
    dec = _Arith(_BitsBE(data))
    initial = _Model(0, 1, 1, 256)
    selector = _Model(0, 10, 8, 1024)
    mtfm = [_Model(2, 3, 8, 1024), _Model(4, 7, 4, 1024), _Model(8, 15, 4, 1024), _Model(16, 31, 4, 1024),
            _Model(32, 63, 2, 1024), _Model(64, 127, 2, 1024), _Model(128, 255, 1, 1024)]
    if dec.bitstring(initial, 8) != ord("A") or dec.bitstring(initial, 8) != ord("s"):
        raise StuffItError("not Arsenic data")
    blockbits = dec.bitstring(initial, 4) + 9
    blocksize = 1 << blockbits
    end = dec.symbol(initial)
    out = bytearray()
    crc = None
    while not end and len(out) < size:
        mtf = list(range(256))
        randomized = dec.symbol(initial)
        tindex = dec.bitstring(initial, blockbits)
        block = bytearray()
        while True:
            sel = dec.symbol(selector)
            if sel in (0, 1):
                state, zeros = 1, 0
                while sel < 2:
                    zeros += state if sel == 0 else 2 * state
                    state *= 2
                    sel = dec.symbol(selector)
                if len(block) + zeros > blocksize:
                    raise StuffItError("bad Arsenic block")
                block += bytes((mtf[0],)) * zeros
            if sel == 10:
                break
            sym = 1 if sel == 2 else dec.symbol(mtfm[sel - 3])
            if len(block) >= blocksize:
                raise StuffItError("bad Arsenic block")
            b = mtf[sym]
            del mtf[sym]
            mtf.insert(0, b)
            block.append(b)
        n = len(block)
        if tindex >= n:
            raise StuffItError("bad Arsenic block")
        selector.reset()
        for m in mtfm:
            m.reset()
        if dec.symbol(initial):
            crc = dec.bitstring(initial, 32)     # CRC-32 of the whole file
            end = True
        # inverse BWT
        counts = [0] * 256
        for b in block:
            counts[b] += 1
        cum = [0] * 256
        t = 0
        for i in range(256):
            cum[i] = t
            t += counts[i]
        transform = [0] * n
        for i, b in enumerate(block):
            transform[cum[b]] = i
            cum[b] += 1
        # unscramble, undo the randomization, then the run-length step
        randidx = 0
        randcount = ARSENIC_RANDOM[0]
        count = 0
        last = 0
        rep = False
        for i in range(n):
            tindex = transform[tindex]
            b = block[tindex]
            if randomized and randcount == i:
                b ^= 1
                randidx = (randidx + 1) & 255
                randcount += ARSENIC_RANDOM[randidx]
            if rep:
                rep = False
                count = 0
                if b:
                    out += bytes((last,)) * b
                continue
            out.append(b)
            if b == last:
                count += 1
                if count == 4:
                    rep = True
            else:
                count = 1
                last = b
    if crc is not None and zlib.crc32(out) != crc:
        raise StuffItError("CRC error")
    return bytes(out[:size])


# -- method 5: LZAH (LHarc's adaptive Huffman + LZSS, 4 KB window) ------------------------------

_LZAH_N = 314
_LZAH_T = 2 * _LZAH_N - 1


def _lzah_tables():
    # first 6 bits of the match offset: a static code read 8 bits at a time
    code, length = [], []
    groups = ((3, 1, 32), (4, 3, 16), (5, 8, 8), (6, 12, 4), (7, 24, 2), (8, 16, 1))
    hc = 0
    for bits, ncodes, repeat in groups:
        for _ in range(ncodes):
            code.extend([hc] * repeat)
            length.extend([bits] * repeat)
            hc += 4
    return code, length


_LZAH_CODE, _LZAH_LEN = _lzah_tables()


def _lzah(data, size):
    N, T = _LZAH_N, _LZAH_T
    bits = _BitsBE(data)
    read = bits.read
    freq = [0] * (T + 1)
    fwd = [0] * (T + 1)
    back = [0] * (T + N + 1)
    for i in range(N):
        freq[i] = 1
        fwd[i] = i + T
        back[i + T] = i
    i, j = 0, N
    while j < T:
        freq[j] = freq[i] + freq[i + 1]
        fwd[j] = i
        back[i] = j
        back[i + 1] = j
        i += 2
        j += 1
    freq[T] = 0xFFFF
    back[T - 1] = 0
    buf = bytearray(b" " * 4096)
    bp = 0
    out = bytearray()
    limit = len(data) * 8 + 64
    consumed = 0
    while len(out) < size:
        ch = fwd[T - 1]
        while ch < T:
            ch = fwd[ch + read(1)]
            consumed += 1
        if consumed > limit:
            raise StuffItError("short data")
        ch -= T
        if freq[T - 1] >= 0x8000:
            j = 0
            for i in range(T):
                if fwd[i] >= T:
                    freq[j] = (freq[i] + 1) >> 1
                    fwd[j] = fwd[i]
                    j += 1
            j = N
            for i in range(0, T, 2):
                l = freq[i] + freq[i + 1]
                freq[j] = l
                k = j - 1
                while l < freq[k]:
                    k -= 1
                k += 1
                freq[k + 1:j + 1] = freq[k:j]
                freq[k] = l
                fwd[k + 1:j + 1] = fwd[k:j]
                fwd[k] = i
                j += 1
            for i in range(T):
                k = fwd[i]
                back[k] = i
                if k < T:
                    back[k + 1] = i
        i = back[ch + T]
        while True:
            freq[i] += 1
            jv = freq[i]
            i1 = i + 1
            if freq[i1] < jv:
                i1 += 1
                while freq[i1] < jv:
                    i1 += 1
                i1 -= 1
                freq[i] = freq[i1]
                freq[i1] = jv
                jj = fwd[i]
                back[jj] = i1
                if jj < T:
                    back[jj + 1] = i1
                fwd[i] = fwd[i1]
                fwd[i1] = jj
                jj = fwd[i]
                back[jj] = i
                if jj < T:
                    back[jj + 1] = i
                i = i1
            i = back[i]
            if i == 0:
                break
        if ch < 256:
            out.append(ch)
            buf[bp] = ch
            bp = (bp + 1) & 0xFFF
        else:
            byte = read(8)
            skip = _LZAH_LEN[byte] - 2
            offs = (_LZAH_CODE[byte] << 4) | (((byte << skip) + read(skip)) & 0x3F)
            consumed += 8 + skip
            offs = (bp - offs - 1) & 0xFFF
            for _ in range(ch - 253):
                c = buf[offs & 0xFFF]
                offs += 1
                out.append(c)
                buf[bp] = c
                bp = (bp + 1) & 0xFFF
    return bytes(out[:size])


# -- method 8: MW (Miller-Wegman LZ) ----------------------------------------------------------------

def _mw(data, size):
    bits = _BitsLE(data)
    out = bytearray()
    while len(out) < size and not bits.eof():
        table = [bytes((i,)) for i in range(256)]
        top = 256
        nextw = 512
        width = 9
        ptr = bits.read(width)
        if ptr < 256:
            prev = table[ptr]
            out += prev
            while len(out) < size and not bits.eof():
                ptr = bits.read(width)
                if ptr >= top:
                    break
                cur = table[ptr]
                table.append(prev + cur)
                top += 1
                if top == nextw:
                    nextw <<= 1
                    width += 1
                out += cur
                prev = cur
        if ptr > top:
            break
    return bytes(out[:size])


_METHODS = {0: lambda d, s: bytes(d[:s]), 1: _rle90, 2: _lzw, 3: _huffman, 5: _lzah, 8: _mw,
            13: _method13, 15: _arsenic}


def _decompress(method, data, size, crc=None):
    method &= 0x0F
    f = _METHODS.get(method)
    if f is None:
        raise StuffItError("compression method %d not supported" % method)
    out = f(data, size)
    if len(out) != size:
        raise StuffItError("short data")
    if crc is not None and method != 15 and crc16(out) != crc:
        raise StuffItError("CRC error")
    return out


# -- archive parsers ------------------------------------------------------------------------------

def _entry(name, path, ftype, creator, rsrc_args, data_args, errors):
    from .macfiles import MacFile
    forks = []
    for args in (rsrc_args, data_args):
        if args is None:
            forks.append(b"")
            continue
        method, comp, size, crc = args
        if size == 0:
            forks.append(b"")
            continue
        if method & 0x80:
            errors.append((path, "encrypted"))
            return None
        try:
            forks.append(_decompress(method, comp, size, crc))
        except (StuffItError, IndexError, RecursionError) as e:
            errors.append((path, str(e)))
            return None
    return MacFile(name, forks[1], forks[0], ftype, creator, path)


def _parse_classic(blob, errors):
    files = []
    total = struct.unpack_from(">I", blob, 6)[0]
    end = min(total, len(blob))
    o = 22
    dirs = []
    while o + 112 <= end:
        h = blob[o:o + 112]
        if struct.unpack_from(">H", h, 110)[0] != crc16(h[:110]):
            raise StuffItError("bad header CRC")
        rmethod, dmethod = h[0], h[1]
        nlen = min(h[2], 31)
        name = h[3:3 + nlen].decode("mac_roman", "replace")
        ftype, creator = h[66:70], h[70:74]
        rlen, dlen, rclen, dclen = struct.unpack_from(">IIII", h, 84)
        rcrc, dcrc = struct.unpack_from(">HH", h, 100)
        o += 112
        fm = 0x20
        if (dmethod & ~0x90 & 0xFF) == fm or (rmethod & ~0x90 & 0xFF) == fm:
            dirs.append(name)
            continue
        if (dmethod & ~0x90 & 0xFF) == 0x21 or (rmethod & ~0x90 & 0xFF) == 0x21:
            if dirs:
                dirs.pop()
            continue
        path = ":".join(dirs + [name])
        rcomp = blob[o:o + rclen]
        dcomp = blob[o + rclen:o + rclen + dclen]
        o += rclen + dclen
        f = _entry(name, path, ftype, creator, (rmethod, rcomp, rlen, rcrc), (dmethod, dcomp, dlen, dcrc), errors)
        if f is not None:
            files.append(f)
    return files


def _parse_sit5(blob, errors):
    files = []
    if blob[82] != 5:
        raise StuffItError("unknown StuffIt 5 version")
    flags = blob[83]
    numfiles = struct.unpack_from(">H", blob, 92)[0]
    first = struct.unpack_from(">I", blob, 94)[0]
    if flags & 0x80:
        raise StuffItError("encrypted archive")
    o = first
    dirs = {}
    remaining = numfiles
    while remaining > 0 and o + 48 <= len(blob):
        remaining -= 1
        offs = o
        if struct.unpack_from(">I", blob, o)[0] != 0xA5A5A5A5:
            raise StuffItError("bad StuffIt 5 entry")
        version = blob[o + 4]
        hsize = struct.unpack_from(">H", blob, o + 6)[0]
        eflags = blob[o + 9]
        diroffs, nlen = struct.unpack_from(">IH", blob, o + 26)
        dlen, dclen, dcrc = struct.unpack_from(">IIH", blob, o + 34)
        p = o + 46
        if eflags & 0x40:
            count = struct.unpack_from(">H", blob, p)[0]
            p += 2
            if dlen == 0xFFFFFFFF:
                # marker entries that follow each folder; The Unarchiver skips them too
                remaining += 1
                o = p
                continue
        else:
            dmethod, plen = blob[p], blob[p + 1]
            p += 2 + plen
        name = blob[p:p + nlen].decode("mac_roman", "replace")
        p += nlen
        if p < offs + hsize:
            csize = struct.unpack_from(">H", blob, p)[0]
            p += 4 + csize
        something = struct.unpack_from(">H", blob, p)[0]
        p += 4
        ftype, creator = blob[p:p + 4], blob[p + 4:p + 8]
        p += 10
        p += 22 if version == 1 else 18
        rlen = rclen = rcrc = rmethod = 0
        hasrsrc = something & 1
        if hasrsrc:
            rlen, rclen, rcrc = struct.unpack_from(">IIH", blob, p)
            rmethod, plen = blob[p + 12], blob[p + 13]
            p += 14 + plen
        parent = dirs.get(diroffs, "")
        path = parent + ":" + name if parent else name
        if eflags & 0x40:
            dirs[offs] = path
            remaining += count
            o = p
            continue
        if eflags & 0x20:
            errors.append((path, "encrypted"))
            o = p + rclen + dclen
            continue
        rcomp = blob[p:p + rclen]
        dcomp = blob[p + rclen:p + rclen + dclen]
        o = p + rclen + dclen
        f = _entry(name, path, ftype, creator, (rmethod, rcomp, rlen, rcrc) if hasrsrc else None,
                   (dmethod, dcomp, dlen, dcrc), errors)
        if f is not None:
            files.append(f)
    return files


def extract(blob):
    """The files in a StuffIt archive, as MacFile objects, and a list of
    (path, reason) for the entries that could not be extracted."""
    errors = []
    if blob[:16] == SIT5_MAGIC:
        files = _parse_sit5(blob, errors)
    else:
        files = _parse_classic(blob, errors)
    return files, errors
