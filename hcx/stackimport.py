"""Port of HCX's `stackimport` tool: reads a HyperCard stack (data fork +
resource fork) and writes a `.hcxs` package (``_stack.xml`` + PNG/WAV files).

Usable from the command line too:  python3 -m hcx.stackimport STACK [OUTDIR]
"""

import hashlib
import os
import traceback
import re
import struct
import sys

from . import addcolor, macfiles, resources, woba
from .model import Content, Layer, Part, Stack, TEXT_STYLES, write_xml

IMPORTER_VERSION = "2"

_BTN_STYLES = {0: "transparent", 1: "opaque", 2: "rectangle", 3: "roundrect", 4: "shadow",
               5: "checkbox", 6: "radio", 7: "transparent", 8: "standard", 9: "default",
               10: "oval", 11: "popup"}
_FLD_STYLES = {0: "transparent", 1: "opaque", 2: "rectangle", 4: "shadow", 7: "scrolling"}


class ImportError_(Exception):
    pass


def u8(b, o):
    return b[o] if o < len(b) else 0


def u16(b, o):
    return struct.unpack_from(">H", b, o)[0] if o + 2 <= len(b) else 0


def s16(b, o):
    return struct.unpack_from(">h", b, o)[0] if o + 2 <= len(b) else 0


def u32(b, o):
    return struct.unpack_from(">I", b, o)[0] if o + 4 <= len(b) else 0


def cstr(b, o):
    end = b.find(b"\0", o)
    if end < 0:
        end = len(b)
    return b[o:end], end + 1


def style_set(flags):
    return {TEXT_STYLES[i] for i in range(8) if flags & (1 << i)}


def version_string(v):
    if v == 0:
        return ""
    major = (v >> 24) & 0xFF
    minor = (v >> 20) & 0xF
    bug = (v >> 16) & 0xF
    stage = {0x20: "d", 0x40: "a", 0x60: "b", 0x80: ""}.get((v >> 8) & 0xFF, "")
    rel = v & 0xFF
    s = "%d.%d" % (major, minor)
    if bug:
        s += ".%d" % bug
    if stage:
        s += "%s%d" % (stage, rel)
    return s


# ---------------------------------------------------------------------------
# private access stacks: header decryption (port of HyperCardPreview's hack)
# ---------------------------------------------------------------------------

def _hash_number(x):
    r = x * 0x41A7
    r += r >> 31
    return r & 0x7FFFFFFF


def decrypt_stak_header(stak):
    """Returns STAK block bytes with the encrypted range 0x18..0x4A decoded, or None."""
    xored = u32(stak, 0x18)
    xor = xored ^ u32(stak, 0)
    first16 = xor & 0xFFFF0000
    found = None
    for i in range(0x10000):
        value = first16 | i
        if value ^ (_hash_number(value) >> 16) != xor:
            continue
        h = value
        for _ in range(23):
            h = _hash_number(h)
        if 0 <= (u16(stak, 0x48) ^ (h & 0xFFFF)) <= 5:
            found = value
            break
    if found is None:
        return None
    x = found
    data = bytearray(stak[0x18:0x18 + 0x32])
    for i in range(0, 0x32 - 3, 2):
        data[i] ^= (x >> 24) & 0xFF
        data[i + 1] ^= (x >> 16) & 0xFF
        data[i + 2] ^= (x >> 8) & 0xFF
        data[i + 3] ^= x & 0xFF
        x = _hash_number(x)
    out = bytearray(stak)
    out[0x18:0x18 + 0x32] = data
    return bytes(out)


# ---------------------------------------------------------------------------
# Block table
# ---------------------------------------------------------------------------

def scan_blocks(data):
    """Map (type, id) -> block bytes, walking the data fork block by block."""
    blocks = {}
    order = []
    off = 0
    n = len(data)
    while off + 12 <= n:
        size = u32(data, off)
        btype = data[off + 4:off + 8]
        if size < 12 or off + size > n or not re.fullmatch(rb"[A-Za-z ]{4}", btype):
            # resynchronise on the next plausible block header
            nxt = -1
            for t in (b"CARD", b"BKGD", b"BMAP", b"PAGE", b"LIST", b"STBL", b"FTBL", b"FREE", b"TAIL", b"MAST", b"PRNT", b"PRST", b"PRFT"):
                k = data.find(t, off + 5)
                if k >= 4 and (nxt < 0 or k < nxt):
                    nxt = k
            if nxt < 0:
                break
            off = nxt - 4
            continue
        bid = struct.unpack_from(">i", data, off + 8)[0]
        t = btype.decode("ascii")
        block = data[off:off + size]
        if t != "FREE":
            blocks[(t, bid)] = block  # later blocks win only if not found via MAST
            order.append((t, bid, off))
        if t == "TAIL":
            break
        off += size
    return blocks, order


def master_lookup(data, stak):
    """Use the MAST table (like HyperCard) to locate blocks precisely."""
    mast_off = u32(stak, 0x18)
    ntables = 1 + u32(stak, 0x20)

    def find(btype, bid):
        table = bid >> 15
        slot_i = (bid >> 8) & 0x7F
        if table < 0 or table >= ntables:
            return None
        slot = u32(data, mast_off + table * 0x200 + slot_i * 4)
        if (slot & 0xFF) != (bid & 0xFF):
            return None
        off = (slot >> 8) * 0x20
        size = u32(data, off)
        if size < 12 or off + size > len(data):
            return None
        if data[off + 4:off + 8] != btype.encode() or u32(data, off + 8) != bid & 0xFFFFFFFF:
            return None
        return data[off:off + size]
    return find


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------

class StackImporter:
    def __init__(self, macfile, outdir, encoding="auto", progress=None):
        self.mf = macfile
        self.outdir = outdir
        self.encoding_opt = encoding
        self.progress = progress or (lambda frac, msg: None)
        self.stack = Stack()
        self.v1 = False

    # -- text ------------------------------------------------------------
    def text(self, raw):
        enc = self.stack.encoding
        try:
            s = raw.decode(enc)
        except UnicodeDecodeError:
            s = raw.decode("cp932" if enc == "shift_jis" else "mac_roman", errors="replace")
        return s.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")

    def detect_encoding(self, raw_names, font_ids):
        if self.encoding_opt in ("mac_roman", "shift_jis"):
            return self.encoding_opt
        if any(16384 <= f <= 16895 for f in font_ids):
            return "shift_jis"
        joined = b" ".join(raw_names)
        if b"Osaka" in joined:
            return "shift_jis"
        for n in raw_names:
            if any(c >= 0x80 for c in n):
                try:
                    s = n.decode("shift_jis")
                    if any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in s):
                        return "shift_jis"
                except UnicodeDecodeError:
                    pass
        return "mac_roman"

    # -- main -----------------------------------------------------------
    def run(self):
        data = self.mf.data
        if not macfiles.looks_like_stack(data):
            raise ImportError_("not a stack")
        os.makedirs(self.outdir, exist_ok=True)
        st = self.stack
        st.dir_path = self.outdir
        st.name = re.sub(r"\.(stack|stk|stak)$", "", self.mf.name, flags=re.I) or "Stack"

        stak_size = u32(data, 0)
        stak = data[:stak_size]
        fmt = u32(stak, 0x10)
        self.v1 = fmt <= 8
        st.format = fmt
        flags = u16(stak, 0x4C)
        st.private_access = bool(flags & (1 << 13))
        dec = stak
        if st.private_access:
            d = decrypt_stak_header(stak)
            if d is None:
                raise ImportError_("private")
            dec = d
        st.cant_peek = bool(flags & (1 << 10))
        st.cant_abort = bool(flags & (1 << 11))
        st.cant_delete = bool(flags & (1 << 14))
        st.cant_modify = bool(flags & (1 << 15))
        st.id = struct.unpack_from(">i", stak, 8)[0]
        st.password = u32(dec, 0x44)
        st.user_level = u16(dec, 0x48) or 5
        st.created_by = version_string(u32(stak, 0x60))
        st.compacted_by = version_string(u32(stak, 0x64))
        st.modified_by = version_string(u32(stak, 0x68))
        st.opened_by = version_string(u32(stak, 0x6C))
        h, w = u16(stak, 0x1B8), u16(stak, 0x1BA)
        st.height = h if 0 < h < 4000 else 342
        st.width = w if 0 < w < 4000 else 512

        blocks, order = scan_blocks(data)
        find_master = master_lookup(data, dec)

        def block(t, bid):
            b = None
            try:
                b = find_master(t, bid)
            except Exception:
                b = None
            return b if b is not None else blocks.get((t, bid))

        self.block = block
        vo = -4 if self.v1 else 0

        # fonts & styles (needed for encoding detection)
        raw_font_names = []
        ftbl_id = u32(stak, 0x1B0)
        ftbl = block("FTBL", ftbl_id) if ftbl_id else None
        raw_fonts = {}
        if ftbl is not None:
            count = u32(ftbl, 0x10)
            o = 0x18
            for _ in range(min(count, 5000)):
                fid = u16(ftbl, o)
                raw, nxt = cstr(ftbl, o + 2)
                raw_fonts[fid] = raw
                raw_font_names.append(raw)
                o = nxt
                if o & 1:
                    o += 1
                if o >= len(ftbl):
                    break
        stbl_id = u32(stak, 0x1B4)
        stbl = block("STBL", stbl_id) if stbl_id else None
        used_fonts = []
        if stbl is not None:
            count = u32(stbl, 0x10)
            st.next_style_id = u32(stbl, 0x14)
            o = 0x18
            for _ in range(min(count, 100000)):
                if o + 0x18 > len(stbl):
                    break
                sid = u32(stbl, o)
                font = s16(stbl, o + 0xC)
                sflags = s16(stbl, o + 0xE)
                size = s16(stbl, o + 0x10)
                st.styles[sid] = {"font": None if font == -1 else font,
                                  "size": None if size == -1 else size,
                                  "style": None if sflags == -1 else style_set((sflags >> 8) & 0xFF)}
                if font != -1:
                    used_fonts.append(font)
                o += 0x18
        script_raw = cstr(stak, 0x600)[0] if stak_size > 0x600 else b""
        st.encoding = self.detect_encoding(raw_font_names + [script_raw[:2000]], used_fonts)
        resources.ENCODING[0] = st.encoding
        st.fonts = {fid: self.text(raw) for fid, raw in raw_fonts.items()}
        st.script = self.text(script_raw)

        # patterns
        for i in range(40):
            pat = stak[0x2C0 + i * 8:0x2C0 + i * 8 + 8]
            px = bytearray()
            for y in range(8):
                for x in range(8):
                    bit = (pat[y] >> (7 - x)) & 1 if y < len(pat) else 0
                    px += b"\0\0\0\xff" if bit else b"\xff\xff\xff\xff"
            fn = "PAT_%d.png" % (i + 1)
            with open(os.path.join(self.outdir, fn), "wb") as fh:
                fh.write(resources._rgba_png(8, 8, px))
            st.patterns.append(fn)

        # resources
        self.progress(0.02, "resources")
        st.media = resources.convert_resources(self.mf.rsrc, self.outdir)
        if self.mf.rsrc:
            # kept whole so scripts and externals can read STR/CSTR/TEXT resources
            with open(os.path.join(self.outdir, "_rsrc.bin"), "wb") as fh:
                fh.write(self.mf.rsrc)
            try:
                addcolor.build(resources.parse_resource_fork(self.mf.rsrc), self.outdir,
                               getattr(st, "source_path", None), self.mf.path)
            except Exception:  # noqa: BLE001 - color is optional, the stack still opens without it
                traceback.print_exc()

        # backgrounds (linked list)
        first_bg = u32(dec, 0x28)
        bg_ids = []
        cur = first_bg
        while cur and cur not in bg_ids:
            b = block("BKGD", cur)
            if b is None:
                break
            bg_ids.append(cur)
            cur = u32(b, 0x1C + vo)
            if cur == first_bg:
                break
        if not bg_ids:
            bg_ids = [bid for (t, bid, _) in order if t == "BKGD"]

        # cards (LIST -> PAGEs)
        card_refs = []
        list_id = u32(dec, 0x34)
        lst = block("LIST", list_id)
        if lst is not None:
            npages = u32(lst, 0x10 + vo)
            entry_size = u16(lst, 0x1C + vo) or 8
            o = 0x30
            for _ in range(min(npages, 100000)):
                pid, ncards = u32(lst, o), u16(lst, o + 4)
                o += 6
                page = block("PAGE", pid)
                if page is None:
                    continue
                po = 0x18
                for _ in range(ncards):
                    cid = u32(page, po)
                    marked = bool(u8(page, po + 4) & 0x10)
                    if cid:
                        card_refs.append((cid, marked))
                    po += entry_size
        if not card_refs:
            card_refs = [(bid, False) for (t, bid, _) in order if t == "CARD"]

        # first card first (HyperCard order already starts at card 1)
        total = len(bg_ids) + len(card_refs)
        done = 0
        self.bitmaps_done = {}
        for bid in bg_ids:
            b = block("BKGD", bid)
            if b is not None:
                st.backgrounds.append(self.read_layer(b, "background"))
            done += 1
            self.progress(0.05 + 0.9 * done / max(1, total), "bg")
        for cid, marked in card_refs:
            b = block("CARD", cid)
            if b is None:
                continue
            cd = self.read_layer(b, "card")
            cd.marked = marked
            if st.bg_by_id(cd.bg_id) is None:
                bb = block("BKGD", cd.bg_id)
                if bb is not None:
                    st.backgrounds.append(self.read_layer(bb, "background"))
            st.cards.append(cd)
            done += 1
            if done % 5 == 0:
                self.progress(0.05 + 0.9 * done / max(1, total), "card")
        if not st.cards:
            raise ImportError_("no cards")
        self.progress(0.97, "xml")
        write_xml(st, os.path.join(self.outdir, "_stack.xml"))
        with open(os.path.join(self.outdir, ".hcx-import"), "w") as fh:
            fh.write(IMPORTER_VERSION + "\n" + st.encoding + "\n")
        self.progress(1.0, "done")
        return st

    # -- bitmaps ----------------------------------------------------------
    def bitmap(self, bmap_id):
        if not bmap_id:
            return None
        if bmap_id in self.bitmaps_done:
            return self.bitmaps_done[bmap_id]
        fn = None
        b = self.block("BMAP", bmap_id)
        if b is not None:
            try:
                res = woba.decode_bmap(b, self.v1)
            except Exception:
                res = None
            if res:
                fn = "BMAP_%d.png" % bmap_id
                with open(os.path.join(self.outdir, fn), "wb") as fh:
                    fh.write(res[2])
        self.bitmaps_done[bmap_id] = fn
        return fn

    # -- layers -------------------------------------------------------------
    def read_layer(self, b, kind):
        vo = -4 if self.v1 else 0
        L = Layer(kind)
        L.id = struct.unpack_from(">i", b, 8)[0]
        flags = u16(b, 0x14 + vo)
        L.cant_delete = bool(flags & (1 << 14))
        L.show_pict = not (flags & (1 << 13))
        L.dont_search = bool(flags & (1 << 11))
        L.bitmap = self.bitmap(u32(b, 0x10 + vo))
        part_off = (0x36 if kind == "card" else 0x32) + vo
        nparts = u16(b, part_off - 0xE)
        parts_size = u32(b, part_off - 0xA)
        ncontents = u16(b, part_off - 0x6)
        contents_size = u32(b, part_off - 0x4)
        if kind == "card":
            L.bg_id = u32(b, 0x24 + vo)
        o = part_off
        for _ in range(nparts):
            size = u16(b, o)
            if size < 0x1E or o + size > len(b):
                break
            p = self.read_part(b[o:o + size])
            if p is not None:
                L.parts.append(p)
            o += size
        o = part_off + parts_size
        for _ in range(ncontents):
            if o + 2 > len(b):
                break
            pid = s16(b, o)
            if self.v1:
                raw, nxt = cstr(b, o + 2)
                c = Content(self.text(raw))
                o = nxt
            else:
                size = u16(b, o + 2)
                chunk = b[o + 4:o + 4 + size]
                o += 4 + size
                if o & 1:
                    o += 1
                c = Content()
                if chunk and chunk[0] & 0x80:
                    flen = u16(chunk, 0) & 0x7FFF
                    raw = chunk[flen:]
                    for k in range(2, flen - 3, 4):
                        boff = u16(chunk, k)
                        # byte offset (Mac encoding) -> character offset
                        coff = boff if self.stack.encoding == "mac_roman" else len(self.text(raw[:boff]))
                        c.runs.append((coff, u16(chunk, k + 2)))
                    c.text = self.text(raw)
                else:
                    c.text = self.text(chunk[1:])
            if pid < 0:
                L.contents[-pid] = c
            elif kind == "card":
                L.bg_contents[pid] = c
            else:
                L.contents[pid] = c
        no = part_off + parts_size + contents_size
        name, nxt = cstr(b, no) if no < len(b) else (b"", no)
        L.name = self.text(name)
        L.script = self.text(cstr(b, nxt)[0]) if nxt < len(b) else ""
        return L

    def read_part(self, d):
        ptype = d[4]
        if ptype not in (1, 2):
            return None
        p = Part("button" if ptype == 1 else "field")
        p.id = u16(d, 2)
        f = d[5]
        p.visible = not (f & 0x80)
        p.top, p.left, p.bottom, p.right = struct.unpack_from(">hhhh", d, 6)
        f2 = d[0xE]
        style = d[0xF]
        a = s16(d, 0x14)
        p.text_align = {0: "left", 1: "center", -1: "right"}.get(a, "left")
        fid = s16(d, 0x16)
        p.text_font_id = fid if fid >= 0 else -fid - 1
        p.text_size = u16(d, 0x18)
        p.text_style = style_set(d[0x1A])
        p.text_height = u16(d, 0x1C)
        name, nxt = cstr(d, 0x1E)
        p.name = self.text(name)
        p.script = self.text(cstr(d, nxt + 1)[0]) if nxt + 1 < len(d) else ""
        if p.type == "button":
            p.enabled = not (f & 0x01)
            p.show_name = bool(f2 & 0x80)
            p.hilite = bool(f2 & 0x40)
            p.auto_hilite = bool(f2 & 0x20)
            p.shared_hilite = not (f2 & 0x10)
            p.family = f2 & 0x0F
            p.style = _BTN_STYLES.get(style, "transparent")
            p.title_width = u16(d, 0x10)
            if p.style == "popup":
                p.selected_line = u16(d, 0x12)
            else:
                p.icon = s16(d, 0x12)
        else:
            p.lock_text = bool(f & 0x01)
            p.auto_tab = bool(f & 0x02)
            p.fixed_line_height = not (f & 0x04)
            p.shared_text = bool(f & 0x08)
            p.dont_search = bool(f & 0x10)
            p.dont_wrap = bool(f & 0x20)
            p.auto_select = bool(f2 & 0x80)
            p.show_lines = bool(f2 & 0x40)
            p.wide_margins = bool(f2 & 0x20)
            p.multiple_lines = bool(f2 & 0x10)
            p.style = _FLD_STYLES.get(style, "transparent")
            p.selected_line = u16(d, 0x12)
        return p


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def cache_root():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "hcx", "stacks")


def package_dir_for(macfile, source_path, encoding="auto"):
    h = hashlib.sha1()
    h.update(macfile.data)
    h.update(macfile.rsrc)
    h.update(encoding.encode())
    safe = re.sub(r"[^\w\- .()]", "_", macfile.name).strip() or "stack"
    return os.path.join(cache_root(), "%s-%s.hcxs" % (safe[:60], h.hexdigest()[:12]))


def is_imported(pkg):
    try:
        with open(os.path.join(pkg, ".hcx-import")) as fh:
            return fh.readline().strip() == IMPORTER_VERSION and os.path.exists(os.path.join(pkg, "_stack.xml"))
    except OSError:
        return False


def import_stack(macfile, source_path, encoding="auto", progress=None, force=False, outdir=None):
    pkg = outdir or package_dir_for(macfile, source_path, encoding)
    if not force and is_imported(pkg):
        return pkg
    st = StackImporter(macfile, pkg, encoding, progress)
    st.stack.source_path = source_path
    st.run()
    return pkg


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: stackimport STACK|IMAGE [OUTDIR]")
        return 2
    res = macfiles.open_path(argv[0])
    stacks = res.stacks
    if not stacks:
        print("no HyperCard stack found in", argv[0])
        return 1
    for mf in stacks:
        out = None
        if len(argv) > 1:
            out = os.path.join(argv[1], re.sub(r"[^\w\- .()]", "_", mf.name) + ".hcxs")
        pkg = import_stack(mf, argv[0], progress=None, force=True, outdir=out)
        print(pkg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
