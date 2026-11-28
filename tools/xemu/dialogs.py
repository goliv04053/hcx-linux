"""The Dialog Manager, the Control Manager and the List Manager (Pack0) of
the xemu, without drawing: a modal dialog goes to HCX's own dialog
(host.ditl_dialog, the call the native externals make), described with the
same items, and what the user does there comes back into the original's
dialog, its list and its controls.

Part of HCX for Linux (GPL-3.0 or later). A development tool; HCX itself
never runs 68k code."""

import struct

from .mac68k import Unimplemented

_HIDE = 16384           # HideDItem moves an item this far to the right
_MAX_MODAL = 60         # ModalDialog calls in one run before giving up


def _from_mac(raw):
    return bytes(raw).decode("mac_roman", "replace").replace("\r", "\n")


def _to_mac(text):
    return str(text).replace("\n", "\r").encode("mac_roman", "replace")


def _rect(m, p):
    return [m.s16(p + 2 * i) for i in range(4)]        # top, left, bottom, right


def _wrect(m, p, r):
    for i, v in enumerate(r):
        m.w16(p + 2 * i, v & 0xFFFF)


def _cell(v):
    """A Point/Cell passed by value: (row, column)."""
    row, col = v >> 16, v & 0xFFFF
    return (row - 0x10000 if row & 0x8000 else row, col - 0x10000 if col & 0x8000 else col)


def _state(tb):
    if not hasattr(tb, "dialogs"):
        tb.dialogs = {}         # dialog pointer -> [item dicts]
        tb.lists = {}           # list handle -> List
        tb.param_text = [b"", b"", b"", b""]
        tb.modal_calls = 0
        tb.controls = {}        # window -> its control handles, oldest first
        tb.windows = []         # the windows the external opened, oldest first
        tb.drawn = {}           # port -> the texts drawn in it
        tb.events = []          # (what, message, global v, h, modifiers) waiting
    return tb


# -- controls ---------------------------------------------------------------------------------------

def _new_control(tb, owner, rect, title, visible, value, lo, hi, proc, refcon):
    m = tb.m
    h = tb.heap.new_handle(296, clear=True)
    c = tb.heap.deref(h)
    m.w32(c + 4, owner)
    _wrect(m, c + 8, rect)
    m.w8(c + 16, 255 if visible else 0)
    m.w16(c + 18, value)
    m.w16(c + 20, lo)
    m.w16(c + 22, hi)
    m.w32(c + 24, proc)
    m.w32(c + 36, refcon)
    m.wpstr(c + 40, title)
    if owner:                                   # the window's controlList
        m.w32(c, m.r32(owner + 140))
        m.w32(owner + 140, h)
        _state(tb).controls.setdefault(owner, []).append(h)
    return h


def _ctl(tb, h):
    return tb.heap.deref(h) if h and tb.heap.is_handle(h) else 0


# -- dialogs ----------------------------------------------------------------------------------------

def make_dialog(tb, dlg, items_h):
    """Builds the items of a new dialog from its item list (a DITL handle,
    which the dialog keeps), filling in the handles as the Mac does."""
    m, heap = tb.m, tb.heap
    _state(tb)
    items = []
    if items_h and heap.is_handle(items_h):
        base = heap.deref(items_h)
        d = bytes(m.rbytes(base, heap.size(items_h)))
        pos = 2
        for _i in range(struct.unpack(">h", d[:2])[0] + 1 if len(d) >= 2 else 0):
            if pos + 14 > len(d):
                break
            rect = list(struct.unpack(">4h", d[pos + 4:pos + 12]))
            typ, n = d[pos + 12], d[pos + 13]
            data = d[pos + 14:pos + 14 + n]
            k = typ & 0x7F
            if k & 4:                           # a control
                if k == 7:                      # from a CNTL resource
                    cid = struct.unpack(">h", data[:2])[0] if len(data) >= 2 else 0
                    h = _get_new_control(tb, cid, dlg)
                else:
                    h = _new_control(tb, dlg, rect, data, True, 0, 0, 1, k - 4, 0)
            elif k in (8, 16):
                h = heap.new_handle(len(data), data=data)
            elif k in (32, 64) and len(data) >= 2:
                rtype = "ICON" if k == 32 else "PICT"
                h = tb.get_resource(rtype, struct.unpack(">h", data[:2])[0]) or 0
            else:
                h = 0
            m.w32(base + pos, h)
            items.append({"type": typ, "rect": rect, "h": h, "at": base + pos})
            pos += 14 + n + (n & 1)
    tb.dialogs[dlg] = items
    return items


def _get_new_control(tb, cid, owner):
    f, r = tb.find_res("CNTL", cid)
    if r is None:
        return 0
    d = r[3]
    rect = list(struct.unpack(">4h", d[:8]))
    value, vis, hi, lo, proc = struct.unpack(">hBxhhh", d[8:18])
    refcon = struct.unpack(">i", d[18:22])[0]
    return _new_control(tb, owner, rect, d[23:23 + d[22]], vis, value, lo, hi, proc, refcon)


def _items(tb, dlg):
    _state(tb)
    if dlg not in tb.dialogs and dlg:
        make_dialog(tb, dlg, tb.m.r32(dlg + 156))
    return tb.dialogs.get(dlg, [])


def _hdata(tb, h):
    return bytes(tb.m.rbytes(tb.heap.deref(h), tb.heap.size(h))) if h and tb.heap.is_handle(h) else b""


def _substitute(tb, text):
    for i in range(4):
        text = text.replace(b"^%d" % i, tb.param_text[i])
    return text


def _list_in(tb, rect):
    """The List Manager list drawn inside an item's rectangle, if any."""
    t, l, b, r = rect
    for lh, lst in tb.lists.items():
        vt, vl, vb, vr = lst.view(tb)
        if vt >= t - 2 and vl >= l - 2 and vb <= b + 2 and vr <= r + 18:
            return lh, lst
    return None, None


def dialog_spec(tb, dlg, items):
    """The dialog as host.ditl_dialog takes it, and the list handles of its
    list items."""
    m = tb.m
    spec_items, lists = [], {}
    used = set()
    for n, it in enumerate(items, 1):
        t, l, b, r = it["rect"]
        if l >= _HIDE // 2:
            continue                            # hidden
        k, rect = it["type"] & 0x7F, (l, t, r, b)
        enabled = not it["type"] & 0x80
        if k & 4:
            c = _ctl(tb, it["h"])
            if not c or not m.r8(c + 16):
                continue
            title = _from_mac(m.pstr(c + 40))
            enabled = enabled and m.r8(c + 17) != 255
            kind = ("button", "check", "radio", "button")[k & 3]
            if kind == "button" and b - t < 4:
                spec_items.append({"n": n, "kind": "line", "rect": rect, "enabled": False})
                continue
            e = {"n": n, "kind": kind, "text": title, "enabled": enabled, "rect": rect}
            if kind != "button":
                e["checked"] = m.s16(c + 18) != 0
            spec_items.append(e)
        elif k in (8, 16):
            text = _hdata(tb, it["h"])
            if k == 8:
                text = _substitute(tb, text)
            spec_items.append({"n": n, "kind": "static" if k == 8 else "edit",
                               "text": _from_mac(text), "enabled": enabled, "rect": rect})
        elif k == 0:
            lh, lst = _list_in(tb, it["rect"])
            if lst is not None and lh not in used:
                used.add(lh)
                lists[n] = lh
                spec_items.append({"n": n, "kind": "list", "lines": lst.lines(), "multiple": lst.multiple(tb),
                                   "selected": lst.selected_rows(), "enabled": True, "rect": rect})
    for lh, lst in tb.lists.items():            # a list outside every user item
        if lh not in used and lst.window == dlg:
            n = 1000 + len(lists)
            lists[n] = lh
            vt, vl, vb, vr = lst.view(tb)
            spec_items.append({"n": n, "kind": "list", "lines": lst.lines(), "multiple": lst.multiple(tb),
                               "selected": lst.selected_rows(), "enabled": True, "rect": (vl, vt, vr, vb)})
    pr = _rect(m, dlg + 16) if dlg else [0, 0, 200, 300]
    spec = {"title": _from_mac(m.pstr(m.r32(m.r32(dlg + 134)))) if dlg and m.r32(dlg + 134) else "",
            "proc": 1, "width": pr[3] - pr[1], "height": pr[2] - pr[0], "default": 1, "cancel": None,
            "items": spec_items}
    return spec, lists


def run_dialog(tb, dlg, items):
    """Shows the dialog through the host and puts what the user did back
    in it. Returns the number of the item that closed it."""
    m = tb.m
    tb.modal_calls += 1
    if tb.modal_calls > _MAX_MODAL:
        raise Unimplemented("ModalDialog: the dialog never closes")
    spec, lists = dialog_spec(tb, dlg, items)
    n, values = tb.it.host.ditl_dialog(spec)
    for e in spec["items"]:
        i = e["n"]
        if i not in values:
            continue
        v = values[i]
        if e["kind"] == "list":
            tb.lists[lists[i]].select_rows(v)
        elif e["kind"] == "edit":
            h = items[i - 1]["h"]
            data = _to_mac(v)
            tb.heap.set_size(h, len(data))
            m.wbytes(tb.heap.deref(h), data)
        elif e["kind"] in ("check", "radio"):
            c = _ctl(tb, items[i - 1]["h"])
            if c:
                m.w16(c + 18, 1 if v else 0)
    return n if n is not None else 1


# -- windows with their own event loop -------------------------------------------------------------

def window_spec(tb, w):
    """A window the external runs itself (controls, lists and the texts it
    drew), as host.ditl_dialog takes it. Item n is the n-th control; the
    texts and lists come after."""
    m = tb.m
    items, n = [], 0
    for h in tb.controls.get(w, []):
        n += 1
        c = _ctl(tb, h)
        if not c or not m.r8(c + 16):
            continue
        t, l, b, r = _rect(m, c + 8)
        kind = ("button", "check", "radio", "button")[m.r16(c + 24 + 2) & 3]
        e = {"n": n, "kind": kind, "text": _from_mac(m.pstr(c + 40)), "enabled": m.r8(c + 17) != 255,
             "rect": (l, t, r, b)}
        if kind != "button":
            e["checked"] = m.s16(c + 18) != 0
        items.append(e)
    for text, rect in tb.drawn.get(w, []):
        n += 1
        if text is None:
            items.append({"n": n, "kind": "line", "enabled": False, "rect": rect})
        else:
            items.append({"n": n, "kind": "static", "text": _from_mac(text), "enabled": False, "rect": rect})
    lists = {}
    for lh, lst in tb.lists.items():
        if lst.window == w:
            n += 1
            lists[n] = lh
            vt, vl, vb, vr = lst.view(tb)
            items.append({"n": n, "kind": "list", "lines": lst.lines(), "multiple": lst.multiple(tb),
                          "selected": lst.selected_rows(), "enabled": True, "rect": (vl, vt, vr, vb)})
    pr = _rect(m, w + 16)
    spec = {"title": "", "proc": 1, "width": pr[3] - pr[1], "height": pr[2] - pr[0], "default": 1,
            "cancel": None, "items": items}
    return spec, lists


def run_window(tb, w):
    """Shows a window with controls through the host, puts the lists'
    selection and the boxes back, and queues a click on the button chosen."""
    m = tb.m
    tb.modal_calls += 1
    if tb.modal_calls > _MAX_MODAL:
        raise Unimplemented("event loop: the window never closes")
    spec, lists = window_spec(tb, w)
    n, values = tb.it.host.ditl_dialog(spec)
    for e in spec["items"]:
        i = e["n"]
        if i in values and e["kind"] == "list":
            tb.lists[lists[i]].select_rows(values[i])
            tb.lists[lists[i]].sync(tb)
        elif i in values and e["kind"] in ("check", "radio"):
            c = _ctl(tb, tb.controls[w][i - 1])
            if c and bool(values[i]) != (m.s16(c + 18) != 0):
                _click(tb, w, c)            # the external toggles it itself
    ctls = tb.controls.get(w, [])
    if n is not None and 1 <= n <= len(ctls) and _ctl(tb, ctls[n - 1]):
        _click(tb, w, _ctl(tb, ctls[n - 1]))


def _click(tb, w, c):
    m = tb.m
    t, l, b, r = _rect(m, c + 8)
    v, h = (t + b) // 2 - m.s16(w + 8), (l + r) // 2 - m.s16(w + 10)        # global
    tb.events.append((1, 0, v, h, 0))       # mouseDown
    tb.events.append((2, 0, v, h, 0))       # mouseUp


def _front_window(tb):
    """The newest visible window of the external that has controls."""
    m = tb.m
    for w in reversed(tb.windows):
        if w not in tb.dialogs and m.r8(w + 110) and tb.controls.get(w):
            return w
    return 0


def _window_at(tb, v, h):
    m = tb.m
    for w in reversed(tb.windows):
        if not m.r8(w + 110):
            continue
        t, l, b, r = _rect(m, w + 16)
        gv, gh = v + m.s16(w + 8), h + m.s16(w + 10)        # local
        if t <= gv < b and l <= gh < r:
            return w
    return 0


def _control_at(tb, w, v, h):
    """The control under a local point and its part code."""
    m = tb.m
    for hc in reversed(tb.controls.get(w, [])):
        c = _ctl(tb, hc)
        if not c or not m.r8(c + 16) or m.r8(c + 17) == 255:
            continue
        t, l, b, r = _rect(m, c + 8)
        if t <= v < b and l <= h < r:
            return hc, (10, 11, 11, 10)[m.r16(c + 26) & 3]
    return 0, 0


# -- lists ------------------------------------------------------------------------------------------

class List:
    def __init__(self, tb, rview, bounds, csize, window):
        self.rows, self.cols = max(0, bounds[2] - bounds[0]), max(0, bounds[3] - bounds[1])
        self.cells = {}                 # (row, col) -> bytes
        self.sel = set()
        self.last = (-1, -1)
        self.window = window
        m = tb.m
        self.h = tb.heap.new_handle(96, clear=True)
        p = tb.heap.deref(self.h)
        _wrect(m, p, rview)
        m.w32(p + 8, window)
        ch = csize & 0xFFFF or max(1, (rview[3] - rview[1]) // max(1, self.cols))
        cv = csize >> 16 or 16
        m.w16(p + 16, cv)
        m.w16(p + 18, ch)
        m.w32(p + 80, tb.heap.new_handle(0))
        self.sync(tb)

    def view(self, tb):
        return _rect(tb.m, tb.heap.deref(self.h))

    def multiple(self, tb):
        return not tb.m.r8(tb.heap.deref(self.h) + 36) & 0x80        # lOnlyOne

    def sync(self, tb):
        """dataBounds, the cells handle and its offsets, as code reading
        the ListRec finds them."""
        m = tb.m
        p = tb.heap.deref(self.h)
        _wrect(m, p + 72, [0, 0, self.rows, self.cols])
        data, offs = b"", []
        for r in range(self.rows):
            for c in range(self.cols):
                offs.append(len(data) | (0x8000 if (r, c) in self.sel else 0))
                data += self.cells.get((r, c), b"")
        offs.append(len(data))
        need = 86 + 2 * len(offs)
        if tb.heap.size(self.h) < need:
            tb.heap.set_size(self.h, need)
            p = tb.heap.deref(self.h)
        m.w16(p + 84, 2 * (len(offs) - 1))                          # maxIndex
        for i, o in enumerate(offs):
            m.w16(p + 86 + 2 * i, o)
        ch = m.r32(p + 80)
        if tb.heap.is_handle(ch):
            tb.heap.set_size(ch, len(data))
            m.wbytes(tb.heap.deref(ch), data)

    def lines(self):
        """The first column's text. A return or a zero byte at the end of a
        cell is not shown (the standard list definition draws it as
        nothing), so it is left out."""
        return [_from_mac(self.cells.get((r, 0), b"").rstrip(b"\r\0")) for r in range(self.rows)]

    def selected_rows(self):
        return sorted({r + 1 for r, c in self.sel})

    def select_rows(self, rows):
        self.sel = {(r - 1, 0) for r in rows if 1 <= r <= self.rows}

    def ok(self, cell):
        return 0 <= cell[0] < self.rows and 0 <= cell[1] < self.cols

    def order(self):
        return [(r, c) for r in range(self.rows) for c in range(self.cols)]


def _pack0(tb, op):
    m = tb.m
    _state(tb)
    sel = m.pop16()

    def lst():
        h = m.pop32()
        return tb.lists.get(h) or _missing(h)

    def _missing(h):
        raise Unimplemented("List Manager: %08X is not a list" % h)

    if sel == 0x44:                                         # LNew
        m.pop16(), m.pop16(), m.pop16(), m.pop16()          # scrollVert, scrollHoriz, hasGrow, drawIt
        win, _proc, csize = m.pop32(), m.pop16(), m.pop32()
        bounds, rview = _rect(m, m.pop32()), _rect(m, m.pop32())
        L = List(tb, rview, bounds, csize, win)
        tb.lists[L.h] = L
        return tb.ret32(L.h)
    if sel in (0x00, 0x2C):                                 # LActivate, LDoDraw
        lst(), m.pop16()
        return
    if sel in (0x10, 0x28):                                 # LAutoScroll, LDispose
        h = m.pop32()
        if sel == 0x28:
            tb.lists.pop(h, None)
        return
    if sel in (0x04, 0x08):                                 # LAddColumn, LAddRow
        L = lst()
        at, count = m.pops16(), m.pops16()
        if sel == 0x08:
            at = max(0, min(at, L.rows))
            L.cells = {((r + count if r >= at else r), c): v for (r, c), v in L.cells.items()}
            L.sel = {((r + count if r >= at else r), c) for r, c in L.sel}
            L.rows += count
        else:
            at = max(0, min(at, L.cols))
            L.cells = {(r, (c + count if c >= at else c)): v for (r, c), v in L.cells.items()}
            L.sel = {(r, (c + count if c >= at else c)) for r, c in L.sel}
            L.cols += count
        L.sync(tb)
        return tb.ret16(at)
    if sel in (0x20, 0x24):                                 # LDelColumn, LDelRow
        L = lst()
        at, count = m.pops16(), m.pops16()
        if sel == 0x24:
            if count == 0:
                at, count = 0, L.rows
            gone = range(at, at + count)
            L.cells = {((r - count if r >= at + count else r), c): v for (r, c), v in L.cells.items() if r not in gone}
            L.sel = {((r - count if r >= at + count else r), c) for r, c in L.sel if r not in gone}
            L.rows = max(0, L.rows - count)
        else:
            if count == 0:
                at, count = 0, L.cols
            gone = range(at, at + count)
            L.cells = {(r, (c - count if c >= at + count else c)): v for (r, c), v in L.cells.items() if c not in gone}
            L.sel = {(r, (c - count if c >= at + count else c)) for r, c in L.sel if c not in gone}
            L.cols = max(0, L.cols - count)
        L.sync(tb)
        return
    if sel in (0x0C, 0x58):                                 # LAddToCell, LSetCell
        L = lst()
        cell, n, p = _cell(m.pop32()), m.pops16(), m.pop32()
        if L.ok(cell):
            data = bytes(m.rbytes(p, max(0, n))) if p else b""
            L.cells[cell] = (L.cells.get(cell, b"") if sel == 0x0C else b"") + data
            L.sync(tb)
        return
    if sel == 0x1C:                                         # LClrCell
        L = lst()
        L.cells.pop(_cell(m.pop32()), None)
        L.sync(tb)
        return
    if sel in (0x14, 0x30):                                 # LCellSize, LDraw
        lst(), m.pop32()
        return
    if sel == 0x18:                                         # LClick
        lst(), m.pop16(), m.pop32()
        return tb.ret_bool(False)
    if sel == 0x34:                                         # LFind
        L = lst()
        cell, lenp, offp = _cell(m.pop32()), m.pop32(), m.pop32()
        if not L.ok(cell):
            m.w16(offp, 0xFFFF)
            m.w16(lenp, 0xFFFF)
            return
        off = sum(len(L.cells.get(c, b"")) for c in L.order()[:L.order().index(cell)])
        m.w16(offp, off)
        m.w16(lenp, len(L.cells.get(cell, b"")))
        return
    if sel == 0x38:                                         # LGetCell
        L = lst()
        cell, lenp, p = _cell(m.pop32()), m.pop32(), m.pop32()
        data = L.cells.get(cell, b"") if L.ok(cell) else b""
        data = data[:max(0, m.s16(lenp))]
        m.wbytes(p, data)
        m.w16(lenp, len(data))
        return
    if sel == 0x3C:                                         # LGetSelect
        L = lst()
        cellp, nxt = m.pop32(), m.pop16()
        cell = (m.s16(cellp), m.s16(cellp + 2))
        if not nxt:
            return tb.ret_bool(cell in L.sel)
        for c in L.order():
            if c >= cell and c in L.sel:
                m.w16(cellp, c[0])
                m.w16(cellp + 2, c[1])
                return tb.ret_bool(True)
        return tb.ret_bool(False)
    if sel == 0x40:                                         # LLastClick
        L = lst()
        return tb.ret32(((L.last[0] & 0xFFFF) << 16) | (L.last[1] & 0xFFFF))
    if sel == 0x48:                                         # LNextCell
        L = lst()
        cellp, vnext, hnext = m.pop32(), m.pop16(), m.pop16()
        r, c = m.s16(cellp), m.s16(cellp + 2)
        if hnext and c + 1 < L.cols:
            c += 1
        elif vnext and r + 1 < L.rows:
            r, c = r + 1, 0 if hnext else c
        else:
            return tb.ret_bool(False)
        m.w16(cellp, r)
        m.w16(cellp + 2, c)
        return tb.ret_bool(True)
    if sel == 0x4C:                                         # LRect
        L = lst()
        cell, rp = _cell(m.pop32()), m.pop32()
        p = tb.heap.deref(L.h)
        cv, ch = m.s16(p + 16), m.s16(p + 18)
        t, l = m.s16(p), m.s16(p + 2)
        _wrect(m, rp, [t + cell[0] * cv, l + cell[1] * ch, t + (cell[0] + 1) * cv, l + (cell[1] + 1) * ch]
               if L.ok(cell) else [0, 0, 0, 0])
        return
    if sel in (0x50, 0x60):                                 # LScroll, LSize
        lst(), m.pop16(), m.pop16()
        return
    if sel == 0x54:                                         # LSearch
        L = lst()
        cellp, proc, n, p = m.pop32(), m.pop32(), m.pops16(), m.pop32()
        if proc:
            raise Unimplemented("LSearch with a search procedure")
        want = bytes(m.rbytes(p, n)).lower()
        start = (m.s16(cellp), m.s16(cellp + 2))
        for c in L.order():
            if c >= start and L.cells.get(c, b"").lower() == want:
                m.w16(cellp, c[0])
                m.w16(cellp + 2, c[1])
                return tb.ret_bool(True)
        return tb.ret_bool(False)
    if sel == 0x5C:                                         # LSetSelect
        L = lst()
        cell, on = _cell(m.pop32()), m.pop16()
        if L.ok(cell):
            (L.sel.add if on else L.sel.discard)(cell)
            L.sync(tb)
        return
    if sel == 0x64:                                         # LUpdate
        lst(), m.pop32()
        return
    raise Unimplemented("Pack0 selector %02X" % sel)


# -- traps ------------------------------------------------------------------------------------------

def install(tb_trap, os_trap, tb_table, os_table):
    def trap(*nums):
        def deco(f):
            tb_trap(*nums)(f)
            return f
        return deco

    trap(0xA9E7)(_pack0)

    @trap(0xA97D, 0xAA4B)
    def _new_dialog(tb, op):
        """NewDialog and NewColorDialog."""
        from .toolbox import _new_window_record, _pop_window_args
        items = tb.m.pop32()
        storage, bounds, visible, refcon = _pop_window_args(tb)
        w = _new_window_record(tb, storage, bounds, visible, refcon, 2, items)
        make_dialog(tb, w, items)
        tb.ret32(w)

    @trap(0xA97C)
    def _get_new_dialog(tb, op):
        from .toolbox import _new_window_record
        m = tb.m
        m.pop32()                   # behind
        storage = m.pop32()
        rid = m.pops16()
        f, r = tb.find_res("DLOG", rid)
        if r is None:
            from .mac68k import resNotFound
            tb.res_err = resNotFound
            return tb.ret32(0)
        d = r[3]
        bounds = list(struct.unpack(">4h", d[:8]))
        items = tb.get_resource("DITL", struct.unpack(">h", d[18:20])[0])
        if items:
            data = _hdata(tb, items)
            items = tb.heap.new_handle(len(data), data=data)
        w = _new_window_record(tb, storage, bounds, d[10], struct.unpack(">i", d[14:18])[0], 2, items)
        make_dialog(tb, w, items)
        tb.ret32(w)

    @trap(0xA98D)
    def _get_ditem(tb, op):
        m = tb.m
        box, item, typ = m.pop32(), m.pop32(), m.pop32()
        n = m.pops16()
        items = _items(tb, m.pop32())
        if 1 <= n <= len(items):
            e = items[n - 1]
            m.w16(typ, e["type"])
            m.w32(item, e["h"])
            _wrect(m, box, e["rect"])
        else:
            m.w16(typ, 0)
            m.w32(item, 0)
            _wrect(m, box, [0, 0, 0, 0])

    @trap(0xA98E)
    def _set_ditem(tb, op):
        m = tb.m
        box, h, typ = m.pop32(), m.pop32(), m.pops16()
        n = m.pops16()
        items = _items(tb, m.pop32())
        if 1 <= n <= len(items):
            items[n - 1].update(type=typ & 0xFF, h=h, rect=_rect(m, box))
            m.w32(items[n - 1]["at"], h)

    @trap(0xA827, 0xA828)
    def _hide_show_ditem(tb, op):
        """HideDItem and ShowDItem."""
        m = tb.m
        n = m.pops16()
        items = _items(tb, m.pop32())
        if 1 <= n <= len(items):
            e = items[n - 1]
            hidden = e["rect"][1] >= _HIDE // 2
            if (op == 0xA827) != hidden:
                d = _HIDE if op == 0xA827 else -_HIDE
                e["rect"][1] += d
                e["rect"][3] += d
                c = _ctl(tb, e["h"]) if e["type"] & 4 else 0
                if c:
                    m.w8(c + 16, 0 if op == 0xA827 else 255)

    @trap(0xA98F)
    def _set_itext(tb, op):
        m = tb.m
        s, h = m.pop32(), m.pop32()
        if h and tb.heap.is_handle(h):
            data = m.pstr(s)
            tb.heap.set_size(h, len(data))
            m.wbytes(tb.heap.deref(h), data)

    @trap(0xA990)
    def _get_itext(tb, op):
        m = tb.m
        s, h = m.pop32(), m.pop32()
        m.wpstr(s, _hdata(tb, h)[:255])

    @trap(0xA97E)
    def _sel_itext(tb, op):
        m = tb.m
        m.pop16(), m.pop16(), m.pop16(), m.pop32()

    @trap(0xA98B)
    def _param_text(tb, op):
        m = tb.m
        _state(tb)
        ps = [m.pop32() for _i in range(4)][::-1]
        tb.param_text = [m.pstr(p) if p else b"" for p in ps]

    @trap(0xA991)
    def _modal_dialog(tb, op):
        m = tb.m
        hit, _filter = m.pop32(), m.pop32()
        dlg = _front_dialog(tb)
        m.w16(hit, run_dialog(tb, dlg, _items(tb, dlg)))

    def _front_dialog(tb):
        _state(tb)
        if not tb.dialogs:
            raise Unimplemented("ModalDialog without a dialog")
        return list(tb.dialogs)[-1]

    @trap(0xA985, 0xA986, 0xA987, 0xA988)
    def _alert(tb, op):
        """Alert, StopAlert, NoteAlert, CautionAlert: the alert's items in
        the host's dialog."""
        m = tb.m
        m.pop32()                   # filterProc
        rid = m.pops16()
        f, r = tb.find_res("ALRT", rid)
        if r is None:
            return tb.ret16(-1)
        d = r[3]
        bounds = list(struct.unpack(">4h", d[:8]))
        items = tb.get_resource("DITL", struct.unpack(">h", d[8:10])[0])
        data = _hdata(tb, items)
        from .toolbox import _new_window_record
        w = _new_window_record(tb, 0, bounds, 1, 0, 2, tb.heap.new_handle(len(data), data=data))
        n = run_dialog(tb, w, make_dialog(tb, w, tb.m.r32(w + 156)))
        tb.dialogs.pop(w, None)
        tb.ret16(n)

    @trap(0xA982, 0xA983)
    def _dispose_dialog(tb, op):
        """CloseDialog and DisposDialog."""
        _state(tb)
        w = tb.m.pop32()
        tb.dialogs.pop(w, None)

    @trap(0xA97F)
    def _is_dialog_event(tb, op):
        tb.m.pop32()
        tb.ret_bool(False)

    @trap(0xA980)
    def _dialog_select(tb, op):
        m = tb.m
        m.pop32(), m.pop32(), m.pop32()
        tb.ret_bool(False)

    @trap(0xA984)
    def _find_ditem(tb, op):
        m = tb.m
        m.pop32(), m.pop32()
        tb.ret16(-1)

    # Controls
    @trap(0xA954)
    def _new_control_trap(tb, op):
        m = tb.m
        refcon, proc, hi, lo, value = m.pop32(), m.pops16(), m.pops16(), m.pops16(), m.pops16()
        visible, title, rect, w = m.pop16(), m.pop32(), m.pop32(), m.pop32()
        tb.ret32(_new_control(tb, w, _rect(m, rect), m.pstr(title), visible, value, lo, hi, proc, refcon))

    @trap(0xA9BE)
    def _get_new_control_trap(tb, op):
        m = tb.m
        w, cid = m.pop32(), m.pops16()
        tb.ret32(_get_new_control(tb, cid, w))

    @trap(0xA95E)
    def _get_ctitle(tb, op):
        m = tb.m
        s, h = m.pop32(), m.pop32()
        c = _ctl(tb, h)
        m.wpstr(s, m.pstr(c + 40) if c else b"")

    @trap(0xA95F)
    def _set_ctitle(tb, op):
        m = tb.m
        s, h = m.pop32(), m.pop32()
        c = _ctl(tb, h)
        if c:
            m.wpstr(c + 40, m.pstr(s))

    @trap(0xA95D)
    def _hilite_control(tb, op):
        m = tb.m
        v, h = m.pop16(), m.pop32()
        c = _ctl(tb, h)
        if c:
            m.w8(c + 17, v & 0xFF)

    @trap(0xA960, 0xA961, 0xA962)
    def _get_ctl(tb, op):
        """GetCtlValue, GetCtlMin, GetCtlMax."""
        c = _ctl(tb, tb.m.pop32())
        tb.ret16(tb.m.r16(c + 18 + 2 * (op - 0xA960)) if c else 0)

    @trap(0xA963, 0xA964, 0xA965)
    def _set_ctl(tb, op):
        """SetCtlValue, SetCtlMin, SetCtlMax."""
        m = tb.m
        v, c = m.pop16(), _ctl(tb, m.pop32())
        if c:
            m.w16(c + 18 + 2 * (op - 0xA963), v)

    @trap(0xA95A)
    def _get_crefcon(tb, op):
        c = _ctl(tb, tb.m.pop32())
        tb.ret32(tb.m.r32(c + 36) if c else 0)

    @trap(0xA95B)
    def _set_crefcon(tb, op):
        m = tb.m
        v, c = m.pop32(), _ctl(tb, m.pop32())
        if c:
            m.w32(c + 36, v)

    @trap(0xA957, 0xA958)
    def _show_hide_control(tb, op):
        c = _ctl(tb, tb.m.pop32())
        if c:
            tb.m.w8(c + 16, 255 if op == 0xA957 else 0)

    @trap(0xA959, 0xA95C)
    def _move_size_control(tb, op):
        m = tb.m
        b, a, c = m.pops16(), m.pops16(), _ctl(tb, m.pop32())
        if c:
            r = _rect(m, c + 8)
            if op == 0xA959:        # MoveControl(h, v)
                r = [b, a, b + r[2] - r[0], a + r[3] - r[1]]
            else:                   # SizeControl(w, h)
                r = [r[0], r[1], r[0] + b, r[1] + a]
            _wrect(m, c + 8, r)

    @trap(0xA955)
    def _dispose_control(tb, op):
        tb.m.pop32()

    @trap(0xA956, 0xA969)
    def _kill_draw_controls(tb, op):
        tb.m.pop32()

    @trap(0xA96D)
    def _draw1_control(tb, op):
        tb.m.pop32()

    @trap(0xA966)
    def _test_control(tb, op):
        m = tb.m
        m.pop32(), m.pop32()
        tb.ret16(0)

    @trap(0xA96B)
    def _set_ctl_action(tb, op):
        m = tb.m
        m.pop32(), m.pop32()

    # Drawing and windows: nothing is drawn. Trap -> bytes of arguments.
    quiet = {0xA89B: 4, 0xA89C: 2, 0xA89D: 4, 0xA89E: 0, 0xA8A1: 4, 0xA8A2: 4, 0xA8A3: 4, 0xA8A4: 4, 0xA8A5: 8,
             0xA8B0: 8, 0xA8B1: 8, 0xA8B2: 8, 0xA8B3: 8, 0xA8B7: 4, 0xA8B8: 4, 0xA8B9: 4, 0xA8BA: 4,
             0xA893: 4, 0xA894: 4, 0xA891: 4, 0xA892: 4, 0xA883: 2, 0xA884: 4, 0xA885: 8,
             0xA8D9: 4, 0xA8DF: 8, 0xA8DE: 12, 0xA8D2: 4, 0xA8D3: 4, 0xA8D4: 4, 0xA8D5: 4, 0xA8D6: 8,
             0xA87B: 4, 0xA879: 4, 0xA862: 4, 0xA863: 4, 0xAA14: 4, 0xAA15: 4,
             0xA928: 4, 0xA92A: 4, 0xA927: 4, 0xA929: 4, 0xA922: 4, 0xA923: 4, 0xA915: 4, 0xA916: 4,
             0xA91F: 4, 0xA904: 4, 0xA91A: 8, 0xA920: 4, 0xA851: 4, 0xA850: 0, 0xA852: 0,
             0xA853: 0, 0xA856: 0, 0xA981: 4, 0xA978: 8, 0xA97B: 4, 0xA979: 2, 0xA97A: 2, 0xA98C: 4,
             0xA8EF: 12, 0xA91B: 10}

    for num, size in quiet.items():
        if num & 0x3FF not in tb_table:
            tb_trap(num)(_skip(size))

    from . import toolbox as _tbx

    @trap(0xA913, 0xAA45)
    def _new_window(tb, op):
        """NewWindow and NewCWindow; a visible window gets an update event."""
        storage, bounds, visible, refcon = _tbx._pop_window_args(tb)
        w = _tbx._new_window_record(tb, storage, bounds, visible, refcon, 8)
        _state(tb).windows.append(w)
        if visible:
            tb.events.append((6, w, 0, 0, 0))
        tb.ret32(w)

    @trap(0xA915)
    def _show_window(tb, op):
        w = tb.m.pop32()
        if w and not tb.m.r8(w + 110):
            tb.m.w8(w + 110, 1)
            _state(tb).events.append((6, w, 0, 0, 0))

    @trap(0xA916)
    def _hide_window(tb, op):
        w = tb.m.pop32()
        if w:
            tb.m.w8(w + 110, 0)

    @trap(0xA914, 0xA92D)
    def _dispose_window(tb, op):
        """DisposeWindow and CloseWindow."""
        _state(tb)
        w = tb.m.pop32()
        tb.dialogs.pop(w, None)
        tb.controls.pop(w, None)
        if w in tb.windows:
            tb.windows.remove(w)
        tb.events = [e for e in tb.events if e[1] != w]

    @trap(0xA971, 0xA970, 0xA860)
    def _get_event(tb, op):
        """EventAvail, GetNextEvent and WaitNextEvent: the queued events;
        when there are none and a window of the external with controls is
        up, it is shown through the host and the user's click is queued."""
        m = tb.m
        _state(tb)
        if op & 0x3FF == 0x60:
            m.pop32(), m.pop32()
        p = m.pop32()
        mask = m.pop16()
        ev = next((e for e in tb.events if mask & (1 << e[0])), None)
        if ev is None and op & 0x3FF != 0x71 and _front_window(tb):
            run_window(tb, _front_window(tb))
            ev = next((e for e in tb.events if mask & (1 << e[0])), None)
        m.wbytes(p, bytes(16))
        m.w32(p + 6, tb.ticks())
        if ev is None:
            return tb.ret_bool(False)
        if op & 0x3FF != 0x71:
            tb.events.remove(ev)
        what, msg, v, h, mods = ev
        m.w16(p, what)
        m.w32(p + 2, msg)
        m.w16(p + 10, v)
        m.w16(p + 12, h)
        m.w16(p + 14, mods)
        tb.ret_bool(True)

    @trap(0xA92C)
    def _find_window(tb, op):
        m = tb.m
        _state(tb)
        wp, pt = m.pop32(), m.pop32()
        v, h = (pt >> 16) - (0x10000 if pt & 0x80000000 else 0), pt & 0xFFFF
        h = h - 0x10000 if h & 0x8000 else h
        w = _window_at(tb, v, h)
        m.w32(wp, w)
        tb.ret16(3 if w else 0)             # inContent, inDesk

    @trap(0xA96C)
    def _find_control(tb, op):
        m = tb.m
        _state(tb)
        hp, w, pt = m.pop32(), m.pop32(), m.pop32()
        v, h = _cell(pt)
        hc, part = _control_at(tb, w, v, h)
        m.w32(hp, hc)
        tb.ret16(part)

    @trap(0xA968)
    def _track_control(tb, op):
        m = tb.m
        _state(tb)
        m.pop32()
        pt, hc = m.pop32(), m.pop32()
        c = _ctl(tb, hc)
        if not c:
            return tb.ret16(0)
        w = m.r32(c + 4)
        v, h = _cell(pt)
        got, part = _control_at(tb, w, v, h)
        tb.ret16(part if got == hc else 0)

    # What the external draws in its windows: texts, and horizontal lines
    # (a separator drawn with MoveTo and LineTo), as (text or None, rect).
    def _drew(tb, text, rect):
        _state(tb)
        if text or text is None:
            tb.drawn.setdefault(m_port(tb), []).append((text, rect))

    def m_port(tb):
        return tb.m.r32(tb.m.r32(tb.m.a(5)))

    def _pen(tb):
        return tb.m.s16(m_port(tb) + 48), tb.m.s16(m_port(tb) + 50)       # pnLoc v, h

    def _set_pen(tb, v, h):
        tb.m.w16(m_port(tb) + 48, v & 0xFFFF)
        tb.m.w16(m_port(tb) + 50, h & 0xFFFF)

    @trap(0xA893, 0xA894, 0xA891, 0xA892)
    def _move_line(tb, op):
        """MoveTo, Move, LineTo, Line."""
        m = tb.m
        _state(tb)
        dv, dh = m.pops16(), m.pops16()
        v0, h0 = _pen(tb)
        v, h = (dv, dh) if op in (0xA893, 0xA891) else (v0 + dv, h0 + dh)
        if op in (0xA891, 0xA892) and v == v0 and abs(h - h0) > 8 and m_port(tb) in tb.windows:
            _drew(tb, None, (min(h, h0), v, max(h, h0), v + 1))
        _set_pen(tb, v, h)

    @trap(0xA884)
    def _draw_string(tb, op):
        v, h = _pen(tb)
        _drew(tb, tb.m.pstr(tb.m.pop32()), (h, v - 12, h, v))

    @trap(0xA885)
    def _draw_text(tb, op):
        m = tb.m
        n, first, p = m.pops16(), m.pops16(), m.pop32()
        v, h = _pen(tb)
        _drew(tb, bytes(m.rbytes(p + first, max(0, n))), (h, v - 12, h, v))

    @trap(0xA9CE)
    def _text_box(tb, op):
        """TETextBox(text, length, box, just)."""
        m = tb.m
        m.pop16()
        t, l, b, r = _rect(m, m.pop32())
        n, p = m.pops32(), m.pop32()
        _drew(tb, bytes(m.rbytes(p, max(0, n))), (l, t, r, b))

    if 0xA8DA & 0x3FF not in tb_table:
        tb_trap(0xA8DA)(_skip(0))                       # OpenRgn
        tb_trap(0xA8DB)(_skip(4))                       # CloseRgn

    @trap(0xA898)
    def _get_pen_state(tb, op):
        p = tb.m.pop32()
        tb.m.wbytes(p, bytes(4) + (1).to_bytes(2, "big") * 2 + (8).to_bytes(2, "big") + b"\xff" * 8)

    if 0xA899 & 0x3FF not in tb_table:
        tb_trap(0xA899)(_skip(4))                       # SetPenState

    @trap(0xA8D8)
    def _new_rgn(tb, op):
        h = tb.heap.new_handle(10, clear=True)
        tb.m.w16(tb.heap.deref(h), 10)
        tb.ret32(h)

    @trap(0xA87A)
    def _get_clip(tb, op):
        tb.m.pop32()

    if 0x32 not in os_table:
        os_trap(0xA032)(lambda tb, op: None)            # FlushEvents: there are none


def _skip(size):
    def f(tb, op):
        tb.m.sp = tb.m.sp + size
    return f
