"""Mac resource fork parsing and conversion of the resources HCX used
(ICON, cicn, icl8, CURS, ppat, PICT, snd) to Linux-friendly files."""

import os
import struct

from .pngwrite import png_bytes


def parse_resource_fork(rsrc):
    """Returns list of (type:str, id:int, name:str, data:bytes)."""
    out = []
    if len(rsrc) < 16:
        return out
    try:
        data_off, map_off, data_len, map_len = struct.unpack_from(">IIII", rsrc, 0)
        if map_off + 28 > len(rsrc):
            return out
        type_list = map_off + struct.unpack_from(">H", rsrc, map_off + 24)[0]
        name_list = map_off + struct.unpack_from(">H", rsrc, map_off + 26)[0]
        ntypes = (struct.unpack_from(">H", rsrc, type_list)[0] + 1) & 0xFFFF
        if ntypes == 0x10000 or ntypes > 2000:
            return out
        for t in range(ntypes):
            to = type_list + 2 + t * 8
            rtype = rsrc[to:to + 4].decode("mac_roman")
            count = struct.unpack_from(">H", rsrc, to + 4)[0] + 1
            ref = type_list + struct.unpack_from(">H", rsrc, to + 6)[0]
            for i in range(count):
                ro = ref + i * 12
                rid, noff = struct.unpack_from(">hh", rsrc, ro)
                doff = int.from_bytes(rsrc[ro + 5:ro + 8], "big") + data_off
                name = ""
                if noff != -1:
                    no = name_list + noff
                    name = decode_mac_text(rsrc[no + 1:no + 1 + rsrc[no]])
                if doff + 4 > len(rsrc):
                    continue
                ln = struct.unpack_from(">I", rsrc, doff)[0]
                out.append((rtype, rid, name, rsrc[doff + 4:doff + 4 + ln]))
    except (struct.error, IndexError):
        pass
    return out


ENCODING = ["mac_roman"]  # set by the importer (mac_roman or shift_jis)


def decode_mac_text(b):
    enc = ENCODING[0]
    try:
        return b.decode(enc)
    except UnicodeDecodeError:
        try:
            return b.decode("cp932" if enc == "shift_jis" else "mac_roman", errors="replace")
        except Exception:
            return b.decode("latin-1")


def _u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def _u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def _rgba_png(w, h, px):
    rows = [bytes(px[y * w * 4:(y + 1) * w * 4]) for y in range(h)]
    return png_bytes(w, h, rows, 6)


def _bit(b, rowbytes, x, y):
    i = y * rowbytes + x // 8
    return (b[i] >> (7 - x % 8)) & 1 if i < len(b) else 0


def icon_png(b):
    """'ICON': 32x32 1-bit. Outside white area made transparent (flood fill), like HCX."""
    W = H = 32
    black = [[_bit(b, 4, x, y) for x in range(W)] for y in range(H)]
    transparent = [[False] * W for _ in range(H)]
    stack = [(x, y) for x in range(W) for y in (0, H - 1)] + [(x, y) for y in range(H) for x in (0, W - 1)]
    while stack:
        x, y = stack.pop()
        if 0 <= x < W and 0 <= y < H and not black[y][x] and not transparent[y][x]:
            transparent[y][x] = True
            stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    px = bytearray()
    for y in range(H):
        for x in range(W):
            if black[y][x]:
                px += b"\0\0\0\xff"
            elif transparent[y][x]:
                px += b"\xff\xff\xff\0"
            else:
                px += b"\xff\xff\xff\xff"
    return _rgba_png(W, H, px)


def curs_png(b):
    px = bytearray()
    for y in range(16):
        for x in range(16):
            c = _bit(b, 2, x, y)
            m = _bit(b[32:], 2, x, y)
            if m:
                px += b"\0\0\0\xff" if c else b"\xff\xff\xff\xff"
            else:
                px += b"\0\0\0\xff" if c else b"\0\0\0\0"
    hot_y, hot_x = struct.unpack_from(">hh", b, 64) if len(b) >= 68 else (0, 0)
    return _rgba_png(16, 16, px), (hot_x, hot_y)


def _ctable(b, o):
    n = _u16(b, o + 6) + 1
    pal = {}
    o += 8
    for i in range(n):
        v, r, g, bl = struct.unpack_from(">HHHH", b, o)
        pal[v & 0xFF if v < 0x8000 else i] = (r >> 8, g >> 8, bl >> 8)
        o += 8
    return pal, o


def _unpack_bits(b, o, rowbytes):
    """PackBits one row. Returns (row, new_offset)."""
    if rowbytes > 250:
        cnt = _u16(b, o)
        o += 2
    else:
        cnt = b[o]
        o += 1
    end = o + cnt
    row = bytearray()
    while o < end and len(row) < rowbytes:
        n = b[o]
        o += 1
        if n < 128:
            row += b[o:o + n + 1]
            o += n + 1
        elif n > 128:
            row += bytes([b[o]]) * (257 - n)
            o += 1
    return bytes(row[:rowbytes]).ljust(rowbytes, b"\0"), end


def _pix(row, x, depth):
    if depth == 8:
        return row[x] if x < len(row) else 0
    per = 8 // depth
    byte = row[x // per] if x // per < len(row) else 0
    shift = 8 - depth * (x % per + 1)
    return (byte >> shift) & ((1 << depth) - 1)


def cicn_png(b):
    """'cicn' color icon with mask."""
    rowbytes = _u16(b, 4) & 0x3FFF
    top, left, bottom, right = struct.unpack_from(">hhhh", b, 6)
    w, h = right - left, bottom - top
    depth = _u16(b, 32)
    m_rb = _u16(b, 50 + 4)
    i_rb = _u16(b, 50 + 14 + 4)
    o = 50 + 14 + 14 + 4
    mask = b[o:o + m_rb * h]
    o += m_rb * h
    o += i_rb * h  # 1-bit icon, unused
    pal, o = _ctable(b, o)
    px = bytearray()
    data = b[o:]
    for y in range(h):
        row = data[y * rowbytes:(y + 1) * rowbytes]
        for x in range(w):
            r, g, bl = pal.get(_pix(row, x, depth), (0, 0, 0))
            a = 255 if _bit(mask, m_rb, x, y) else 0
            px += bytes((r, g, bl, a))
    return _rgba_png(w, h, px)


def _std_palette8():
    pal = []
    for i in range(216):
        pal.append((0xFF * (5 - (i // 36) % 6) // 5, 0xFF * (5 - (i // 6) % 6) // 5, 0xFF * (5 - i % 6) // 5))
    ramp = [0xFF * (9 - i) // 9 for i in range(10)]
    ramp = [v for v in ramp]
    pal += [(v, 0, 0) for v in ramp] + [(0, v, 0) for v in ramp] + [(0, 0, v) for v in ramp] + [(v, v, v) for v in ramp]
    return pal[:256]


def icl8_png(b):
    pal = _std_palette8()
    px = bytearray()
    for i in range(32 * 32):
        r, g, bl = pal[b[i]] if i < len(b) else (255, 255, 255)
        px += bytes((r, g, bl, 255))
    return _rgba_png(32, 32, px)


def ppat_png(b):
    if _u16(b, 0) != 1:
        return None
    pmap, pdata = _u32(b, 2), _u32(b, 6)
    rowbytes = _u16(b, pmap + 4) & 0x3FFF
    top, left, bottom, right = struct.unpack_from(">hhhh", b, pmap + 6)
    w, h = right - left, bottom - top
    depth = _u16(b, pmap + 32)
    ctab = _u32(b, pmap + 42)
    pal, _ = _ctable(b, ctab)
    px = bytearray()
    for y in range(h):
        row = b[pdata + y * rowbytes:pdata + (y + 1) * rowbytes]
        for x in range(w):
            r, g, bl = pal.get(_pix(row, x, depth), (0, 0, 0))
            px += bytes((r, g, bl, 255))
    return _rgba_png(w, h, px)


def _wav(rate, channels, bits, frames_le):
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(bits // 8)
        w.setframerate(max(1, int(round(rate))))
        w.writeframes(frames_le)
    return buf.getvalue()


def snd_wav(b):
    """'snd ' format 1/2 with sampled sound (uncompressed) -> WAV bytes."""
    fmt = _u16(b, 0)
    o = 2
    if fmt == 1:
        nmods = _u16(b, o)
        o += 2 + nmods * 6
    elif fmt == 2:
        o += 2
    else:
        return None
    ncmds = _u16(b, o)
    o += 2
    hdr = None
    for _ in range(ncmds):
        cmd, p1, p2 = struct.unpack_from(">HHI", b, o)
        o += 8
        if cmd & 0x7FFF in (80, 81):  # soundCmd / bufferCmd
            hdr = p2
            break
    if hdr is None or hdr + 22 > len(b):
        return None
    length_or_ch = _u32(b, hdr + 4)
    rate = _u32(b, hdr + 8) / 65536.0
    encode = b[hdr + 20]
    if encode == 0:
        data = b[hdr + 22:hdr + 22 + length_or_ch]
        return _wav(rate, 1, 8, data)  # 8-bit offset binary == WAV 8-bit
    if encode == 0xFF:
        ch = length_or_ch
        frames = _u32(b, hdr + 22)
        size = _u16(b, hdr + 48)
        start = hdr + 64
        if size == 8:
            return _wav(rate, ch, 8, b[start:start + frames * ch])
        if size == 16:
            raw = b[start:start + frames * ch * 2]
            le = bytearray(len(raw))
            le[0::2] = raw[1::2]
            le[1::2] = raw[0::2]
            return _wav(rate, ch, 16, bytes(le))
    return None  # compressed (MACE/IMA): not converted, kept raw


def convert_resources(rsrc, outdir):
    """Write converted resources into outdir. Returns list of media dicts."""
    media = []
    for rtype, rid, name, data in parse_resource_fork(rsrc):
        entry = {"id": rid, "name": name, "type": rtype, "file": None}
        try:
            if rtype == "ICON":
                fn = "ICON_%d.png" % rid
                blob = icon_png(data)
                entry["type"] = "icon"
            elif rtype == "cicn":
                fn = "cicn_%d.png" % rid
                blob = cicn_png(data)
            elif rtype == "icl8":
                fn = "icl8_%d.png" % rid
                blob = icl8_png(data)
            elif rtype == "CURS":
                fn = "CURS_%d.png" % rid
                blob, hot = curs_png(data)
                entry["type"] = "cursor"
                entry["hotspot"] = hot
            elif rtype == "ppat":
                fn = "ppat_%d.png" % rid
                blob = ppat_png(data)
            elif rtype in ("PICT", "pict", "Pdat"):
                fn = "PICT_%d.pict" % rid
                blob = b"\0" * 512 + data
                entry["type"] = "picture"
            elif rtype == "snd ":
                blob = snd_wav(data)
                if blob:
                    fn = "SND_%d.wav" % rid
                else:
                    fn = "snd_%d.data" % rid
                    blob = data
                entry["type"] = "sound"
            elif rtype in ("XCMD", "XFCN", "xcmd", "xfcn"):
                plat = "68k" if rtype.isupper() else "ppc"
                fn = "%s_%s_%d.data" % (rtype, plat, rid)
                blob = data
                entry["type"] = "external"
                entry["kind"] = "command" if rtype.lower() == "xcmd" else "function"
                entry["platform"] = "mac" + plat
                entry["size"] = len(data)
            elif rtype in ("FONT", "NFNT", "FOND"):
                continue
            else:
                safe = "".join(c if c.isalnum() else "_" for c in rtype)
                fn = "%s_%d.data" % (safe, rid)
                blob = data
        except Exception:
            safe = "".join(c if c.isalnum() else "_" for c in rtype)
            fn = "%s_%d.data" % (safe, rid)
            blob = data
        if blob is None:
            continue
        with open(os.path.join(outdir, fn), "wb") as fh:
            fh.write(blob)
        entry["file"] = fn
        media.append(entry)
    return media
