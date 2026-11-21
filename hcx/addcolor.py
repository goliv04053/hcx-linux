"""Color for HyperCard 2.x stacks made with the Color Tools (the AddColor XCMD).

HyperCard itself is black and white. The Color Tools keep a list of colored
objects per card and per background in resources 'HCcd' and 'HCbg' (the
resource id is the card or background id). AddColor paints those objects
in color and then lays the black-and-white card over them, so black stays
black and white shows the color underneath.

Each object in the list starts with a type byte; 0x80 set means hidden:

  1 button   id:2  bevel:2  red:2 green:2 blue:2
  2 field    id:2  bevel:2  red:2 green:2 blue:2
  3 rect     top:2 left:2 bottom:2 right:2  bevel:2  red:2 green:2 blue:2
  4 picture  top:2 left:2 bottom:2 right:2  transparent:1  name:pstring   (PICT resource)
  5 picture  top:2 left:2 bottom:2 right:2  transparent:1  name:pstring   (PICT file)

The importer turns them into addcolor.json (pictures into PNG files) in the
stack package; the renderer reads that file."""

import json
import os
import struct

from . import pict
from .pngwrite import png_bytes

FILE = "addcolor.json"
DIR = "addcolor"


def parse(data):
    """The objects of one HCcd/HCbg resource, as dicts."""
    out = []
    i = 0
    n = len(data)
    while i < n:
        t = data[i]
        kind = t & 0x7F
        hidden = bool(t & 0x80)
        try:
            if kind in (1, 2):
                pid, bevel, r, g, b = struct.unpack_from(">hhHHH", data, i + 1)
                out.append({"type": "button" if kind == 1 else "field", "id": pid, "bevel": bevel,
                            "rgb": [r, g, b], "hidden": hidden})
                i += 11
            elif kind == 3:
                top, left, bottom, right, bevel, r, g, b = struct.unpack_from(">hhhhhHHH", data, i + 1)
                out.append({"type": "rect", "rect": [left, top, right, bottom], "bevel": bevel,
                            "rgb": [r, g, b], "hidden": hidden})
                i += 17
            elif kind in (4, 5):
                top, left, bottom, right = struct.unpack_from(">hhhh", data, i + 1)
                transparent = bool(data[i + 9])
                ln = data[i + 10]
                name = data[i + 11:i + 11 + ln].decode("mac_roman", "replace")
                out.append({"type": "pict" if kind == 4 else "pictfile", "rect": [left, top, right, bottom],
                            "transparent": transparent, "name": name, "hidden": hidden})
                i += 11 + ln
            else:
                break       # unknown object: stop rather than misread the rest
        except (struct.error, IndexError):
            break
    return out


def _png(data, transparent):
    """PICT data (no 512-byte header) to PNG; white becomes see-through if transparent."""
    r = pict.decode(data)
    if r is None:
        return None
    w, h, px = r
    px = bytearray(px)
    if transparent:
        for k in range(0, len(px), 4):
            if px[k] >= 250 and px[k + 1] >= 250 and px[k + 2] >= 250:
                px[k + 3] = 0
    rows = [bytes(px[y * w * 4:(y + 1) * w * 4]) for y in range(h)]
    return png_bytes(w, h, rows, 6)


class _Files:
    """Finds the PICT files that type-5 objects name, in the disk image the stack came from."""

    def __init__(self, source_path, stack_mac_path):
        self.files = None
        self.source_path = source_path
        self.folder = stack_mac_path.rsplit(":", 1)[0] if stack_mac_path and ":" in stack_mac_path else ""

    def find(self, name):
        if self.files is None:
            self.files = []
            if self.source_path:
                try:
                    from . import macfiles
                    self.files = list(macfiles.open_path(self.source_path).files)
                except Exception:  # noqa: BLE001 - a missing picture must not stop the import
                    self.files = []
        low = name.lower()
        base = low.split(":")[-1]
        best = None
        for f in self.files:
            path = (getattr(f, "path", "") or "").lower()
            if path.split(":")[-1] != base:
                continue
            score = 2 if self.folder and path.startswith(self.folder.lower() + ":") else 1
            if path.endswith(low):
                score += 1
            if best is None or score > best[0]:
                best = (score, f)
        return best[1] if best else None


def build(rsrc_list, outdir, source_path=None, stack_mac_path=None):
    """Write addcolor.json (and the pictures) into the package. rsrc_list is
    resources.parse_resource_fork() output. Returns the table, or None if the
    stack has no color."""
    cards, bgs = {}, {}
    for rtype, rid, _name, data in rsrc_list:
        if rtype == "HCcd":
            cards[str(rid)] = parse(data)
        elif rtype == "HCbg":
            bgs[str(rid)] = parse(data)
    path = os.path.join(outdir, FILE)
    if not cards and not bgs:
        if os.path.exists(path):
            os.remove(path)
        return None
    picts = {}
    for rtype, rid, name, data in rsrc_list:
        if rtype == "PICT":
            picts.setdefault(name.lower(), data)
            picts.setdefault(str(rid), data)
    files = _Files(source_path, stack_mac_path)
    made = {}
    os.makedirs(os.path.join(outdir, DIR), exist_ok=True)
    for table in (cards, bgs):
        for items in table.values():
            for it in items:
                if it["type"] not in ("pict", "pictfile"):
                    continue
                key = (it["type"], it["name"].lower(), it["transparent"])
                if key not in made:
                    data = None
                    if it["type"] == "pict":
                        data = picts.get(it["name"].lower())
                    else:
                        f = files.find(it["name"])
                        raw = getattr(f, "data", None) if f else None
                        if raw and len(raw) > 522:
                            data = raw[512:]
                    png = _png(data, it["transparent"]) if data else None
                    fn = None
                    if png:
                        fn = os.path.join(DIR, "%d.png" % len(made))
                        with open(os.path.join(outdir, fn), "wb") as fh:
                            fh.write(png)
                    made[key] = fn
                it["file"] = made[key]
    table = {"cards": cards, "backgrounds": bgs, "files_resolved": bool(source_path)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(table, fh)
    return table


def load(stack):
    """The stack's color table (cached on the stack), or None."""
    t = getattr(stack, "_addcolor", False)
    if t is not False:
        return t
    t = None
    d = getattr(stack, "dir_path", "") or ""
    path = os.path.join(d, FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            t = json.load(fh)
    except (OSError, ValueError):
        # packages imported by older versions: build it from the saved resource fork
        try:
            with open(os.path.join(d, "_rsrc.bin"), "rb") as fh:
                from .resources import parse_resource_fork
                t = build(parse_resource_fork(fh.read()), d)
        except (OSError, ValueError):
            t = None
    stack._addcolor = t
    return t


def ensure(stack, source_path, mac_path=None):
    """Called when a stack is shown: find PICT files next to the stack if that wasn't done yet."""
    t = load(stack)
    if t is None or t.get("files_resolved") or not source_path:
        return
    if not any(it["type"] == "pictfile" for tbl in (t["cards"], t["backgrounds"]) for items in tbl.values()
               for it in items):
        return
    d = getattr(stack, "dir_path", "") or ""
    try:
        with open(os.path.join(d, "_rsrc.bin"), "rb") as fh:
            from .resources import parse_resource_fork
            stack._addcolor = build(parse_resource_fork(fh.read()), d, source_path, mac_path)
    except OSError:
        pass


def key(obj_id):
    """Resource id for a card or background id (resource ids are signed 16-bit)."""
    return str(((int(obj_id) + 32768) % 65536) - 32768)


def rgb(c):
    return (c[0] / 65535.0, c[1] / 65535.0, c[2] / 65535.0)


def parse_rgb(text):
    """ "65535,0,0" (or a color name) to [r, g, b] in 0..65535."""
    names = {"black": (0, 0, 0), "white": (65535,) * 3, "red": (65535, 0, 0), "green": (0, 65535, 0),
             "blue": (0, 0, 65535), "yellow": (65535, 65535, 0), "cyan": (0, 65535, 65535),
             "magenta": (65535, 0, 65535), "gray": (32767,) * 3, "grey": (32767,) * 3}
    s = str(text).strip().lower()
    if s in names:
        return list(names[s])
    try:
        v = [int(float(x)) for x in s.split(",")[:3]]
    except ValueError:
        return None
    if len(v) != 3:
        return None
    return [max(0, min(65535, x)) for x in v]


def picture_for_script(host, name, resource, transparent):
    """A PICT resource (or file) named by an AddColor command, as a PNG in the
    stack package. Returns (path, width, height) or None."""
    import hashlib
    data = None
    if resource:
        low = name.lower()
        num = int(name) if name.strip().lstrip("-").isdigit() else None
        for res in host.resource_chain():
            for r in res:
                if r[0] == "PICT" and ((num is not None and r[1] == num) or r[2].lower() == low):
                    data = r[3]
                    break
            if data:
                break
    if data is None:
        from .hthost import mac_to_local
        try:
            with open(mac_to_local(name), "rb") as fh:
                raw = fh.read()
            data = raw[512:] if len(raw) > 522 else None
        except OSError:
            data = None
    if not data:
        return None
    d = getattr(host.stack, "dir_path", "") or ""
    key = hashlib.sha1(data + (b"t" if transparent else b"o")).hexdigest()[:16]
    path = os.path.join(d, DIR, "s_%s.png" % key)
    if not os.path.exists(path):
        png = _png(data, transparent)
        if not png:
            return None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(png)
    w, h = struct.unpack(">II", open(path, "rb").read()[16:24])
    return path, w, h
