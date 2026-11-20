"""Tiny PNG writer (no dependencies)."""

import struct
import zlib


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_bytes(width, height, rows, color_type, bit_depth=8, palette=None, trns=None, level=6):
    out = [b"\x89PNG\r\n\x1a\n",
           _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0))]
    if palette is not None:
        out.append(_chunk(b"PLTE", b"".join(bytes(c[:3]) for c in palette)))
        if trns is not None:
            out.append(_chunk(b"tRNS", bytes(trns)))
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    out.append(_chunk(b"IDAT", zlib.compress(raw, level)))
    out.append(_chunk(b"IEND", b""))
    return b"".join(out)


def write_rgba(path, width, height, pixels):
    """pixels: list (row-major) of (r, g, b, a) tuples."""
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row.extend(pixels[y * width + x])
        rows.append(row)
    with open(path, "wb") as fh:
        fh.write(png_bytes(width, height, rows, 6))


# 2-bit indexed card pictures: 0 = black, 1 = white, 2 = transparent
_PAL3 = [(0, 0, 0), (255, 255, 255), (255, 255, 255)]
_TRNS3 = [255, 255, 0]
_TABLE = None


def _table():
    global _TABLE
    if _TABLE is None:
        t = []
        for i in range(256):
            for m in range(256):
                v = 0
                for b in range(7, -1, -1):
                    if (i >> b) & 1:
                        c = 0
                    elif (m >> b) & 1:
                        c = 1
                    else:
                        c = 2
                    v = (v << 2) | c
                t.append(struct.pack(">H", v))
        _TABLE = t
    return _TABLE


def bitmap_png(width, height, image_rows, mask_rows):
    """image_rows / mask_rows: lists of `bytes` (1 bit per pixel, MSB first)."""
    t = _table()
    nbytes = (width * 2 + 7) // 8
    rows = []
    for y in range(height):
        ir = image_rows[y]
        mr = mask_rows[y]
        row = b"".join([t[(a << 8) | b] for a, b in zip(ir, mr)])
        rows.append(row[:nbytes])
    return png_bytes(width, height, rows, 3, 2, _PAL3, _TRNS3)
