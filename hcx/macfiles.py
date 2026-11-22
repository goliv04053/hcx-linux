"""Reading classic Macintosh files on Linux.

A HyperCard stack is a Mac file with two forks: the data fork (the stack
itself) and the resource fork (icons, sounds, pictures, XCMDs).  On macOS the
original HCX got the resource fork through `tar`; on Linux the forks arrive in
one of these containers, all handled here:

* plain file (data fork only) + optional sidecar with the resource fork:
  ``name.rsrc`` (raw fork or AppleDouble, as written by `unar`), ``._name``
  (AppleDouble, macOS/zip), ``.AppleDouble/name`` (netatalk),
  ``__MACOSX/._name`` (zip made on a Mac)
* MacBinary I/II/III (``.bin``)
* AppleSingle / AppleDouble
* BinHex 4.0 (``.hqx``)
* HFS disk images (``.img``, ``.dsk``, ``.image``, ``.hfs``, ``.iso`` …), raw,
  with an Apple partition map, or with a DiskCopy 4.2 header.
* StuffIt (``.sit``) and Compact Pro (``.cpt``) archives, also inside any of
  the above and as self-extracting ``.sea`` applications.
"""

import os
import struct


class MacFile:
    """A Mac file: name, type/creator and both forks."""

    def __init__(self, name, data=b"", rsrc=b"", ftype=b"????", creator=b"????", path=""):
        self.name = name
        self.data = data or b""
        self.rsrc = rsrc or b""
        self.type = ftype
        self.creator = creator
        self.path = path  # path inside a disk image ("Folder:File")

    def is_stack(self):
        return self.type == b"STAK" or looks_like_stack(self.data)

    def __repr__(self):
        return "MacFile(%r, type=%r, data=%d, rsrc=%d)" % (self.name, self.type, len(self.data), len(self.rsrc))


def looks_like_stack(data):
    return len(data) >= 0x600 and data[4:8] == b"STAK"


def u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def mac_name(raw):
    try:
        return raw.decode("mac_roman")
    except Exception:
        return raw.decode("latin-1")


# ---------------------------------------------------------------------------
# AppleSingle / AppleDouble
# ---------------------------------------------------------------------------

def parse_apple_single_double(blob):
    """Returns dict with optional 'data', 'rsrc', 'name', 'type', 'creator'."""
    if len(blob) < 26:
        return None
    magic = u32(blob, 0)
    if magic not in (0x00051600, 0x00051607):
        return None
    n = u16(blob, 24)
    out = {}
    for i in range(n):
        o = 26 + i * 12
        if o + 12 > len(blob):
            break
        eid, off, length = struct.unpack_from(">III", blob, o)
        chunk = blob[off:off + length]
        if eid == 1:
            out["data"] = chunk
        elif eid == 2:
            out["rsrc"] = chunk
        elif eid == 3:
            out["name"] = mac_name(chunk)
        elif eid == 9 and len(chunk) >= 8:
            out["type"], out["creator"] = chunk[0:4], chunk[4:8]
    return out


# ---------------------------------------------------------------------------
# MacBinary
# ---------------------------------------------------------------------------

def parse_macbinary(blob):
    if len(blob) < 128 or blob[0] != 0 or blob[74] != 0:
        return None
    nlen = blob[1]
    if not 1 <= nlen <= 63:
        return None
    dlen, rlen = u32(blob, 83), u32(blob, 87)
    dstart = 128
    sec = u16(blob, 120) if blob[122] >= 129 else 0  # MacBinary II secondary header
    dstart += (sec + 127) // 128 * 128
    rstart = dstart + (dlen + 127) // 128 * 128
    if dlen > 0x7FFFFFFF or rlen > 0x7FFFFFFF or rstart + rlen > len(blob) + 128:
        return None
    if dstart + dlen > len(blob):
        return None
    return MacFile(mac_name(blob[2:2 + nlen]), blob[dstart:dstart + dlen],
                   blob[rstart:rstart + rlen], blob[65:69], blob[69:73])


# ---------------------------------------------------------------------------
# BinHex 4.0
# ---------------------------------------------------------------------------

_HQX = "!\"#$%&'()*+,-012345689@ABCDEFGHIJKLMNPQRSTUVXYZ[`abcdefhijklmpqr"
_HQX_MAP = {c: i for i, c in enumerate(_HQX)}
_HQX_TABLE = bytes(_HQX_MAP.get(chr(i), 0) for i in range(256))
_HQX_JUNK = bytes(i for i in range(256) if chr(i) not in _HQX_MAP)


def parse_binhex(blob):
    marker = b"(This file must be converted with BinHex"
    i = blob.find(marker)
    if i < 0:
        return None
    start = blob.find(b":", blob.find(b"\n", i) if blob.find(b"\n", i) >= 0 else i)
    if start < 0:
        return None
    body = blob[start + 1:]
    end = body.find(b":")
    if end >= 0:
        body = body[:end]
    # 6-bit values, one per character, skipping line breaks and other noise
    vals = body.translate(_HQX_TABLE, _HQX_JUNK)
    n = len(vals) // 4 * 4
    raw = bytearray()
    for k in range(0, n, 4):
        a, b, c, d = vals[k], vals[k + 1], vals[k + 2], vals[k + 3]
        raw += bytes(((a << 2) | (b >> 4), ((b & 15) << 4) | (c >> 2), ((c & 3) << 6) | d))
    rest = vals[n:]
    if len(rest) >= 2:
        raw.append((rest[0] << 2) | (rest[1] >> 4))
    if len(rest) >= 3:
        raw.append(((rest[1] & 15) << 4) | (rest[2] >> 2))
    # run-length decoding (0x90 marker)
    out = bytearray()
    j = 0
    last = 0
    nraw = len(raw)
    while j < nraw:
        k = raw.find(0x90, j)
        if k < 0:
            out += raw[j:]
            break
        if k > j:
            out += raw[j:k]
            last = raw[k - 1]
        if k + 1 >= nraw:
            out.append(0x90)
            break
        cnt = raw[k + 1]
        j = k + 2
        if cnt == 0:
            out.append(0x90)
            last = 0x90
        else:
            out += bytes((last,)) * (cnt - 1)
    nl = out[0]
    p = 1 + nl + 1
    name = mac_name(bytes(out[1:1 + nl]))
    ftype, creator = bytes(out[p:p + 4]), bytes(out[p + 4:p + 8])
    p += 10
    dlen, rlen = u32(out, p), u32(out, p + 4)
    p += 10  # lengths + header crc
    data = bytes(out[p:p + dlen])
    p += dlen + 2
    rsrc = bytes(out[p:p + rlen])
    return MacFile(name, data, rsrc, ftype, creator)


# ---------------------------------------------------------------------------
# HFS volumes (read only)
# ---------------------------------------------------------------------------

class HFSError(Exception):
    pass


class HFSVolume:
    def __init__(self, img, base):
        self.img = img
        self.base = base
        mdb = img[base + 1024:base + 1024 + 512]
        if mdb[0:2] != b"BD":
            raise HFSError("not HFS")
        if u16(mdb, 0x7C) == 0x482B:  # embedded HFS+ signature 'H+'
            raise HFSError("HFS+ wrapper")
        self.block_size = u32(mdb, 0x14)
        self.first_block = u16(mdb, 0x1C)
        self.name = mac_name(mdb[0x25:0x25 + mdb[0x24]])
        self.overflow = {}
        xt_extents = self._extrec(mdb, 0x86)
        xt = self._read_extents(xt_extents, u32(mdb, 0x82))
        self._load_overflow(xt)
        ct = self._read_fork(4, 0, self._extrec(mdb, 0x96), u32(mdb, 0x92))
        self.files = []
        self._load_catalog(ct)

    @staticmethod
    def _extrec(b, o):
        return [(u16(b, o + i * 4), u16(b, o + i * 4 + 2)) for i in range(3)]

    def _read_extents(self, extents, length):
        out = bytearray()
        for start, count in extents:
            if count == 0:
                continue
            off = self.base + self.first_block * 512 + start * self.block_size
            out += self.img[off:off + count * self.block_size]
        return bytes(out[:length])

    def _read_fork(self, file_id, fork, first_extents, length):
        extents = list(first_extents)
        have = sum(c for _, c in extents)
        for start_block, ext in sorted(self.overflow.get((file_id, fork), [])):
            if start_block >= have:
                extents += ext
                have += sum(c for _, c in ext)
        return self._read_extents(extents, length)

    @staticmethod
    def _leaf_records(tree):
        if len(tree) < 512:
            return
        node_size = u16(tree, 14 + 18)
        if node_size == 0:
            node_size = 512
        node = u32(tree, 14 + 10)  # first leaf
        seen = set()
        while node and node not in seen and (node + 1) * node_size <= len(tree):
            seen.add(node)
            n = tree[node * node_size:(node + 1) * node_size]
            nrec = u16(n, 10)
            for r in range(nrec):
                off = u16(n, node_size - 2 * (r + 1))
                end = u16(n, node_size - 2 * (r + 2)) if r + 1 < nrec + 1 else node_size
                yield n[off:end if end > off else node_size]
            node = u32(n, 0)

    def _load_overflow(self, xt):
        for rec in self._leaf_records(xt):
            klen = rec[0]
            if klen < 7:
                continue
            fork, fid, start = rec[1], u32(rec, 2), u16(rec, 6)
            d = 1 + klen
            if d % 2:
                d += 1
            self.overflow.setdefault((fid, fork), []).append((start, self._extrec(rec, d)))

    def _load_catalog(self, ct):
        dirs = {}
        files = []
        for rec in self._leaf_records(ct):
            klen = rec[0]
            if klen == 0:
                continue
            parent = u32(rec, 2)
            name = mac_name(rec[7:7 + rec[6]])
            d = 1 + klen
            if d % 2:
                d += 1
            body = rec[d:]
            if not body:
                continue
            kind = body[0]
            if kind == 1:
                dirs[u32(body, 6)] = (parent, name)
            elif kind == 2 and len(body) >= 98:
                fid = u32(body, 20)
                data = self._read_fork(fid, 0, self._extrec(body, 74), u32(body, 26))
                rsrc = self._read_fork(fid, 0xFF, self._extrec(body, 86), u32(body, 36))
                files.append((parent, MacFile(name, data, rsrc, body[4:8], body[8:12])))
        for parent, f in files:
            parts = [f.name]
            seen = set()
            while parent in dirs and parent not in seen and parent != 1:
                seen.add(parent)
                parent, dname = dirs[parent]
                if parent == 1:
                    break  # root folder = volume name, skip it
                parts.append(dname)
            f.path = ":".join(reversed(parts))
            self.files.append(f)


def find_hfs_volumes(img):
    """Return HFSVolume objects found in a disk image (raw, partitioned, DiskCopy)."""
    bases = []
    if len(img) >= 1536 and img[1024:1026] == b"BD":
        bases.append(0)
    # DiskCopy 4.2: 84-byte header, magic 0x0100 at 82
    if len(img) > 84 + 1536 and u16(img, 82) == 0x0100 and img[84 + 1024:84 + 1026] == b"BD":
        bases.append(84)
    # Apple partition map. CD-ROMs often say 2048-byte blocks in block 0 but
    # keep a map of 512-byte entries counted in 512-byte blocks: try both.
    if img[0:2] == b"ER" or img[512:514] == b"PM":
        sizes = []
        if img[0:2] == b"ER" and u16(img, 2) in (1024, 2048):
            sizes.append(u16(img, 2))
        sizes.append(512)
        for blk in sizes:
            if img[blk:blk + 2] != b"PM":
                continue
            count = u32(img, blk + 4)
            for i in range(min(count, 64)):
                e = blk * (i + 1)
                if img[e:e + 2] != b"PM":
                    break
                start = u32(img, e + 8) * blk
                ptype = img[e + 48:e + 80].split(b"\0")[0]
                if ptype == b"Apple_HFS" and img[start + 1024:start + 1026] == b"BD":
                    bases.append(start)
            if bases:
                break
    vols = []
    for b in dict.fromkeys(bases):
        try:
            vols.append(HFSVolume(img, b))
        except HFSError:
            pass
        except Exception:
            pass
    return vols


def is_hfsplus(img):
    return img[1024:1026] in (b"H+", b"HX")


# ---------------------------------------------------------------------------
# Top level: open whatever the user gave us
# ---------------------------------------------------------------------------

def _sidecar_rsrc(path):
    d, base = os.path.split(path)
    candidates = [
        path + ".rsrc",
        os.path.splitext(path)[0] + ".rsrc",
        os.path.join(d, "._" + base),
        os.path.join(d, ".AppleDouble", base),
        os.path.join(d, "__MACOSX", "._" + base),
        os.path.join(d, "..namedfork", "rsrc"),
        path + "/..namedfork/rsrc",
    ]
    for c in candidates:
        try:
            if os.path.isfile(c) and os.path.getsize(c) > 0:
                with open(c, "rb") as fh:
                    blob = fh.read()
                ad = parse_apple_single_double(blob)
                if ad is not None:
                    if ad.get("rsrc"):
                        return ad["rsrc"]
                    continue
                return blob  # raw resource fork
        except OSError:
            pass
    return b""


class OpenResult:
    def __init__(self, files, container_name, kind):
        self.files = files          # list of MacFile (stacks first)
        self.container_name = container_name
        self.kind = kind            # 'file', 'macbinary', 'binhex', 'applesingle', 'hfs', 'stuffit'
        self.errors = []            # (path, reason) of archive entries that could not be read

    @property
    def stacks(self):
        return [f for f in self.files if f.is_stack()]


def _is_archive_data(data):
    """True for the data of a StuffIt or Compact Pro archive."""
    from . import stuffit, compactpro
    return bool(data) and (stuffit.is_stuffit(data) or compactpro.is_compact_pro(data))


def _is_archive(mf):
    from . import stuffit
    return _is_archive_data(mf.data) or (not mf.data and mf.type == b"SIT!" and stuffit.is_stuffit(mf.rsrc))


def _extract_archive(data):
    from . import stuffit, compactpro
    if stuffit.is_stuffit(data):
        return stuffit.extract(data)
    return compactpro.extract(data)


_ARCHIVE_EXTS = (".sit", ".cpt", ".sea")


def expand_archives(files, depth=0, errors=None):
    """Replace the StuffIt and Compact Pro archives among files by the files
    inside them (archives inside archives too, a few levels deep). Entries
    that could not be extracted are added to errors as (path, reason)."""
    from . import stuffit, compactpro
    out = []
    for mf in files:
        if depth < 4 and _is_archive_data(mf.data):
            prefix = mf.path.rsplit(".", 1)[0] if mf.path.lower().endswith(_ARCHIVE_EXTS) else mf.path
            try:
                inner, errs = _extract_archive(mf.data)
            except (stuffit.StuffItError, compactpro.CompactProError, IndexError, struct.error) as e:
                if errors is not None:
                    errors.append((prefix or mf.name, str(e)))
                out.append(mf)
                continue
            for f in inner:
                f.path = (prefix + ":" + f.path) if prefix else f.path
            if errors is not None:
                errors.extend(((prefix + ":" + p) if prefix else p, r) for p, r in errs)
            out.extend(expand_archives(inner, depth + 1, errors))
        else:
            out.append(mf)
    return out


def _archive_result(files, name, kind):
    if not any(_is_archive_data(f.data) for f in files):
        return None
    errors = []
    files = expand_archives(files, errors=errors)
    files.sort(key=lambda f: (not f.is_stack(), f.path.lower()))
    res = OpenResult(files, name, "stuffit")
    res.errors = errors
    return res


def open_path(path):
    """Read anything that may contain a HyperCard stack."""
    with open(path, "rb") as fh:
        blob = fh.read()
    name = os.path.basename(path)

    if looks_like_stack(blob):
        return OpenResult([MacFile(name, blob, _sidecar_rsrc(path), b"STAK", b"WILD")], name, "file")

    if _is_archive_data(blob):
        return _archive_result([MacFile(name, blob, b"", b"SIT!", b"SIT!", "")], name, "stuffit")

    ad = parse_apple_single_double(blob)
    if ad is not None and ad.get("data"):
        mf = MacFile(ad.get("name", name), ad.get("data", b""), ad.get("rsrc", b""),
                     ad.get("type", b"????"), ad.get("creator", b"????"))
        return _archive_result([mf], name, "stuffit") or OpenResult([mf], name, "applesingle")

    mb = parse_macbinary(blob)
    if mb is not None and _is_archive_data(mb.data):
        mb.path = ""
        return _archive_result([mb], name, "stuffit")
    if mb is not None and (mb.is_stack() or mb.rsrc):
        return OpenResult([mb], name, "macbinary")

    if b"(This file must be converted with BinHex" in blob[:4096]:
        bh = parse_binhex(blob)
        if bh is not None:
            bh.path = ""
            return _archive_result([bh], name, "stuffit") or OpenResult([bh], name, "binhex")

    vols = find_hfs_volumes(blob)
    if vols:
        files = []
        for v in vols:
            files.extend(v.files)
        files = expand_archives(files)
        files.sort(key=lambda f: (not f.is_stack(), f.path.lower()))
        return OpenResult(files, vols[0].name, "hfs")

    if is_hfsplus(blob):
        raise ValueError("hfsplus")

    return OpenResult([MacFile(name, blob, _sidecar_rsrc(path))], name, "file")
