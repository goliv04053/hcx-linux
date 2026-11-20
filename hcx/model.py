"""In-memory stack model, shared by the importer and the viewer, plus the
`_stack.xml` reader/writer (same layout HCX's stackimport produced)."""

import os
import xml.etree.ElementTree as ET

BUTTON_STYLES = ["standard", "transparent", "opaque", "rectangle", "shadow", "roundrect",
                 "default", "oval", "popup", "checkbox", "radio"]
FIELD_STYLES = ["transparent", "opaque", "rectangle", "shadow", "scrolling"]
TEXT_STYLES = ["bold", "italic", "underline", "outline", "shadow", "condensed", "extend", "group"]


class Content:
    def __init__(self, text="", runs=None, hilite=None):
        self.text = text
        self.runs = runs or []      # list of (offset, style_id)
        self.hilite = hilite        # for background buttons without shared hilite


class Part:
    def __init__(self, ptype="button"):
        self.type = ptype
        self.id = 0
        self.name = ""
        self.script = ""
        self.left = self.top = self.right = self.bottom = 0
        self.visible = True
        self.enabled = True
        self.style = "transparent"
        self.show_name = True
        self.hilite = False
        self.auto_hilite = False
        self.shared_hilite = True
        self.family = 0
        self.title_width = 0
        self.icon = 0
        self.selected_line = 0      # 1-based, 0 = none
        self.text_align = "left"
        self.text_font_id = 0
        self.text_size = 12
        self.text_style = set()
        self.text_height = 16
        # fields
        self.lock_text = False
        self.dont_wrap = False
        self.dont_search = False
        self.shared_text = False
        self.fixed_line_height = False
        self.auto_tab = False
        self.auto_select = False
        self.show_lines = False
        self.wide_margins = False
        self.multiple_lines = False

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top


class Layer:
    def __init__(self, kind):
        self.kind = kind            # 'card' | 'background'
        self.id = 0
        self.name = ""
        self.script = ""
        self.bitmap = None
        self.show_pict = True
        self.dont_search = False
        self.cant_delete = False
        self.marked = False
        self.bg_id = 0
        self.parts = []
        self.contents = {}          # own part id -> Content
        self.bg_contents = {}       # (cards) background part id -> Content

    def part_by_id(self, pid):
        for p in self.parts:
            if p.id == pid:
                return p
        return None

    def next_part_id(self):
        return max([p.id for p in self.parts] + [0]) + 1


class Stack:
    def __init__(self):
        self.name = ""
        self.dir_path = ""
        self.source_path = ""
        self.id = 0
        self.format = 11
        self.width = 512
        self.height = 342
        self.cards = []
        self.backgrounds = []
        self.fonts = {}             # id -> name
        self.styles = {}            # id -> {'font':id|None,'size':int|None,'style':set|None}
        self.next_style_id = 0
        self.script = ""
        self.password = 0
        self.user_level = 5
        self.cant_modify = self.cant_delete = self.private_access = False
        self.cant_abort = self.cant_peek = False
        self.created_by = self.compacted_by = self.modified_by = self.opened_by = ""
        self.patterns = []
        self.media = []             # list of dicts
        self.encoding = "mac_roman"
        self.resizable = False

    def card_by_id(self, cid):
        for c in self.cards:
            if c.id == cid:
                return c
        return None

    def bg_by_id(self, bid):
        for b in self.backgrounds:
            if b.id == bid:
                return b
        return None

    def media_file(self, mtype, mid):
        for m in self.media:
            if m["type"] == mtype and m["id"] == mid and m.get("file"):
                return os.path.join(self.dir_path, m["file"])
        return None


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------

def _sub(parent, tag, text=None):
    e = ET.SubElement(parent, tag)
    if text is not None:
        e.text = str(text)
    return e


def _bool(parent, tag, value):
    e = ET.SubElement(parent, tag)
    ET.SubElement(e, "true" if value else "false")


def _content_xml(parent, layer_name, pid, content, is_button=False):
    e = _sub(parent, "content")
    _sub(e, "layer", layer_name)
    _sub(e, "id", pid)
    if content.hilite is not None and is_button:
        _bool(e, "highlight", content.hilite)
    else:
        _sub(e, "text", content.text)
        for off, sid in content.runs:
            r = _sub(e, "stylerun")
            _sub(r, "offset", off)
            _sub(r, "id", sid)


def _part_xml(parent, p, is_bg):
    e = _sub(parent, "part")
    _sub(e, "id", p.id)
    _sub(e, "type", p.type)
    _bool(e, "visible", p.visible)
    if p.type == "field":
        _bool(e, "dontWrap", p.dont_wrap)
        _bool(e, "dontSearch", p.dont_search)
        if is_bg:
            _bool(e, "sharedText", p.shared_text)
        _bool(e, "fixedLineHeight", p.fixed_line_height)
        _bool(e, "autoTab", p.auto_tab)
        _bool(e, "lockText", p.lock_text)
    else:
        _bool(e, "enabled", p.enabled)
    r = _sub(e, "rect")
    _sub(r, "left", p.left)
    _sub(r, "top", p.top)
    _sub(r, "right", p.right)
    _sub(r, "bottom", p.bottom)
    _sub(e, "style", p.style)
    if p.type == "field":
        _bool(e, "autoSelect", p.auto_select)
        _bool(e, "showLines", p.show_lines)
        _bool(e, "wideMargins", p.wide_margins)
        _bool(e, "multipleLines", p.multiple_lines)
        if p.selected_line > 0:
            sl = _sub(e, "selectedLines")
            _sub(sl, "integer", p.selected_line)
    else:
        _bool(e, "showName", p.show_name)
        _bool(e, "highlight", p.hilite)
        _bool(e, "autoHighlight", p.auto_hilite)
        _sub(e, "family", p.family)
        _sub(e, "titleWidth", p.title_width)
        if is_bg:
            _bool(e, "sharedHighlight", p.shared_hilite)
        if p.style == "popup":
            sl = _sub(e, "selectedLines")
            _sub(sl, "integer", p.selected_line)
        else:
            _sub(e, "icon", p.icon)
    _sub(e, "textAlign", p.text_align)
    _sub(e, "textFontID", p.text_font_id)
    _sub(e, "textSize", p.text_size)
    if not p.text_style:
        _sub(e, "textStyle", "plain")
    for s in TEXT_STYLES:
        if s in p.text_style:
            _sub(e, "textStyle", s)
    _sub(e, "textHeight", p.text_height)
    _sub(e, "name", p.name)
    _sub(e, "script", p.script)


def write_xml(stack, path):
    root = ET.Element("stackfile")
    s = _sub(root, "stack")
    _sub(s, "stackID", stack.id)
    _sub(s, "format", stack.format)
    _sub(s, "name", stack.name)
    _sub(s, "sourcePath", stack.source_path)
    _sub(s, "textEncoding", stack.encoding)
    _sub(s, "backgroundCount", len(stack.backgrounds))
    _sub(s, "firstBackgroundID", stack.backgrounds[0].id if stack.backgrounds else 0)
    _sub(s, "cardCount", len(stack.cards))
    _sub(s, "firstCardID", stack.cards[0].id if stack.cards else 0)
    _sub(s, "password", stack.password)
    _sub(s, "userLevel", stack.user_level)
    _bool(s, "cantModify", stack.cant_modify)
    _bool(s, "cantDelete", stack.cant_delete)
    _bool(s, "privateAccess", stack.private_access)
    _bool(s, "cantAbort", stack.cant_abort)
    _bool(s, "cantPeek", stack.cant_peek)
    _bool(s, "resizable", stack.resizable)
    _sub(s, "createdByVersion", stack.created_by)
    _sub(s, "lastCompactedVersion", stack.compacted_by)
    _sub(s, "modifyVersion", stack.modified_by)
    _sub(s, "openVersion", stack.opened_by)
    cs = _sub(s, "cardSize")
    _sub(cs, "width", stack.width)
    _sub(cs, "height", stack.height)
    pats = _sub(s, "patterns")
    for p in stack.patterns:
        _sub(pats, "pattern", p)
    _sub(s, "script", stack.script)

    for fid, name in stack.fonts.items():
        f = _sub(root, "font")
        _sub(f, "id", fid)
        _sub(f, "name", name)
    _sub(root, "nextStyleID", stack.next_style_id)
    for sid, st in stack.styles.items():
        e = _sub(root, "styleentry")
        _sub(e, "id", sid)
        if st.get("font") is not None:
            _sub(e, "font", st["font"])
        if st.get("size") is not None:
            _sub(e, "size", st["size"])
        if st.get("style") is not None:
            _sub(e, "style", " ".join(sorted(st["style"])) or "plain")

    for bg in stack.backgrounds:
        e = _sub(root, "background")
        _layer_common(e, bg)
        for p in bg.parts:
            _part_xml(e, p, True)
        for pid, c in bg.contents.items():
            _content_xml(e, "background", pid, c)
        _sub(e, "name", bg.name)
        _sub(e, "script", bg.script)

    for cd in stack.cards:
        e = _sub(root, "card")
        _layer_common(e, cd)
        _bool(e, "marked", cd.marked)
        _sub(e, "background", cd.bg_id)
        for p in cd.parts:
            _part_xml(e, p, False)
        for pid, c in cd.contents.items():
            _content_xml(e, "card", pid, c)
        bg = stack.bg_by_id(cd.bg_id)
        for pid, c in cd.bg_contents.items():
            bp = bg.part_by_id(pid) if bg else None
            _content_xml(e, "background", pid, c, is_button=bool(bp and bp.type == "button"))
        _sub(e, "name", cd.name)
        _sub(e, "script", cd.script)

    for m in stack.media:
        if m["type"] == "external":
            x = ET.SubElement(root, "externalcommand", {
                "type": m.get("kind", ""), "platform": m.get("platform", ""), "id": str(m["id"]),
                "size": str(m.get("size", 0)), "name": m["name"], "file": m["file"] or ""})
            continue
        e = _sub(root, "media")
        _sub(e, "id", m["id"])
        _sub(e, "type", m["type"])
        _sub(e, "name", m["name"])
        _sub(e, "file", m["file"])
        if m["type"] == "cursor":
            h = _sub(e, "hotspot")
            _sub(h, "left", m["hotspot"][0])
            _sub(h, "top", m["hotspot"][1])

    ET.indent(root, "\t")
    tree = ET.ElementTree(root)
    tmp = path + ".tmp"
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    os.replace(tmp, path)


def _layer_common(e, layer):
    _sub(e, "id", layer.id)
    _sub(e, "bitmap", layer.bitmap or "")
    _bool(e, "cantDelete", layer.cant_delete)
    _bool(e, "showPict", layer.show_pict)
    _bool(e, "dontSearch", layer.dont_search)


def _t(e, tag, default=""):
    c = e.find(tag)
    if c is None or c.text is None:
        return default
    return c.text


def _i(e, tag, default=0):
    try:
        return int(_t(e, tag, str(default)))
    except ValueError:
        return default


def _b(e, tag, default=False):
    c = e.find(tag)
    if c is None:
        return default
    if c.find("true") is not None:
        return True
    if c.find("false") is not None:
        return False
    return (c.text or "").strip() == "true"


def _read_part(e, is_bg):
    p = Part(_t(e, "type", "button"))
    p.id = _i(e, "id")
    p.visible = _b(e, "visible", True)
    r = e.find("rect")
    if r is not None:
        p.left, p.top, p.right, p.bottom = _i(r, "left"), _i(r, "top"), _i(r, "right"), _i(r, "bottom")
    p.style = _t(e, "style", "transparent")
    p.text_align = _t(e, "textAlign", "left") or "left"
    p.text_font_id = _i(e, "textFontID")
    p.text_size = _i(e, "textSize", 12)
    p.text_style = {x.text for x in e.findall("textStyle") if x.text and x.text != "plain"}
    p.text_height = _i(e, "textHeight", 16)
    p.name = _t(e, "name")
    p.script = _t(e, "script")
    sl = e.find("selectedLines")
    if sl is not None:
        try:
            p.selected_line = int(_t(sl, "integer", "0"))
        except ValueError:
            pass
    if p.type == "field":
        p.dont_wrap = _b(e, "dontWrap")
        p.dont_search = _b(e, "dontSearch")
        p.shared_text = _b(e, "sharedText")
        p.fixed_line_height = _b(e, "fixedLineHeight")
        p.auto_tab = _b(e, "autoTab")
        p.lock_text = _b(e, "lockText")
        p.auto_select = _b(e, "autoSelect")
        p.show_lines = _b(e, "showLines")
        p.wide_margins = _b(e, "wideMargins")
        p.multiple_lines = _b(e, "multipleLines")
    else:
        p.enabled = _b(e, "enabled", True)
        p.show_name = _b(e, "showName", True)
        p.hilite = _b(e, "highlight")
        p.auto_hilite = _b(e, "autoHighlight")
        p.family = _i(e, "family")
        p.title_width = _i(e, "titleWidth")
        p.shared_hilite = _b(e, "sharedHighlight", True)
        p.icon = _i(e, "icon")
    return p


def _read_content(e):
    hl = e.find("highlight")
    c = Content(_t(e, "text"))
    if hl is not None:
        c.hilite = _b(e, "highlight")
    c.runs = [(_i(r, "offset"), _i(r, "id")) for r in e.findall("stylerun")]
    return c


def _read_layer(e, kind):
    l = Layer(kind)
    l.id = _i(e, "id")
    l.bitmap = _t(e, "bitmap") or None
    l.cant_delete = _b(e, "cantDelete")
    l.show_pict = _b(e, "showPict", True)
    l.dont_search = _b(e, "dontSearch")
    l.name = _t(e, "name")
    l.script = _t(e, "script")
    for pe in e.findall("part"):
        l.parts.append(_read_part(pe, kind == "background"))
    for ce in e.findall("content"):
        c = _read_content(ce)
        pid = _i(ce, "id")
        if kind == "card" and _t(ce, "layer") == "background":
            l.bg_contents[pid] = c
        else:
            l.contents[pid] = c
    if kind == "card":
        l.marked = _b(e, "marked")
        l.bg_id = _i(e, "background")
    return l


def read_xml(dir_path):
    tree = ET.parse(os.path.join(dir_path, "_stack.xml"))
    root = tree.getroot()
    st = Stack()
    st.dir_path = dir_path
    s = root.find("stack")
    if s is not None:
        st.id = _i(s, "stackID")
        st.format = _i(s, "format", 11)
        st.name = _t(s, "name")
        st.source_path = _t(s, "sourcePath")
        st.encoding = _t(s, "textEncoding", "mac_roman")
        st.password = _i(s, "password")
        st.user_level = _i(s, "userLevel", 5)
        st.cant_modify = _b(s, "cantModify")
        st.cant_delete = _b(s, "cantDelete")
        st.private_access = _b(s, "privateAccess")
        st.cant_abort = _b(s, "cantAbort")
        st.cant_peek = _b(s, "cantPeek")
        st.resizable = _b(s, "resizable")
        st.created_by = _t(s, "createdByVersion")
        st.compacted_by = _t(s, "lastCompactedVersion")
        st.modified_by = _t(s, "modifyVersion")
        st.opened_by = _t(s, "openVersion")
        cs = s.find("cardSize")
        if cs is not None:
            st.width = _i(cs, "width", 512) or 512
            st.height = _i(cs, "height", 342) or 342
        st.patterns = [p.text for p in s.findall("patterns/pattern")]
        st.script = _t(s, "script")
    if not st.name:
        st.name = os.path.basename(dir_path).rsplit(".hcxs", 1)[0]
    for f in root.findall("font"):
        st.fonts[_i(f, "id")] = _t(f, "name")
    st.next_style_id = _i(root, "nextStyleID")
    for e in root.findall("styleentry"):
        sty = _t(e, "style", None)
        st.styles[_i(e, "id")] = {
            "font": _i(e, "font") if e.find("font") is not None else None,
            "size": _i(e, "size") if e.find("size") is not None else None,
            "style": (set(sty.split()) - {"plain"}) if sty is not None else None,
        }
    for e in root.findall("background"):
        st.backgrounds.append(_read_layer(e, "background"))
    for e in root.findall("card"):
        st.cards.append(_read_layer(e, "card"))
    for e in root.findall("media"):
        m = {"id": _i(e, "id"), "type": _t(e, "type"), "name": _t(e, "name"), "file": _t(e, "file")}
        h = e.find("hotspot")
        if h is not None:
            m["hotspot"] = (_i(h, "left"), _i(h, "top"))
        st.media.append(m)
    for e in root.findall("externalcommand"):
        a = e.attrib
        st.media.append({"id": int(a.get("id", 0)), "type": "external", "name": a.get("name", ""),
                         "file": a.get("file", ""), "kind": a.get("type", ""),
                         "platform": a.get("platform", ""), "size": int(a.get("size", 0))})
    return st
