"""The Mac's own fonts, taken from a Mac disk the user has (a System file or
font suitcases), so that stacks are drawn with Chicago, Geneva, Monaco and
New York and lines break where they broke on the Mac.

Apple's fonts can't be shipped with HCX, so nothing is bundled: when the
user opens a Mac disk image that has fonts, HCX offers to copy them into
~/.local/share/hcx/fonts. Two kinds are kept:

* bitmap strikes (NFNT/FONT resources) become small TrueType fonts whose
  glyphs are made of the strike's pixels, one font per family and size
  ("HCX Geneva 12"), with the Mac's exact character widths;
* TrueType fonts in the suitcases ('sfnt' resources) are saved as .ttf and
  used for the sizes that have no bitmap.

The fonts are registered with fontconfig for this process only."""

import ctypes
import ctypes.util
import os
import re
import struct
import time

from .resources import parse_resource_fork

PIXEL = 64           # font units per pixel in the generated fonts


def fonts_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "hcx", "fonts")


# -- reading Mac font resources ----------------------------------------------------------------

def parse_fond(data):
    """(family name, [(size, style, resource id)]) of a FOND resource."""
    if len(data) < 54:
        return None, []
    n = struct.unpack_from(">h", data, 52)[0] + 1
    out = []
    for k in range(max(0, n)):
        o = 54 + 6 * k
        if o + 6 > len(data):
            break
        size, style, rid = struct.unpack_from(">hhh", data, o)
        out.append((size, style, rid))
    return None, out


def parse_strike(data):
    """A bitmap font (NFNT or FONT resource) as a dict, or None."""
    if len(data) < 26:
        return None
    (ftype, first, last, wid_max, kern_max, n_descent, rect_w, rect_h, owtloc,
     ascent, descent, leading, row_words) = struct.unpack_from(">hhhhhhhhHhhhh", data, 0)
    if last < first or rect_h <= 0 or row_words <= 0 or last > 255 or first < 0:
        return None
    row_bytes = row_words * 2
    img_off = 26
    img_len = row_bytes * rect_h
    loc_off = img_off + img_len
    n = last - first + 3
    ow_off = 16 + owtloc * 2          # the owTLoc word offset counts from its own field
    if n_descent > 0:
        ow_off += n_descent << 17     # high word of the offset (fonts larger than 128 KB)
    # the offset/width table's final -1 word is sometimes left out
    n_ow = min(n, (len(data) - ow_off) // 2)
    if n_ow < n - 1 or loc_off + n * 2 > len(data):
        return None
    loc = struct.unpack_from(">%dH" % n, data, loc_off)
    ow = struct.unpack_from(">%dH" % n_ow, data, ow_off) + (0xFFFF,) * (n - n_ow)
    img = data[img_off:img_off + img_len]
    return {"first": first, "last": last, "kern_max": kern_max, "ascent": ascent, "descent": descent,
            "leading": leading, "rect_h": rect_h, "row_bytes": row_bytes, "img": img, "loc": loc, "ow": ow}


def _glyph(strike, k):
    """(advance width, x offset, [rows of 0/1]) of glyph k (0-based from first), or None if missing."""
    ow = strike["ow"][k]
    if ow == 0xFFFF:
        return None
    off, width = ow >> 8, ow & 0xFF
    a, b = strike["loc"][k], strike["loc"][k + 1]
    if b < a:
        b = a
    rb = strike["row_bytes"]
    img = strike["img"]
    rows = []
    for y in range(strike["rect_h"]):
        base = y * rb
        rows.append([(img[base + (x >> 3)] >> (7 - (x & 7))) & 1 if base + (x >> 3) < len(img) else 0
                     for x in range(a, b)])
    return width, strike["kern_max"] + off, rows


# -- building a TrueType font from a strike --------------------------------------------------------

def _checksum(b):
    b = b + b"\0" * (-len(b) % 4)
    return sum(struct.unpack(">%dI" % (len(b) // 4), b)) & 0xFFFFFFFF


def _name_table(family, style, ps):
    names = [(1, family), (2, style), (3, "HCX: " + ps), (4, family + ("" if style == "Regular" else " " + style)),
             (5, "Version 1.0"), (6, ps)]
    recs, strings = [], b""
    for nid, s in names:
        enc = s.encode("utf-16-be")
        recs.append(struct.pack(">HHHHHH", 3, 1, 0x409, nid, len(enc), len(strings)))
        strings += enc
    return struct.pack(">HHH", 0, len(recs), 6 + 12 * len(recs)) + b"".join(recs) + strings


def _cmap_table(mapping):
    """Format 4 cmap for {unicode: glyph id}."""
    codes = sorted(c for c in mapping if c < 0xFFFF)
    segs = [(c, c, mapping[c]) for c in codes] + [(0xFFFF, 0xFFFF, 0)]
    n = len(segs)
    ends = b"".join(struct.pack(">H", e) for _s, e, _g in segs)
    starts = b"".join(struct.pack(">H", s) for s, _e, _g in segs)
    deltas = b"".join(struct.pack(">H", ((g - s) & 0xFFFF) if g else 1) for s, _e, g in segs)
    offs = b"\0\0" * n
    sr = 2 * (1 << (n.bit_length() - 1))
    sub = struct.pack(">HHHHHHH", 4, 0, 0, 2 * n, sr, (sr // 2).bit_length() - 1, 2 * n - sr)
    sub += ends + b"\0\0" + starts + deltas + offs
    sub = sub[:2] + struct.pack(">H", len(sub)) + sub[4:]
    return struct.pack(">HHHHI", 0, 1, 3, 1, 12) + sub


def build_ttf(strike, family, size, bold=False, italic=False):
    """A TrueType font whose glyphs are the strike's pixels (PIXEL units each)."""
    U = PIXEL
    upem = max(16, min(16384, size * U))
    asc = strike["ascent"]
    glyphs = []           # (advance, contours, xmin, ymin, xmax, ymax)
    cmap = {}
    first = strike["first"]
    n = strike["last"] - first + 1

    def outline(g):
        adv, x0, rows = g
        contours = []
        for y, row in enumerate(rows):
            x = 0
            while x < len(row):
                if not row[x]:
                    x += 1
                    continue
                s = x
                while x < len(row) and row[x]:
                    x += 1
                left, right = (x0 + s) * U, (x0 + x) * U
                top, bottom = (asc - y) * U, (asc - y - 1) * U
                contours.append([(left, bottom), (left, top), (right, top), (right, bottom)])
        if contours:
            xs = [p[0] for c in contours for p in c]
            ys = [p[1] for c in contours for p in c]
            return adv * U, contours, min(xs), min(ys), max(xs), max(ys)
        return adv * U, [], 0, 0, 0, 0

    missing = _glyph(strike, n)        # the strike's "missing character" glyph
    glyphs.append(outline(missing) if missing else (size * U // 2, [], 0, 0, 0, 0))
    for k in range(n):
        g = _glyph(strike, k)
        if g is None:
            continue
        code = first + k
        if code < 32:
            continue
        try:
            ch = bytes((code,)).decode("mac_roman")
        except UnicodeDecodeError:
            continue
        cmap[ord(ch)] = len(glyphs)
        if code == 0xCA:                         # option-space is a non-breaking space
            cmap.setdefault(0x20, len(glyphs))
        glyphs.append(outline(g))
    if 0x20 not in cmap:
        cmap[0x20] = len(glyphs)
        glyphs.append((size * U // 3, [], 0, 0, 0, 0))

    glyf, loca = b"", [0]
    max_pts = max_cont = 0
    for adv, contours, xmin, ymin, xmax, ymax in glyphs:
        if contours:
            ends, pts = [], []
            for c in contours:
                pts += c
                ends.append(len(pts) - 1)
            g = struct.pack(">hhhhh", len(contours), xmin, ymin, xmax, ymax)
            g += b"".join(struct.pack(">H", e) for e in ends) + b"\0\0"
            g += b"\x01" * len(pts)
            px = py = 0
            xs, ys = b"", b""
            for x, y in pts:
                xs += struct.pack(">h", x - px)
                ys += struct.pack(">h", y - py)
                px, py = x, y
            g += xs + ys
            g += b"\0" * (-len(g) % 4)
            glyf += g
            max_pts = max(max_pts, len(pts))
            max_cont = max(max_cont, len(contours))
        loca.append(len(glyf))
    ng = len(glyphs)
    all_x = [v for g in glyphs if g[1] for v in (g[2], g[4])] or [0]
    all_y = [v for g in glyphs if g[1] for v in (g[3], g[5])] or [0]
    xmin, xmax, ymin, ymax = min(all_x), max(all_x), min(all_y), max(all_y)
    ascender, descender, gap = asc * U, -strike["descent"] * U, max(0, strike["leading"]) * U
    adv_max = max(g[0] for g in glyphs)
    mac_style = (1 if bold else 0) | (2 if italic else 0)
    t = int(time.time()) + 2082844800
    head = struct.pack(">HHIIIHHqqhhhhHHhhh", 1, 0, 0x10000, 0, 0x5F0F3CF5, 0x000B, upem, t, t,
                       xmin, ymin, xmax, ymax, mac_style, 8, 2, 1, 0)
    hhea = struct.pack(">HHhhhHhhhhhhhhhhhH", 1, 0, ascender, descender, gap, adv_max,
                       min([g[2] for g in glyphs if g[1]] or [0]), 0, xmax, 1, 0, 0, 0, 0, 0, 0, 0, ng)
    maxp = struct.pack(">IHHHHHHHHHHHHHH", 0x10000, ng, max_pts, max_cont, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0)
    hmtx = b"".join(struct.pack(">Hh", g[0], g[2]) for g in glyphs)
    style_name = ("Bold " if bold else "") + ("Italic" if italic else "")
    style_name = style_name.strip() or "Regular"
    ps = re.sub(r"[^A-Za-z0-9-]", "", family.replace(" ", "-")) + ("-" + style_name.replace(" ", "") if style_name != "Regular" else "")
    fs_sel = (0x20 if bold else 0) | (0x01 if italic else 0) or 0x40
    os2 = struct.pack(">HhHHHhhhhhhhhhhh", 4, adv_max // 2, 700 if bold else 400, 5, 0,
                      upem // 2, upem // 2, 0, 0, upem // 2, upem // 2, 0, upem // 2, upem // 20, upem // 4, 0)
    os2 += b"\0" * 10                             # PANOSE
    os2 += struct.pack(">IIII", 1, 0, 0, 0)       # Unicode ranges: Basic Latin
    os2 += b"HCX "
    codes = [c for c in cmap if c < 0xFFFF]
    os2 += struct.pack(">HHH", fs_sel, min(codes), max(codes))
    os2 += struct.pack(">hhhHH", ascender, descender, gap, ascender, -descender)
    os2 += struct.pack(">II", 1, 0)
    os2 += struct.pack(">hhHHH", strike["rect_h"] * U // 2, ascender, 0, 0x20, 0)
    post = struct.pack(">IIhhIIIII", 0x30000, 0, -U, U, 0, 0, 0, 0, 0)
    tables = {"OS/2": os2, "cmap": _cmap_table(cmap), "glyf": glyf, "head": head, "hhea": hhea,
              "hmtx": hmtx, "loca": b"".join(struct.pack(">I", v) for v in loca), "maxp": maxp,
              "name": _name_table(family, style_name, ps), "post": post}
    tags = sorted(tables)
    nt = len(tags)
    sr = 16 * (1 << (nt.bit_length() - 1))
    out = struct.pack(">IHHHH", 0x10000, nt, sr, (sr // 16).bit_length() - 1, nt * 16 - sr)
    off = 12 + 16 * nt
    dirs, body = b"", b""
    head_off = 0
    for tag in tags:
        d = tables[tag]
        if tag == "head":
            head_off = off
        dirs += struct.pack(">4sIII", tag.encode("latin-1"), _checksum(d), off, len(d))
        pad = d + b"\0" * (-len(d) % 4)
        body += pad
        off += len(pad)
    font = bytearray(out + dirs + body)
    adj = (0xB1B0AFBA - _checksum(bytes(font))) & 0xFFFFFFFF
    struct.pack_into(">I", font, head_off + 8, adj)
    return bytes(font)


# -- importing from a disk --------------------------------------------------------------------------

def _clean(name):
    return re.sub(r"[^\w .-]", "", name).strip() or "Font"


def collect(files):
    """The fonts in a list of MacFile: {"strikes": [(family, size, style, data)],
    "sfnt": [(family, style, data)]} from FOND/NFNT/FONT/sfnt resources."""
    strikes, sfnts = [], []
    seen = set()
    for f in files:
        if not f.rsrc or f.type not in (b"FFIL", b"zsys", b"FKEY", b"tfil", b"ffil", b"suit"):
            continue
        res = parse_resource_fork(f.rsrc)
        by = {}
        for t, rid, name, data in res:
            by.setdefault(t, {})[rid] = (name, data)
        fonds = by.get("FOND", {})
        used = set()
        for fid, (fname, fdata) in fonds.items():
            if not fname:
                continue
            _n, assoc = parse_fond(fdata)
            for size, style, rid in assoc:
                if size == 0:
                    src = by.get("sfnt", {}).get(rid)
                    if src is not None and (fname.lower(), style, "sfnt") not in seen:
                        seen.add((fname.lower(), style, "sfnt"))
                        sfnts.append((fname, style, src[1]))
                    continue
                src = by.get("NFNT", {}).get(rid) or by.get("FONT", {}).get(rid)
                if src is None or (fname.lower(), size, style) in seen:
                    continue
                seen.add((fname.lower(), size, style))
                strikes.append((fname, size, style, src[1]))
                used.add(rid)
        # old FONT resources without a FOND: id = family * 128 + size, name on the size-0 entry
        fams = {rid >> 7: name for rid, (name, _d) in by.get("FONT", {}).items() if rid & 0x7F == 0 and name}
        for rid, (_name, data) in by.get("FONT", {}).items():
            size = rid & 0x7F
            fam = fams.get(rid >> 7)
            if size and fam and rid not in used and (fam.lower(), size, 0) not in seen:
                seen.add((fam.lower(), size, 0))
                strikes.append((fam, size, 0, data))
    return {"strikes": strikes, "sfnt": sfnts}


def importable(files):
    """Family names of the fonts in files that HCX hasn't imported yet."""
    have = installed()
    c = collect(files)
    out = []
    for fam, size, style, _d in c["strikes"]:
        if style in (0, 1) and (fam.lower(), size, style) not in have["strikes"] and fam not in out:
            out.append(fam)
    for fam, style, _d in c["sfnt"]:
        if (fam.lower(), style) not in have["sfnt"] and fam not in out:
            out.append(fam)
    first = ["Chicago", "Geneva", "New York", "Monaco"]
    return sorted(out, key=lambda f: (first.index(f) if f in first else len(first), f))


def import_fonts(files):
    """Write the fonts found in files to fonts_dir(). Returns the number of font files written."""
    d = fonts_dir()
    os.makedirs(d, exist_ok=True)
    c = collect(files)
    count = 0
    for fam, size, style, data in c["strikes"]:
        if style not in (0, 1, 2, 3):
            continue
        strike = parse_strike(data)
        if strike is None:
            continue
        bold, italic = bool(style & 1), bool(style & 2)
        family = "HCX %s %d" % (fam, size)
        try:
            ttf = build_ttf(strike, family, size, bold, italic)
        except (struct.error, ValueError, IndexError):
            continue
        name = "%s-%d-%d.ttf" % (_clean(fam), size, style)
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(ttf)
        count += 1
    for fam, style, data in c["sfnt"]:
        if data[:4] not in (b"\0\1\0\0", b"true"):
            continue
        name = "%s-tt-%d.ttf" % (_clean(fam), style)
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(data)
        count += 1
    _INSTALLED.clear()
    register()
    return count


_INSTALLED = {}


def installed():
    """{"strikes": {(family lower, size, style)}, "sfnt": {(family lower, style)}} of imported fonts."""
    if _INSTALLED:
        return _INSTALLED
    strikes, sfnt = set(), set()
    try:
        names = os.listdir(fonts_dir())
    except OSError:
        names = []
    for n in names:
        m = re.match(r"(.+)-tt-(\d+)\.ttf$", n)
        if m:
            sfnt.add((m.group(1).lower(), int(m.group(2))))
            continue
        m = re.match(r"(.+)-(\d+)-(\d+)\.ttf$", n)
        if m:
            strikes.add((m.group(1).lower(), int(m.group(2)), int(m.group(3))))
    _INSTALLED.update(strikes=strikes, sfnt=sfnt)
    return _INSTALLED


def family_for(mac_name, size):
    """The fontconfig family to draw mac_name at size pixels with, or None
    if the user has no Mac font for it."""
    have = installed()
    low = _clean(mac_name or "").lower()
    if not low:
        return None
    if (low, int(size), 0) in have["strikes"]:
        return "HCX %s %d" % (mac_name, int(size))
    if (low, 0) in have["sfnt"]:
        return mac_name
    return None


_REGISTERED = set()


def register():
    """Make the imported fonts visible to fontconfig (and so to Pango) in this process."""
    d = fonts_dir()
    if not os.path.isdir(d):
        return False
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("fontconfig") or "libfontconfig.so.1")
        lib.FcConfigGetCurrent.restype = ctypes.c_void_p
        lib.FcConfigAppFontAddDir.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cfg = lib.FcConfigGetCurrent()
        added = False
        for n in sorted(os.listdir(d)):
            p = os.path.join(d, n)
            if n.endswith(".ttf") and p not in _REGISTERED:
                if lib.FcConfigAppFontAddFile(cfg, p.encode()):
                    _REGISTERED.add(p)
                    added = True
    except (OSError, AttributeError):
        return False
    if added:
        try:
            from gi.repository import PangoCairo
            PangoCairo.FontMap.get_default().changed()
        except Exception:  # noqa: BLE001
            pass
    return True
