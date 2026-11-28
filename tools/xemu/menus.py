"""The Menu Manager of the xemu: menus are real MenuInfo records in the
emulated memory (code reads them directly), and PopUpMenuSelect shows the
menu through HCX's own pop-up menu (host.popup_menu, the call the native
externals make). Nothing is drawn; there is no menu bar.

Part of HCX for Linux (GPL-3.0 or later). A development tool; HCX itself
never runs 68k code."""

import struct

from .mac68k import Unimplemented

_MAX_POPUPS = 60


def _from_mac(raw):
    return bytes(raw).decode("mac_roman", "replace").replace("\r", "\n")


def _state(tb):
    if not hasattr(tb, "menus"):
        tb.menus = {}           # menu ID -> handle of the menus inserted (InsertMenu)
        tb.popups = 0
    return tb


# -- MenuInfo -----------------------------------------------------------------------------------------
# menuID 0, menuWidth 2, menuHeight 4, menuProc 6, enableFlags 10, menuData 14: the title
# (a Pascal string), then each item: its name, icon, key equivalent, mark and style bytes,
# and a zero byte at the end.

def _data(tb, mh):
    if not mh or not tb.heap.is_handle(mh) or tb.heap.size(mh) < 15:
        raise Unimplemented("Menu Manager: %08X is not a menu" % mh)
    return bytes(tb.m.rbytes(tb.heap.deref(mh), tb.heap.size(mh)))


def _parse(d):
    """(header, title, [[name, icon, cmd, mark, style]])."""
    pos = 14
    title = d[pos + 1:pos + 1 + d[pos]]
    pos += 1 + d[pos]
    items = []
    while pos < len(d) and d[pos]:
        n = d[pos]
        name = d[pos + 1:pos + 1 + n]
        icon, cmd, mark, style = d[pos + 1 + n:pos + 5 + n].ljust(4, b"\0")
        items.append([name, icon, cmd, mark, style])
        pos += 5 + n
    return d[:14], title, items


def _write(tb, mh, head, title, items):
    out = bytearray(head) + bytes([len(title)]) + title
    for name, icon, cmd, mark, style in items:
        out += bytes([len(name)]) + name + bytes([icon, cmd, mark, style])
    out += b"\0"
    tb.heap.set_size(mh, len(out))
    tb.m.wbytes(tb.heap.deref(mh), bytes(out))


def _flags(head):
    return struct.unpack(">I", head[10:14])[0]


def _set_flags(head, f):
    return head[:10] + struct.pack(">I", f & 0xFFFFFFFF)


def _parse_meta(text):
    """AppendMenu's items: HCX's own reading (hcx.xcmds.parse_menu_meta)."""
    from hcx.xcmds import parse_menu_meta
    return parse_menu_meta(text)


def _insert_items(tb, mh, text, after):
    head, title, items = _parse(_data(tb, mh))
    flags = _flags(head)
    new = _parse_meta(text)
    at = max(0, min(after, len(items)))
    for k, (item, enabled) in enumerate(new):
        n = at + k + 1                      # its item number
        if n < 32:                          # the bits after it move up
            low = flags & ((1 << n) - 1)
            high = (flags >> n) << (n + 1)
            flags = low | high | ((1 << n) if enabled else 0)
        items.insert(at + k, item)
    _write(tb, mh, _set_flags(head, flags), title, items)


def new_menu(tb, mid, title):
    h = tb.heap.new_handle(0)
    _write(tb, h, struct.pack(">hhhiI", mid, 0, 0, 0, 0xFFFFFFFF), title, [])
    return h


def menu_items(tb, mh, depth=0):
    """The menu as host.popup_menu takes it, and for each item the
    (menu handle, item number) of each row of its submenu. A submenu's
    own submenus are flattened into it: "child › grandchild"."""
    head, title, items = _parse(_data(tb, mh))
    flags = _flags(head)
    out, subs = [], []
    for k, (name, icon, cmd, mark, style) in enumerate(items, 1):
        enabled = bool(flags & 1) and (k >= 32 or bool(flags & (1 << k)))
        text = _from_mac(name)
        sub, rows = [], []
        if cmd == 0x1B and depth < 4:       # hierarchical: mark is the submenu's ID
            sub_h = tb.menus.get(mark) or tb.menus.get(mark - 256)
            if sub_h:
                kids, kid_rows = menu_items(tb, sub_h, depth + 1)
                for j, kid in enumerate(kids, 1):
                    if kid["sub"]:
                        sub += ["%s › %s" % (kid["text"], g) for g in kid["sub"]]
                        rows += kid_rows[j - 1]
                    else:
                        sub.append(kid["text"])
                        rows.append((sub_h, j))
        out.append({"text": text, "sub": sub, "enabled": enabled, "sep": text.startswith("-"),
                    "checked": mark != 0 and cmd != 0x1B, "title": False, "style": style})
        subs.append(rows)
    return out, subs


def _item(tb, mh, n):
    head, title, items = _parse(_data(tb, mh))
    return head, title, items, (items[n - 1] if 1 <= n <= len(items) else None)


# -- traps ----------------------------------------------------------------------------------------------

def install(tb_trap):
    def trap(*nums):
        def deco(f):
            tb_trap(*nums)(f)
            return f
        return deco

    @trap(0xA931)
    def _new_menu(tb, op):
        m = tb.m
        title, mid = m.pstr(m.pop32()), m.pops16()
        tb.ret32(new_menu(tb, mid, title))

    @trap(0xA9BF)
    def _get_menu(tb, op):
        """GetMenu: a copy of the MENU resource."""
        rid = tb.m.pops16()
        h = tb.get_resource("MENU", rid)
        tb.ret32(h or 0)

    @trap(0xA932)
    def _dispose_menu(tb, op):
        _state(tb)
        h = tb.m.pop32()
        for k, v in list(tb.menus.items()):
            if v == h:
                del tb.menus[k]

    @trap(0xA933)
    def _append_menu(tb, op):
        m = tb.m
        text, mh = m.pstr(m.pop32()), m.pop32()
        _insert_items(tb, mh, text, 1 << 15)

    @trap(0xA826)
    def _ins_menu_item(tb, op):
        m = tb.m
        after, text, mh = m.pops16(), m.pstr(m.pop32()), m.pop32()
        _insert_items(tb, mh, text, after)

    @trap(0xA952)
    def _del_menu_item(tb, op):
        m = tb.m
        n, mh = m.pops16(), m.pop32()
        head, title, items = _parse(_data(tb, mh))
        if 1 <= n <= len(items):
            del items[n - 1]
            f = _flags(head)
            if n < 32:
                f = (f & ((1 << n) - 1)) | ((f >> (n + 1)) << n)
            _write(tb, mh, _set_flags(head, f), title, items)

    @trap(0xA94D, 0xA951)
    def _add_res_menu(tb, op):
        """AddResMenu and InsertResMenu: the names of the resources of a type
        (and, for fonts, the fonts HCX's Font menu has), sorted."""
        from hcx.xcmds import res_menu_names
        m = tb.m
        after = m.pops16() if op == 0xA951 else 1 << 15
        rtype, mh = m.pop32().to_bytes(4, "big").decode("mac_roman"), m.pop32()
        res = [r for f in tb.search_files(False) for r in tb.files.get(f, [])]
        for k, name in enumerate(res_menu_names(rtype, res)):
            # each one as an item of its own: no metacharacters
            head, title, items = _parse(_data(tb, mh))
            at = min(after, len(items)) + k if after != 1 << 15 else len(items)
            _insert_items(tb, mh, b"x", at)
            head, title, items = _parse(_data(tb, mh))
            items[at][0] = name.encode("mac_roman", "replace")[:255]
            _write(tb, mh, head, title, items)

    @trap(0xA935)
    def _insert_menu(tb, op):
        m = tb.m
        _state(tb)
        m.pop16()
        mh = m.pop32()
        if mh and tb.heap.is_handle(mh):
            tb.menus[struct.unpack(">h", _data(tb, mh)[:2])[0]] = mh

    @trap(0xA936)
    def _delete_menu(tb, op):
        _state(tb)
        tb.menus.pop(tb.m.pops16(), None)

    @trap(0xA949)
    def _get_mhandle(tb, op):
        _state(tb)
        tb.ret32(tb.menus.get(tb.m.pops16(), 0))

    @trap(0xA950)
    def _count_mitems(tb, op):
        mh = tb.m.pop32()
        tb.ret16(len(_parse(_data(tb, mh))[2]) if mh and tb.heap.is_handle(mh) else 0)

    @trap(0xA946)
    def _get_item(tb, op):
        """GetItem (GetMenuItemText)."""
        m = tb.m
        s, n, mh = m.pop32(), m.pops16(), m.pop32()
        e = _item(tb, mh, n)[3]
        m.wpstr(s, e[0] if e else b"")

    @trap(0xA947)
    def _set_item(tb, op):
        m = tb.m
        s, n, mh = m.pstr(m.pop32()), m.pops16(), m.pop32()
        head, title, items, e = _item(tb, mh, n)
        if e:
            e[0] = s
            _write(tb, mh, head, title, items)

    def _byte_trap(index, op_get, op_set, size=2):
        def get(tb, op):
            m = tb.m
            p, n, mh = m.pop32(), m.pops16(), m.pop32()
            e = _item(tb, mh, n)[3]
            v = e[index] if e else 0
            if size == 2:
                m.w16(p, v)
            else:
                m.w8(p, v)

        def put(tb, op):
            m = tb.m
            v, n, mh = m.pop16(), m.pops16(), m.pop32()
            head, title, items, e = _item(tb, mh, n)
            if e:
                e[index] = v & 0xFF
                _write(tb, mh, head, title, items)
        tb_trap(op_get)(get)
        tb_trap(op_set)(put)

    _byte_trap(1, 0xA93F, 0xA940)           # GetItemIcon, SetItemIcon
    _byte_trap(2, 0xA84E, 0xA84F)           # GetItemCmd, SetItemCmd
    _byte_trap(3, 0xA943, 0xA944)           # GetItemMark, SetItemMark
    _byte_trap(4, 0xA941, 0xA942)           # GetItemStyle, SetItemStyle

    @trap(0xA945)
    def _check_item(tb, op):
        m = tb.m
        on, n, mh = m.pop16(), m.pops16(), m.pop32()
        head, title, items, e = _item(tb, mh, n)
        if e:
            e[3] = 0x12 if on else 0
            _write(tb, mh, head, title, items)

    @trap(0xA939, 0xA93A)
    def _enable_item(tb, op):
        """EnableItem and DisableItem (0: the whole menu)."""
        m = tb.m
        n, mh = m.pops16(), m.pop32()
        head, title, items = _parse(_data(tb, mh))
        if n < 32:
            f = _flags(head)
            f = f | (1 << n) if op == 0xA939 else f & ~(1 << n)
            _write(tb, mh, _set_flags(head, f), title, items)

    @trap(0xA948)
    def _calc_menu_size(tb, op):
        mh = tb.m.pop32()
        head, title, items = _parse(_data(tb, mh))
        w = max([6 * len(i[0]) + 30 for i in items] or [0])
        _write(tb, mh, head[:2] + struct.pack(">hh", w, 16 * len(items)) + head[6:], title, items)

    @trap(0xA80B)
    def _pop_up_menu_select(tb, op):
        """PopUpMenuSelect(menu, top, left, popUpItem): the host's pop-up
        menu; (menu ID << 16) | item, or 0."""
        m = tb.m
        _state(tb)
        tb.popups += 1
        if tb.popups > _MAX_POPUPS:
            raise Unimplemented("PopUpMenuSelect: too many menus")
        m.pops16()
        left, top, mh = m.pops16(), m.pops16(), m.pop32()
        items, subs = menu_items(tb, mh)
        r = tb.it.host.popup_menu(items, left, top)
        mid = struct.unpack(">H", _data(tb, mh)[:2])[0]
        if not r or not 1 <= r[0] <= len(items):
            m.w32(0xB54, mid << 16)     # MenuDisable: released over no item of this menu
            return tb.ret32(0)
        line, sub = r
        enabled = items[line - 1]["enabled"]
        if sub and 1 <= sub <= len(subs[line - 1]):
            mh, line = subs[line - 1][sub - 1]
            mid = struct.unpack(">H", _data(tb, mh)[:2])[0]
            enabled = menu_items(tb, mh)[0][line - 1]["enabled"]
        m.w32(0xB54, (mid << 16) | line)
        tb.ret32((mid << 16) | line if enabled else 0)  # a disabled item: 0, and it is in MenuDisable

    for num, size in {0xA938: 2, 0xA937: 0, 0xA94C: 2, 0xA934: 0}.items():   # HiliteMenu DrawMenuBar ...
        tb_trap(num)(_skip(size))


def _skip(size):
    def f(tb, op):
        tb.m.sp = tb.m.sp + size
    return f
