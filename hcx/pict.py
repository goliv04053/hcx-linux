"""A small QuickDraw PICT (version 1 and 2) decoder.

It handles what HyperCard palettes and picture windows use: bitmaps
(BitsRect/PackBitsRect, also with regions), 8-bit and direct-colour pixmaps,
and the simple shapes (lines, rectangles, ovals) drawn in black. Text and
other opcodes are skipped. Returns (width, height, RGBA bytes)."""

import struct

from .pngwrite import png_bytes


class _Canvas:
    def __init__(self, top, left, bottom, right):
        self.t, self.l = top, left
        self.w = max(1, right - left)
        self.h = max(1, bottom - top)
        self.px = bytearray(b"\xff" * (self.w * self.h * 4))
        self.fg = (0, 0, 0)
        self.pen = (1, 1)
        self.pnloc = (0, 0)

    def put(self, x, y, rgb):
        x -= self.l
        y -= self.t
        if 0 <= x < self.w and 0 <= y < self.h:
            o = (y * self.w + x) * 4
            self.px[o:o + 4] = bytes((rgb[0], rgb[1], rgb[2], 255))

    def fill(self, t, l, b, r, rgb):
        for y in range(max(t, self.t), min(b, self.t + self.h)):
            for x in range(max(l, self.l), min(r, self.l + self.w)):
                self.put(x, y, rgb)

    def frame(self, t, l, b, r, rgb):
        pw, ph = self.pen
        self.fill(t, l, t + ph, r, rgb)
        self.fill(b - ph, l, b, r, rgb)
        self.fill(t, l, b, l + pw, rgb)
        self.fill(t, r - pw, b, r, rgb)

    def oval(self, t, l, b, r, rgb, filled):
        cx, cy = (l + r - 1) / 2.0, (t + b - 1) / 2.0
        rx, ry = max(0.5, (r - l) / 2.0), max(0.5, (b - t) / 2.0)
        for y in range(t, b):
            for x in range(l, r):
                d = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
                if d <= 1.0 and (filled or d >= (1 - 2.0 / min(rx, ry))):
                    self.put(x, y, rgb)

    def line(self, x0, y0, x1, y1, rgb):
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        for _ in range(10000):
            self.fill(y0, x0, y0 + self.pen[1], x0 + self.pen[0], rgb)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy
        self.pnloc = (x1, y1)


def _unpack_row(b, o, rowbytes, packed, pixel16=False):
    if not packed or rowbytes < 8:
        return bytes(b[o:o + rowbytes]), o + rowbytes
    if rowbytes > 250:
        cnt = struct.unpack_from(">H", b, o)[0]
        o += 2
    else:
        cnt = b[o]
        o += 1
    end = o + cnt
    row = bytearray()
    unit = 2 if pixel16 else 1
    while o < end and len(row) < rowbytes:
        n = b[o]
        o += 1
        if n < 128:
            row += b[o:o + (n + 1) * unit]
            o += (n + 1) * unit
        elif n > 128:
            row += bytes(b[o:o + unit]) * (257 - n)
            o += unit
    return bytes(row[:rowbytes]).ljust(rowbytes, b"\0"), end


def _bits(cv, b, o, op, v2):
    """BitsRect/BitsRgn/PackBitsRect/PackBitsRgn/DirectBits*."""
    direct = op in (0x9A, 0x9B)
    if direct:
        o += 4  # baseAddr
    rb = struct.unpack_from(">H", b, o)[0]
    is_pixmap = bool(rb & 0x8000) or direct
    rowbytes = rb & 0x3FFF
    bt, bl, bb, br = struct.unpack_from(">hhhh", b, o + 2)
    o += 10
    depth = 1
    pal = {0: (255, 255, 255), 1: (0, 0, 0)}
    pixtype = 0
    if is_pixmap:
        # pmVersion packType packSize hRes vRes pixelType pixelSize cmpCount cmpSize planeBytes pmTable pmReserved
        pack_type = struct.unpack_from(">H", b, o + 2)[0]
        pixtype, depth = struct.unpack_from(">HH", b, o + 16)
        cmp_count = struct.unpack_from(">H", b, o + 20)[0]
        o += 36
        if not direct:
            ct_flags, n = struct.unpack_from(">HH", b, o + 4)
            n += 1
            device = bool(ct_flags & 0x8000)   # device table: the entry's position is its index
            pal = {}
            co = o + 8
            for i in range(n):
                v, r, g, bl_ = struct.unpack_from(">HHHH", b, co)
                pal[i if device or v >= 0x8000 or v > 255 else v] = (r >> 8, g >> 8, bl_ >> 8)
                co += 8
            o = co
    else:
        pack_type, cmp_count = 0, 1
    st, sl, sb, sr = struct.unpack_from(">hhhh", b, o)
    dt, dl, db, dr = struct.unpack_from(">hhhh", b, o + 8)
    o += 18  # src, dst, mode
    if op in (0x91, 0x99, 0x9B):
        o += struct.unpack_from(">H", b, o)[0]
    packed = op in (0x98, 0x99, 0x9A, 0x9B) and rowbytes >= 8
    rows = bb - bt
    sw = br - bl
    for y in range(rows):
        if direct and depth == 32:
            row, o = _unpack_row(b, o, rowbytes, packed and pack_type != 1)
        else:
            row, o = _unpack_row(b, o, rowbytes, packed, pixel16=(depth == 16))
        ty = dt + (y + bt - st) if True else 0
        if not (st - bt <= y < sb - bt):
            continue
        for x in range(sl - bl, min(sr - bl, sw)):
            if depth == 1:
                bit = (row[x >> 3] >> (7 - (x & 7))) & 1 if (x >> 3) < len(row) else 0
                rgb = pal.get(bit, (0, 0, 0) if bit else (255, 255, 255))
                if not is_pixmap and not bit:
                    continue  # srcOr-like: white is transparent over white canvas anyway
            elif depth in (2, 4, 8):
                per = 8 // depth
                byte = row[x // per] if x // per < len(row) else 0
                v = (byte >> (8 - depth * (x % per + 1))) & ((1 << depth) - 1)
                rgb = pal.get(v, (0, 0, 0))
            elif depth == 16:
                v = struct.unpack_from(">H", row, x * 2)[0] if x * 2 + 1 < len(row) else 0
                rgb = (((v >> 10) & 31) * 255 // 31, ((v >> 5) & 31) * 255 // 31, (v & 31) * 255 // 31)
            elif depth == 32:
                w = sw
                if cmp_count >= 3 and packed and pack_type != 1:
                    off = cmp_count - 3
                    rgb = (row[(off) * w + x], row[(off + 1) * w + x], row[(off + 2) * w + x]) \
                        if (off + 2) * w + x < len(row) else (0, 0, 0)
                else:
                    k = x * 4
                    rgb = (row[k + 1], row[k + 2], row[k + 3]) if k + 3 < len(row) else (0, 0, 0)
            else:
                continue
            cv.put(dl + (x - (sl - bl)), ty, rgb)
    return o


_V1_SIZES = {0x00: 0, 0x02: 8, 0x03: 2, 0x04: 1, 0x05: 2, 0x06: 4, 0x08: 2, 0x09: 8, 0x0A: 8, 0x0B: 4,
             0x0D: 2, 0x0E: 4, 0x0F: 4, 0x10: 8, 0x11: 1, 0x15: 2, 0x16: 2, 0x1A: 6, 0x1B: 6, 0x1C: 0,
             0x1D: 6, 0x1E: 0, 0x1F: 6, 0x0C: 4, 0x02FF: 2}


def decode(data):
    """Decode a PICT resource (without the 512-byte file header)."""
    if len(data) < 12:
        return None
    top, left, bottom, right = struct.unpack_from(">hhhh", data, 2)
    cv = _Canvas(top, left, bottom, right)
    o = 10
    v2 = data[o:o + 2] == b"\x00\x11"
    if v2:
        o += 4  # 0011 02FF
    elif data[o:o + 2] == b"\x11\x01":
        o += 2
    last = (top, left, bottom, right)
    try:
        while o < len(data):
            if v2:
                if o & 1:
                    o += 1
                op = struct.unpack_from(">H", data, o)[0]
                o += 2
            else:
                op = data[o]
                o += 1
            if op == 0xFF or op == 0x00FF:
                break
            if op == 0x0C00:
                o += 24
            elif op in (0x90, 0x91, 0x98, 0x99, 0x9A, 0x9B):
                o = _bits(cv, data, o, op, v2)
            elif op == 0x01:  # clip region
                o += struct.unpack_from(">H", data, o)[0]
            elif op == 0x07:
                ph, pw = struct.unpack_from(">hh", data, o)
                cv.pen = (max(1, pw), max(1, ph))
                o += 4
            elif op == 0x0C:
                o += 4
            elif op == 0x20:
                y0, x0, y1, x1 = struct.unpack_from(">hhhh", data, o)
                cv.line(x0, y0, x1, y1, cv.fg)
                o += 8
            elif op == 0x21:
                y1, x1 = struct.unpack_from(">hh", data, o)
                cv.line(cv.pnloc[0], cv.pnloc[1], x1, y1, cv.fg)
                o += 4
            elif op == 0x22:
                y0, x0 = struct.unpack_from(">hh", data, o)
                dx, dy = struct.unpack_from(">bb", data, o + 4)
                cv.line(x0, y0, x0 + dx, y0 + dy, cv.fg)
                o += 6
            elif op == 0x23:
                dx, dy = struct.unpack_from(">bb", data, o)
                x0, y0 = cv.pnloc
                cv.line(x0, y0, x0 + dx, y0 + dy, cv.fg)
                o += 2
            elif 0x30 <= op <= 0x3F or 0x40 <= op <= 0x4F or 0x50 <= op <= 0x5F:
                kind = op & 0x07
                if (op & 0x0F) < 8:
                    last = struct.unpack_from(">hhhh", data, o)
                    o += 8
                t, l, b_, r = last
                col = cv.fg if kind in (0, 1, 3, 4) else (255, 255, 255)
                if op < 0x50:
                    if kind == 0:
                        cv.frame(t, l, b_, r, col)
                    elif kind in (1, 2, 4):
                        cv.fill(t, l, b_, r, col if kind != 4 else (128, 128, 128))
                else:
                    cv.oval(t, l, b_, r, col, kind != 0)
            elif 0x60 <= op <= 0x6F:
                o += 12 if (op & 0x0F) < 8 else 4
            elif 0x70 <= op <= 0x7F or 0x80 <= op <= 0x8F:
                if (op & 0x0F) < 8:
                    o += struct.unpack_from(">H", data, o)[0]
            elif op == 0x28:
                o += 4
                o += 1 + data[o]
            elif op in (0x29, 0x2A):
                o += 1
                o += 1 + data[o]
            elif op == 0x2B:
                o += 2
                o += 1 + data[o]
            elif op == 0x2C or op == 0x2E:
                o += 2 + struct.unpack_from(">H", data, o)[0]
            elif op == 0xA0:
                o += 2
            elif op == 0xA1:
                o += 4 + struct.unpack_from(">H", data, o + 2)[0]
            elif op == 0x1A:
                r, g, b_ = struct.unpack_from(">HHH", data, o)
                cv.fg = (r >> 8, g >> 8, b_ >> 8)
                o += 6
            elif op in _V1_SIZES:
                o += _V1_SIZES[op]
            elif v2 and 0x0100 <= op <= 0x7FFF:
                o += (op >> 8) * 2
            elif v2 and 0x8000 <= op <= 0x80FF:
                pass
            elif v2 and op >= 0x8100:
                o += 4 + struct.unpack_from(">I", data, o)[0]
            else:
                break  # unknown opcode: keep what was drawn so far
    except (struct.error, IndexError):
        pass
    return cv.w, cv.h, bytes(cv.px)


def to_png(data):
    r = decode(data)
    if r is None:
        return None
    w, h, px = r
    rows = [px[y * w * 4:(y + 1) * w * 4] for y in range(h)]
    return png_bytes(w, h, rows, 6)
