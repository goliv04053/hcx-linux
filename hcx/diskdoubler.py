"""DiskDoubler archives (DDAR, DDA2) and files compressed in place by
DiskDoubler, from Salient and later Symantec, on the classic Mac OS. Its
self-extracting archives are applications named ".sea" whose data fork is
the archive.

The format is ported from XADMaster's XADDiskDoublerParser (LGPL 2.1 or
later, Dag Ågren and contributors). The methods supported are the ones the
HyperCard collections use: none, Compact Pro (method 8, the most common),
compress (1) and Huffman (4)."""

import binascii
import struct

FILE_MAGIC = b"\xab\xcd\x00\x54"


class DiskDoublerError(Exception):
    pass


def _header_ok(data, n):
    """The header's CRC-16 (CCITT, as BinHex uses) is stored after it."""
    return binascii.crc_hqx(data[:n], 0) == struct.unpack_from(">H", data, n)[0]


def is_diskdoubler(data):
    if not data or len(data) < 62:
        return False
    m = data[:4]
    if m == FILE_MAGIC and len(data) >= 84:
        return struct.unpack_from(">H", data, 82)[0] == 0 or _header_ok(data, 82)
    if m == b"DDAR" and len(data) >= 78:
        return _header_ok(data, 76)
    if m == b"DDA2":
        return _header_ok(data, 60)
    return False


# -- forks ----------------------------------------------------------------------------------------

def _sum16(data):
    return sum(data) & 0xFFFF


def _fork(comp, size, method, crc, info1, info2):
    from . import compactpro, stuffit
    method &= 0x7F
    xor = 0x5A if info1 >= 0x2A and not info2 & 0x80 else 0
    if method == 0:
        out = bytes(comp[:size])
        check = None
    elif method == 8:
        sub = sum(comp[:16])
        body = comp[16:]
        if sub == 0:
            body = compactpro._lzh(body, 0xFFF0)
        out = compactpro._rle(body, size)
        check = stuffit.crc16(out) == crc
    elif method == 1:
        m1, m2, flags = (b ^ xor for b in comp[:3])
        if not flags & 0x80:
            raise DiskDoublerError("compress without block mode is not supported")
        out = stuffit._lzw(comp[3:], size, flags & 0x1F)
        if xor:
            out = bytes(b ^ xor for b in out)
        check = _sum16(out) == (crc - m1 - m2 - flags) & 0xFFFF
    elif method == 4:
        out = stuffit._huffman(comp, size)
        if xor:
            out = bytes(b ^ xor for b in out)
        check = _sum16(out) == crc
    else:
        raise DiskDoublerError("compression method %d not supported" % method)
    if len(out) != size:
        raise DiskDoublerError("short data")
    if check is False:
        raise DiskDoublerError("CRC error")
    return out


def _file(data, o, name, path, files, errors):
    """A file compressed by DiskDoubler (its 84-byte header at o). Returns
    the size of the compressed forks."""
    from .macfiles import MacFile
    (dsize, dcomp, rsize, rcomp, dmethod, rmethod, info1, _x, _mod, _cre, ftype, creator,
     _flags) = struct.unpack_from(">IIIIBBBBII4s4sH", data, o + 4)
    dcrc, rcrc, info2 = struct.unpack_from(">HHB", data, o + 48)
    ddelta, rdelta = struct.unpack_from(">HH", data, o + 54)
    start = o + 84
    try:
        if ddelta or rdelta:
            raise DiskDoublerError("delta preprocessing is not supported")
        if start + dcomp + rcomp > len(data):
            raise DiskDoublerError("short data")
        d = _fork(data[start:start + dcomp], dsize, dmethod, dcrc, info1, info2) if dsize else b""
        r = _fork(data[start + dcomp:start + dcomp + rcomp], rsize, rmethod, rcrc, info1, info2) if rsize else b""
        files.append(MacFile(name, d, r, ftype, creator, path))
    except (DiskDoublerError, IndexError, struct.error) as e:
        errors.append((path, str(e)))
    return dcomp + rcomp


def _mac_name(raw):
    try:
        from .macfiles import mac_name
        return mac_name(raw)
    except Exception:  # noqa: BLE001
        return raw.decode("mac_roman", "replace")


def _ddar(data, files, errors):
    from .macfiles import MacFile
    o = 78
    dirs = []
    while o + 4 <= len(data):
        magic = data[o:o + 4]
        if magic == FILE_MAGIC:
            o += 84     # a redundant file header at the end of a file
            continue
        if magic != b"DDAR":
            if o + 124 > len(data) or not any(data[o:o + 64]):
                break   # padding at the end
            raise DiskDoublerError("bad entry")
        n = min(data[o + 8], 63)
        name = _mac_name(data[o + 9:o + 9 + n])
        isdir, enddir = data[o + 72], data[o + 73]
        dsize, rsize = struct.unpack_from(">II", data, o + 74)
        ftype, creator = data[o + 90:o + 94], data[o + 94:o + 98]
        fflags = struct.unpack_from(">H", data, o + 98)[0]
        start = o + 124
        path = ":".join(dirs + [name])
        if enddir:
            if dirs:
                dirs.pop()
            o = start
        elif isdir:
            dirs.append(name)
            o = start
        elif fflags & 0x20:
            files.append(MacFile(name, data[start:start + dsize], data[start + dsize:start + dsize + rsize],
                                 ftype, creator, path))
            o = start + dsize + rsize
        else:
            if data[start:start + 4] != FILE_MAGIC:
                raise DiskDoublerError("bad file header")
            o = start + 84 + _file(data, start, name, path, files, errors)


def _dda2(data, files, errors):
    o = 62
    dirs = []
    last = 0
    while o + 46 <= len(data):
        start = o
        if data[o:o + 4] != b"DDA2":
            raise DiskDoublerError("bad entry")
        etype = struct.unpack_from(">H", data, o + 4)[0]
        if etype == 0xBBBB:
            break
        n = min(data[o + 6], 31)
        name = _mac_name(data[o + 7:o + 7 + n])
        level, total = struct.unpack_from(">iI", data, o + 38)
        level -= 2
        if total == 0:
            break
        if level >= 0:
            for _ in range(level, last):
                if dirs:
                    dirs.pop()
            last = level
            path = ":".join(dirs + [name])
            if etype & 0x8000:
                dirs.append(name)
            else:
                f = o + 46 + 10
                if data[f:f + 4] == FILE_MAGIC:
                    _file(data, f, name, path, files, errors)
        o = start + total


def extract(data, name="file"):
    """The files in a DiskDoubler archive (or the one file of a file
    compressed in place), as MacFile objects, and a list of (path, reason)
    for the entries that could not be extracted."""
    files, errors = [], []
    m = data[:4]
    if m == FILE_MAGIC:
        if name.lower().endswith(".dd"):
            name = name[:-3]
        _file(data, 0, name, name, files, errors)
    elif m == b"DDAR":
        _ddar(data, files, errors)
    elif m == b"DDA2":
        _dda2(data, files, errors)
    return files, errors
