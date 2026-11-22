"""Compact Pro (.cpt) archives, as made by Bill Goodman's Compact Pro and
Compactor on the classic Mac OS.

The format and the decompressors (RLE with an 0x81 escape, and LZH with
static Huffman codes per block) are ported from XADMaster's
XADCompactProParser, XADCompactProRLEHandle and XADCompactProLZHHandle
(LGPL 2.1 or later, Dag Ågren and contributors)."""

import struct
import zlib


class CompactProError(Exception):
    pass


def _header_crc_ok(blob, offset):
    try:
        correct = struct.unpack_from(">I", blob, offset)[0]
        o = offset + 4
        nentries = struct.unpack_from(">H", blob, o)[0]
        commentlen = blob[o + 2]
        p = o + 3 + commentlen
        for _ in range(nentries):
            namelen = blob[p]
            p += 1 + (namelen & 0x7F) + (2 if namelen & 0x80 else 45)
        if p > len(blob):
            return False
        crc = zlib.crc32(blob[o:p]) ^ 0xFFFFFFFF
        return crc == correct
    except (IndexError, struct.error):
        return False


def is_compact_pro(blob):
    if len(blob) < 8 or blob[0] != 1:
        return False
    offset = struct.unpack_from(">I", blob, 4)[0]
    if offset + 7 > len(blob):
        return False
    return _header_crc_ok(blob, offset)


# -- decompressors ---------------------------------------------------------------------------------

def _rle(data, size):
    """Compact Pro RLE: 0x81 0x82 n repeats the last byte (n-1 more times),
    0x81 0x82 0 is a literal 0x81 0x82, 0x81 0x81 is a literal 0x81."""
    out = bytearray()
    saved = 0
    i, n = 0, len(data)
    half = False
    while len(out) < size:
        if half:
            b = 0x81
            half = False
        else:
            if i >= n:
                break
            b = data[i]
            i += 1
        if b != 0x81:
            # copy a run of plain bytes at once
            j = data.find(b"\x81", i)
            if j < 0:
                j = n
            j = min(j, i + size - len(out) - 1)
            out.append(b)
            if j > i:
                out += data[i:j]
                saved = data[j - 1]
                i = j
            else:
                saved = b
            continue
        c = data[i] if i < n else 0
        i += 1
        if c == 0x82:
            k = data[i] if i < n else 0
            i += 1
            if k != 0:
                out += bytes((saved,)) * (k - 1)
            else:
                out += b"\x81\x82"
                saved = 0x82
        elif c == 0x81:
            half = True
            out.append(0x81)
            saved = 0x81
        else:
            out.append(0x81)
            out.append(c)
            saved = c
    return bytes(out[:size])


class _Bits:
    """High bit first, with an exact byte position."""

    def __init__(self, data):
        self.data = data
        self.bp = 0
        self.end = len(data) * 8

    def peek(self, k):
        o = self.bp >> 3
        v = int.from_bytes(self.data[o:o + 3].ljust(3, b"\0"), "big")
        return (v >> (24 - (self.bp & 7) - k)) & ((1 << k) - 1)

    def read(self, k):
        if self.bp + k > self.end:
            raise CompactProError("end of data")
        v = self.peek(k)
        self.bp += k
        return v

    def byte(self):
        return self.read(8)

    def align(self):
        self.bp = (self.bp + 7) & ~7

    def offset(self):
        return self.bp >> 3


class _Code:
    """Canonical prefix code (shortest codes are all zeros), read high bit first."""

    def __init__(self, lengths):
        self.maxlen = max(lengths) if lengths else 0
        if self.maxlen == 0:
            raise CompactProError("empty prefix code")
        tb = self.maxlen
        table = [None] * (1 << tb)
        code = 0
        for length in range(1, self.maxlen + 1):
            for sym, l in enumerate(lengths):
                if l != length:
                    continue
                if code >= (1 << length):
                    raise CompactProError("bad prefix code")
                lo = code << (tb - length)
                hi = (code + 1) << (tb - length)
                entry = (length, sym)
                for k in range(lo, hi):
                    table[k] = entry
                code += 1
            code <<= 1
        self.table = table
        self.tb = tb

    def decode(self, bits):
        e = self.table[bits.peek(self.tb)]
        if e is None:
            raise CompactProError("bad prefix code")
        if bits.bp + e[0] > bits.end:
            raise CompactProError("end of data")
        bits.bp += e[0]
        return e[1]


def _parse_code(bits, size):
    nbytes = bits.byte()
    if nbytes * 2 > size:
        raise CompactProError("bad code table")
    lengths = [0] * size
    for i in range(nbytes):
        v = bits.byte()
        lengths[2 * i] = v >> 4
        lengths[2 * i + 1] = v & 0x0F
    return _Code(lengths)


def _lzh(data, blocksize=0x1FFF0):
    bits = _Bits(data)
    out = bytearray()
    count = blocksize
    start = 0
    lit = length = offs = None
    try:
        while True:
            if count >= blocksize:
                if start:
                    bits.align()
                    bits.bp += 24 if (bits.offset() - start) & 1 else 16
                lit = _parse_code(bits, 256)
                length = _parse_code(bits, 64)
                offs = _parse_code(bits, 128)
                count = 0
                start = bits.offset()
            if bits.read(1):
                count += 2
                out.append(lit.decode(bits))
            else:
                count += 3
                n = length.decode(bits)
                d = (offs.decode(bits) << 6) | bits.read(6)
                if d == 0:
                    d = 8192        # the window is 8 KB: offset 0 is a whole window back
                p = len(out) - d
                if p < 0:
                    # before the start of the output the window holds zeros
                    for _ in range(n):
                        out.append(out[p] if p >= 0 else 0)
                        p += 1
                elif d >= n:
                    out += out[p:p + n]
                else:
                    for _ in range(n):
                        out.append(out[p])
                        p += 1
    except CompactProError:
        pass
    return bytes(out)


def _fork(blob, offset, complen, size, lzh):
    comp = blob[offset:offset + complen]
    if len(comp) < complen:
        raise CompactProError("short data")
    if lzh:
        comp = _lzh(comp)
    out = _rle(comp, size)
    if len(out) != size:
        raise CompactProError("short data")
    return out


# -- archive --------------------------------------------------------------------------------------

def _mac_name(raw):
    try:
        from .macfiles import mac_name
        return mac_name(raw)
    except Exception:
        return raw.decode("mac_roman", "replace")


def extract(blob):
    """The files in a Compact Pro archive, as MacFile objects, and a list of
    (path, reason) for the entries that could not be extracted."""
    from .macfiles import MacFile
    errors = []
    files = []
    offset = struct.unpack_from(">I", blob, 4)[0]
    nentries = struct.unpack_from(">H", blob, offset + 4)[0]
    p = offset + 7 + blob[offset + 6]

    def parse_dir(parent, n):
        nonlocal p
        while n > 0:
            namelen = blob[p]
            name = _mac_name(blob[p + 1:p + 1 + (namelen & 0x7F)])
            p += 1 + (namelen & 0x7F)
            path = parent + ":" + name if parent else name
            if namelen & 0x80:
                nsub = struct.unpack_from(">H", blob, p)[0]
                p += 2
                parse_dir(path, nsub)
                n -= nsub + 1
                continue
            (_vol, fileoffs, ftype, creator, _cdate, _mdate, _fflags, crc, flags,
             rlen, dlen, rclen, dclen) = struct.unpack_from(">BI4s4sIIHIHIIII", blob, p)
            p += 45
            n -= 1
            if flags & 1:
                errors.append((path, "encrypted"))
                continue
            try:
                rsrc = _fork(blob, fileoffs, rclen, rlen, flags & 2) if rlen else b""
                data = _fork(blob, fileoffs + rclen, dclen, dlen, flags & 4) if dlen else b""
            except (CompactProError, IndexError) as e:
                errors.append((path, str(e)))
                continue
            check = zlib.crc32(data, zlib.crc32(rsrc)) ^ 0xFFFFFFFF    # resource fork first
            if crc != check:
                errors.append((path, "CRC error"))
                continue
            files.append(MacFile(name, data, rsrc, ftype, creator, path))

    parse_dir("", nentries)
    return files, errors
