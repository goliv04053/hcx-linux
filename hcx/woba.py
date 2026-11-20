"""HyperCard card/background pictures ('BMAP' blocks, "Wrath Of Bill Atkinson")."""

import struct


def _rect(b, o):
    top, left, bottom, right = struct.unpack_from(">hhhh", b, o)
    return top, left, bottom, right


def _align32(r):
    top, left, bottom, right = r
    return top, left // 32 * 32, bottom, (right + 31) // 32 * 32


def decode_layer(data, offset, length, rect32):
    """Decode one compressed layer. Returns list of row `bytearray`s."""
    top, left, bottom, right = rect32
    rb = max(0, (right - left) // 8)
    h = max(0, bottom - top)
    out = bytearray(rb * h)
    end = min(len(data), offset + length)
    p = offset
    dx = dy = 0
    rep = [0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55]
    y = 0
    total_bytes = rb * h

    def put(base, pos, chunk):
        # write chunk at absolute position, clipped to the current row
        n = min(len(chunk), base + rb - pos, total_bytes - pos)
        if n > 0:
            out[pos:pos + n] = chunk[:n]

    while y < h:
        x = 0
        repeat = 1
        row_op = False
        base = y * rb
        while x < rb:
            if p >= end:
                return [out[i * rb:(i + 1) * rb] for i in range(h)]
            op = data[p]
            p += 1
            if op <= 0x7F:
                z = op & 0x0F
                d = op >> 4
                tot = z + d
                vals = data[p:p + d]
                p += d
                for r in range(repeat):
                    if d:
                        put(base, base + x + r * tot + z, vals)
                x += tot * repeat
                repeat = 1
            elif op == 0x80:
                rowdata = data[p:p + rb]
                p += rb
                for _ in range(repeat):
                    if y < h:
                        out[y * rb:(y + 1) * rb] = rowdata.ljust(rb, b"\0")
                        y += 1
                row_op = True
                break
            elif op == 0x81:
                y += repeat
                row_op = True
                break
            elif op == 0x82:
                for _ in range(repeat):
                    if y < h:
                        out[y * rb:(y + 1) * rb] = b"\xff" * rb
                        y += 1
                row_op = True
                break
            elif op == 0x83:
                v = data[p] if p < end else 0
                p += 1
                rep[y % 8] = v
                for _ in range(repeat):
                    if y < h:
                        out[y * rb:(y + 1) * rb] = bytes([v]) * rb
                        y += 1
                row_op = True
                break
            elif op == 0x84:
                for _ in range(repeat):
                    if y < h:
                        out[y * rb:(y + 1) * rb] = bytes([rep[y % 8]]) * rb
                        y += 1
                row_op = True
                break
            elif op in (0x85, 0x86, 0x87):
                back = op - 0x84
                for _ in range(repeat):
                    if y < h:
                        if y - back >= 0:
                            out[y * rb:(y + 1) * rb] = out[(y - back) * rb:(y - back + 1) * rb]
                        y += 1
                row_op = True
                break
            elif 0x88 <= op <= 0x8F:
                dx, dy = {0x88: (16, 0), 0x89: (0, 0), 0x8A: (0, 1), 0x8B: (0, 2),
                          0x8C: (1, 0), 0x8D: (1, 1), 0x8E: (2, 2), 0x8F: (8, 0)}[op]
            elif 0xA0 <= op <= 0xBF:
                repeat = op & 0x1F
            elif 0xC0 <= op <= 0xDF:
                n = (op & 0x1F) * 8
                vals = data[p:p + n]
                p += n
                for r in range(repeat):
                    put(base, base + x + r * n, vals)
                x += n * repeat
                repeat = 1
            elif op >= 0xE0:
                x += (op & 0x1F) * 16 * repeat
                repeat = 1
            else:
                # unknown opcode: data is over
                return [out[i * rb:(i + 1) * rb] for i in range(h)]
        if row_op:
            continue
        if rb:
            if dx:
                nbits = rb * 8
                v = int.from_bytes(out[base:base + rb], "big")
                s = dx
                while s < nbits:
                    v ^= v >> s
                    s *= 2
                out[base:base + rb] = v.to_bytes(rb, "big")
            if dy and y >= dy:
                a = int.from_bytes(out[base:base + rb], "big")
                b = int.from_bytes(out[base - dy * rb:base - dy * rb + rb], "big")
                out[base:base + rb] = (a ^ b).to_bytes(rb, "big")
        y += 1
    return [out[i * rb:(i + 1) * rb] for i in range(h)]


class Layer:
    """Either a decoded bitmap, a filled rectangle, or nothing."""

    def __init__(self, kind, rect=None, rect32=None, rows=None):
        self.kind = kind  # 'bitmap' | 'rect' | 'clear'
        self.rect = rect
        self.rect32 = rect32
        self.rows = rows


def _make_layer(rows, rect, rect32):
    if rect == (0, 0, 0, 0):
        return Layer("clear")
    if rows is not None:
        return Layer("bitmap", rect, rect32, rows)
    return Layer("rect", rect)


def _layer_to_card_rows(layer, width, height):
    """Render a layer as `height` rows of `int` bitmasks, `width` bits wide."""
    zero = [0] * height
    if layer.kind == "clear":
        return zero
    top, left, bottom, right = layer.rect
    l = max(0, left)
    r = min(width, right)
    if r <= l:
        return zero
    clip = ((1 << (r - l)) - 1) << (width - r)
    rows = list(zero)
    if layer.kind == "rect":
        for y in range(max(0, top), min(height, bottom)):
            rows[y] = clip
        return rows
    t32, l32, b32, r32 = layer.rect32
    w32 = r32 - l32
    for i, rowbytes in enumerate(layer.rows):
        y = t32 + i
        if y < max(0, top) or y >= min(height, bottom):
            continue
        v = int.from_bytes(rowbytes, "big")
        shift = width - l32 - w32
        v = v << shift if shift >= 0 else v >> (-shift)
        rows[y] = v & clip
    return rows


def decode_bmap(block, v1=False):
    """Decode a full 'BMAP' block. Returns (width, height, png_bytes) or None."""
    from .pngwrite import bitmap_png
    vo = -4 if v1 else 0
    card = _rect(block, 0x18 + vo)
    mrect = _rect(block, 0x20 + vo)
    irect = _rect(block, 0x28 + vo)
    mlen, ilen = struct.unpack_from(">II", block, 0x38 + vo)
    data_off = 0x40 + vo
    width = card[3] - card[1]
    height = card[2] - card[0]
    if width <= 0 or height <= 0 or width > 4096 or height > 4096:
        return None
    if len(block) <= data_off:
        mlayer = _make_layer(None, mrect, None)
        ilayer = _make_layer(None, irect, None)
    else:
        m32 = _align32(mrect)
        i32 = _align32(irect)
        mrows = decode_layer(block, data_off, mlen, m32) if mlen > 0 else None
        irows = decode_layer(block, data_off + mlen, ilen, i32) if ilen > 0 else None
        mlayer = _make_layer(mrows, mrect, m32)
        ilayer = _make_layer(irows, irect, i32)
    pw = (width + 7) // 8 * 8
    irows_i = _layer_to_card_rows(ilayer, pw, height)
    mrows_i = _layer_to_card_rows(mlayer, pw, height)
    nb = pw // 8
    img = [v.to_bytes(nb, "big") for v in irows_i]
    msk = [v.to_bytes(nb, "big") for v in mrows_i]
    return width, height, bitmap_png(width, height, img, msk)
