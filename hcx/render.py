"""Draws a HyperCard card (background picture, background parts, card picture,
card parts) into a Gsk render node with Gtk.Snapshot. Geometry follows
HyperCard / HyperCardPreview; text uses the system fonts through Pango."""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import addcolor  # noqa: E402



def rgba(r, g, b, a=1.0):
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = r, g, b, a
    return c


BLACK = rgba(0, 0, 0)
WHITE = rgba(1, 1, 1)
GRAY = rgba(0.5, 0.5, 0.5)
LIGHT = rgba(0.75, 0.75, 0.75)
SEL = rgba(0.21, 0.52, 0.89)
OUTLINE = rgba(0.21, 0.52, 0.89, 0.55)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BUILTIN_ICONS = os.path.join(DATA_DIR, "icons")

# Classic Mac font ids when the stack's font table doesn't name them
MAC_FONT_IDS = {0: "Chicago", 1: "Geneva", 2: "New York", 3: "Geneva", 4: "Monaco", 5: "Venice",
                6: "London", 7: "Athens", 8: "San Francisco", 9: "Toronto", 11: "Cairo",
                12: "Los Angeles", 13: "Zapf Dingbats", 14: "Bookman", 16: "Palatino",
                18: "Zapf Chancery", 20: "Times", 21: "Helvetica", 22: "Courier", 23: "Symbol",
                24: "Taliesin", 16384: "Osaka"}

SERIF = ("new york", "times", "palatino", "bookman", "century", "garamond", "georgia", "athens",
         "london", "zapf chancery", "venice", "toronto", "minchō", "mincho", "明朝")
MONO = ("monaco", "courier", "osaka−等幅", "osaka-等幅", "monospace")
BOLDISH = ("chicago", "charcoal")


def linux_family(mac_name, size=None):
    """The font to draw a Mac font with: the user's own Mac font when it was
    imported from a Mac disk (see macfonts), else a similar Linux font."""
    if size:
        from . import macfonts
        fam = macfonts.family_for(mac_name, size)
        if fam:
            return fam
    n = (mac_name or "").lower()
    if any(k in n for k in MONO):
        return "Monospace"
    if any(k in n for k in SERIF):
        return "Serif"
    return "Sans"


def R(x, y, w, h):
    return Graphene.Rect().init(x, y, w, h)


def P(x, y):
    return Graphene.Point().init(x, y)


def fill(s, x, y, w, h, color=BLACK):
    if w > 0 and h > 0:
        s.append_color(color, R(x, y, w, h))


def frame(s, x, y, w, h, color=BLACK, t=1, radius=0):
    if w <= 0 or h <= 0:
        return
    rr = Gsk.RoundedRect()
    rr.init_from_rect(R(x, y, w, h), radius)
    s.append_border(rr, [t, t, t, t], [color, color, color, color])


def rfill(s, x, y, w, h, radius, color):
    if w <= 0 or h <= 0:
        return
    rr = Gsk.RoundedRect()
    rr.init_from_rect(R(x, y, w, h), radius)
    s.push_rounded_clip(rr)
    s.append_color(color, R(x, y, w, h))
    s.pop()


def oval_path(x, y, w, h):
    b = Gsk.PathBuilder.new()
    rr = Gsk.RoundedRect()
    rr.init(R(x, y, w, h), Graphene.Size().init(w / 2, h / 2), Graphene.Size().init(w / 2, h / 2),
            Graphene.Size().init(w / 2, h / 2), Graphene.Size().init(w / 2, h / 2))
    b.add_rounded_rect(rr)
    return b.to_path()


def content_hilite(c):
    """Per-card hilite of a background button (HyperCard stores "1")."""
    if not c:
        return False
    if c.hilite is not None:
        return bool(c.hilite)
    return c.text.strip() == "1"


class Renderer:
    more_stacks = None
    color_state = None      # {"enabled": bool, "extra": [objects drawn by AddColor commands]}

    def __init__(self, stack, pango_context):
        self.stack = stack
        self.pctx = pango_context
        self.textures = {}

    # -- resources ---------------------------------------------------------
    def texture(self, path, invert=False):
        key = (path, invert)
        if key in self.textures:
            return self.textures[key]
        tex = None
        try:
            if path and os.path.exists(path):
                pb = GdkPixbuf.Pixbuf.new_from_file(path)
                if not pb.get_has_alpha():
                    pb = pb.add_alpha(False, 0, 0, 0)
                if invert:
                    px = bytearray(pb.get_pixels())
                    rs, n = pb.get_rowstride(), pb.get_n_channels()
                    for yy in range(pb.get_height()):
                        o = yy * rs
                        for xx in range(pb.get_width()):
                            i = o + xx * n
                            if px[i + 3]:
                                px[i] ^= 255
                                px[i + 1] ^= 255
                                px[i + 2] ^= 255
                    pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(px)), GdkPixbuf.Colorspace.RGB,
                                                         True, 8, pb.get_width(), pb.get_height(), rs)
                tex = Gdk.Texture.new_for_pixbuf(pb)
        except GLib.Error:
            tex = None
        self.textures[key] = tex
        return tex

    def icon_path(self, icon_id):
        p = self.stack.media_file("icon", icon_id)
        if p:
            return p
        # resource chain: stacks in use (start using), then Home
        for st in (self.more_stacks() if self.more_stacks else []):
            p = st.media_file("icon", icon_id) if st is not self.stack else None
            if p:
                return p
        b = os.path.join(BUILTIN_ICONS, "icon_%d.png" % icon_id)
        return b if os.path.exists(b) else None

    def picture(self, s, layer, w, h):
        if not layer or not layer.bitmap or not layer.show_pict:
            return
        tex = self.texture(os.path.join(self.stack.dir_path, layer.bitmap))
        if tex:
            s.append_scaled_texture(tex, Gsk.ScalingFilter.NEAREST, R(0, 0, tex.get_width(), tex.get_height()))

    # -- text ----------------------------------------------------------------
    def font_name(self, fid):
        return self.stack.fonts.get(fid) or MAC_FONT_IDS.get(fid, "Geneva")

    def font_desc(self, fid, size, styles):
        name = self.font_name(fid)
        fd = Pango.FontDescription.new()
        fam = linux_family(name, size)
        fd.set_family(fam)
        fd.set_absolute_size(max(1, size) * Pango.SCALE)
        real = fam.lower().startswith("hcx ") or fam.lower() == name.lower()
        if "bold" in styles or (not real and any(k in name.lower() for k in BOLDISH)):
            fd.set_weight(Pango.Weight.BOLD)
        if "italic" in styles:
            fd.set_style(Pango.Style.ITALIC)
        if "condensed" in styles:
            fd.set_stretch(Pango.Stretch.CONDENSED)
        elif "extend" in styles:
            fd.set_stretch(Pango.Stretch.EXPANDED)
        return fd

    def simple_layout(self, text, fid, size, styles):
        lay = Pango.Layout.new(self.pctx)
        lay.set_font_description(self.font_desc(fid, size, styles))
        lay.set_text(text, -1)
        attrs = Pango.AttrList()
        if "underline" in styles or "group" in styles:
            attrs.insert(Pango.attr_underline_new(Pango.Underline.SINGLE))
        lay.set_attributes(attrs)
        return lay

    def field_layout(self, part, content, width):
        text = content.text if content else ""
        lay = Pango.Layout.new(self.pctx)
        lay.set_font_description(self.font_desc(part.text_font_id, part.text_size, part.text_style))
        lay.set_text(text, -1)
        if part.dont_wrap:
            lay.set_width(-1)
        else:
            lay.set_width(max(1, width) * Pango.SCALE)
            lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_alignment({"center": Pango.Alignment.CENTER, "right": Pango.Alignment.RIGHT}.get(
            part.text_align, Pango.Alignment.LEFT))
        attrs = Pango.AttrList()
        if part.fixed_line_height or not (content and content.runs):
            attrs.insert(Pango.attr_line_height_new_absolute(max(1, part.text_height) * Pango.SCALE))
        if "underline" in part.text_style or "group" in part.text_style:
            attrs.insert(Pango.attr_underline_new(Pango.Underline.SINGLE))
        if content and content.runs and text:
            enc = text.encode("utf-8")
            # char offset -> utf-8 byte index
            byte_at = []
            acc = 0
            for ch in text:
                byte_at.append(acc)
                acc += len(ch.encode("utf-8"))
            byte_at.append(acc)
            runs = sorted(content.runs)
            for i, (off, sid) in enumerate(runs):
                start = byte_at[min(off, len(text))]
                end = byte_at[min(runs[i + 1][0], len(text))] if i + 1 < len(runs) else len(enc)
                if end <= start:
                    continue
                st = self.stack.styles.get(sid)
                if not st:
                    continue
                fid = st["font"] if st.get("font") is not None and st["font"] >= 0 else part.text_font_id
                size = st["size"] if st.get("size") is not None and st["size"] > 0 else part.text_size
                sty = st["style"] if st.get("style") is not None else part.text_style
                fd = self.font_desc(fid, size, sty)
                a = Pango.attr_font_desc_new(fd)
                a.start_index, a.end_index = start, end
                attrs.insert(a)
                if "underline" in sty or "group" in sty:
                    a = Pango.attr_underline_new(Pango.Underline.SINGLE)
                    a.start_index, a.end_index = start, end
                    attrs.insert(a)
        lay.set_attributes(attrs)
        return lay

    def draw_layout(self, s, lay, x, y, color=BLACK):
        s.save()
        s.translate(P(x, y))
        s.append_layout(lay, color)
        s.restore()

    # -- card ----------------------------------------------------------------
    def render(self, card, opts):
        """opts: dict with keys hilite (dict key->bool), scroll (dict key->px),
        skip_text (key), bg_only (bool), outlines ('button'|'field'|None),
        selected (key). Returns a Gsk.RenderNode or None."""
        st = self.stack
        W, H = st.width, st.height
        self.opts = opts
        self.s = Gtk.Snapshot()
        self.pending = []
        fill(self.s, 0, 0, W, H, WHITE)
        bg = st.bg_by_id(card.bg_id) if card else None
        self.card = card
        self.bg = bg
        if bg:
            self.picture(self.s, bg, W, H)
            for p in bg.parts:
                self.part(p, bg, "bg")
        if card and not opts.get("bg_only"):
            self.picture(self.s, card, W, H)
            for p in card.parts:
                self.part(p, card, "cd")
        self.overlays(card, bg)
        node = self.s.to_node()
        self.s = None
        color = self.color_layer(card, bg) if not opts.get("outlines") else None
        out = Gtk.Snapshot()
        out.push_clip(R(0, 0, W, H))
        if node and color:
            # AddColor: the black-and-white card over the colors, white letting the color through
            out.push_blend(Gsk.BlendMode.MULTIPLY)
            out.append_node(color)
            out.pop()
            out.append_node(node)
            out.pop()
        elif node:
            out.append_node(node)
        out.pop()
        return out.to_node()

    # -- color (AddColor) ------------------------------------------------------------
    def color_layer(self, card, bg):
        state = self.color_state or {}
        if state.get("enabled") is False:
            return None
        table = addcolor.load(self.stack)
        extra = [x for x in state.get("extra", []) if x.get("card") is card]
        if not table and not extra:
            return None
        items = []
        if table:
            if bg is not None:
                items += [("bg", it) for it in table["backgrounds"].get(addcolor.key(bg.id), [])]
            if card is not None and not self.opts.get("bg_only"):
                items += [("cd", it) for it in table["cards"].get(addcolor.key(card.id), [])]
        items += [(x.get("layer", "cd"), x) for x in extra]
        if not items:
            return None
        st = self.stack
        s = Gtk.Snapshot()
        fill(s, 0, 0, st.width, st.height, WHITE)
        drew = False
        for kind, it in items:
            if it.get("hidden"):
                continue
            t = it["type"]
            if t in ("button", "field"):
                layer = bg if kind == "bg" else card
                p = next((q for q in (layer.parts if layer else []) if q.type == t and q.id == it["id"]), None)
                if p is None or not p.visible:
                    continue
                self._color_rect(s, p.left, p.top, p.width, p.height, it["rgb"], it.get("bevel", 0))
                drew = True
            elif t == "rect":
                l, tp, r, b = it["rect"]
                self._color_rect(s, l, tp, r - l, b - tp, it["rgb"], it.get("bevel", 0))
                drew = True
            elif t in ("pict", "pictfile") and it.get("file"):
                path = it["file"] if os.path.isabs(it["file"]) else os.path.join(st.dir_path, it["file"])
                tex = self.texture(path)
                if tex:
                    l, tp, r, b = it["rect"]
                    w, h = (r - l, b - tp) if r > l and b > tp else (tex.get_width(), tex.get_height())
                    s.append_scaled_texture(tex, Gsk.ScalingFilter.NEAREST, R(l, tp, w, h))
                    drew = True
        return s.to_node() if drew else None

    @staticmethod
    def _color_rect(s, x, y, w, h, c, bevel):
        r, g, b = addcolor.rgb(c)
        fill(s, x, y, w, h, rgba(r, g, b))
        k = max(0, min(int(bevel or 0), w // 2, h // 2))
        if k:
            light = rgba(r + (1 - r) * 0.5, g + (1 - g) * 0.5, b + (1 - b) * 0.5)
            dark = rgba(r * 0.55, g * 0.55, b * 0.55)
            fill(s, x, y, w, k, light)
            fill(s, x, y, k, h, light)
            fill(s, x, y + h - k, w, k, dark)
            fill(s, x + w - k, y, k, h, dark)

    def invert(self, x, y, w, h, oval=False, clip=None):
        """Queue an XOR of the area (applied once the part is drawn)."""
        if clip:
            cx, cy, cw, ch = clip
            nx, ny = max(x, cx), max(y, cy)
            w, h = min(x + w, cx + cw) - nx, min(y + h, cy + ch) - ny
            x, y = nx, ny
        if w > 0 and h > 0:
            self.pending.append((x, y, w, h, oval))

    def _flush_inversions(self):
        for x, y, w, h, oval in self.pending:
            node = self.s.to_node()
            s = Gtk.Snapshot()
            s.push_blend(Gsk.BlendMode.DIFFERENCE)
            if node:
                s.append_node(node)
            s.pop()
            if oval:
                s.append_fill(oval_path(x, y, w, h), Gsk.FillRule.WINDING, WHITE)
            else:
                fill(s, x, y, w, h, WHITE)
            s.pop()
            self.s = s
        self.pending = []

    def content_for(self, part, layer, kind):
        if kind == "cd":
            return layer.contents.get(part.id)
        bg = layer
        if part.type == "field" and part.shared_text:
            return bg.contents.get(part.id)
        if part.type == "button":
            return bg.contents.get(part.id) if part.shared_hilite else (
                self.card.bg_contents.get(part.id) if self.card else None) or bg.contents.get(part.id)
        return self.card.bg_contents.get(part.id) if self.card else None

    def hilite_of(self, part, layer, kind):
        key = (kind, part.id)
        hl = self.opts.get("hilite", {})
        if key in hl:
            return hl[key]
        if kind == "bg" and part.type == "button" and not part.shared_hilite and self.card:
            return content_hilite(self.card.bg_contents.get(part.id))
        return part.hilite

    def part(self, p, layer, kind):
        if not p.visible or p.width <= 0 or p.height <= 0:
            return
        self.s.push_clip(R(p.left, p.top, p.width, p.height))
        try:
            if p.type == "field":
                self.field(p, layer, kind)
            else:
                self.button(p, layer, kind)
        finally:
            self.s.pop()
        self._flush_inversions()

    # -- buttons -------------------------------------------------------------
    def button(self, p, layer, kind):
        hl = self.hilite_of(p, layer, kind)
        if p.style in ("checkbox", "radio"):
            return self.check_button(p, hl)
        if p.style == "popup":
            return self.popup_button(p, layer, kind)
        s = self.s
        x, y, w, h = p.left, p.top, p.width, p.height
        bgc = BLACK if hl else WHITE
        style = p.style
        icon = self.icon_path(p.icon) if p.icon else None
        if style == "opaque":
            fill(s, x, y, w, h, bgc)
        elif style == "rectangle":
            fill(s, x, y, w, h, bgc)
            frame(s, x, y, w, h)
        elif style == "shadow":
            fill(s, x, y, w - 1, h - 1, bgc)
            frame(s, x, y, w - 1, h - 1)
            fill(s, x + 2, y + h - 1, w - 2, 1)
            fill(s, x + w - 1, y + 2, 1, h - 3)
        elif style == "roundrect":
            rfill(s, x, y, w - 1, h - 1, 7, bgc)
            frame(s, x, y, w - 1, h - 1, radius=7)
            fill(s, x + 6, y + h - 1, w - 12, 1)
            fill(s, x + w - 1, y + 6, 1, h - 12)
        elif style == "standard":
            rfill(s, x, y, w, h, 4, bgc)
            frame(s, x, y, w, h, radius=4)
        elif style == "default":
            frame(s, x, y, w, h, t=3, radius=7)
            rfill(s, x + 4, y + 4, w - 8, h - 8, 4, bgc)
            frame(s, x + 4, y + 4, w - 8, h - 8, radius=4)

        transparent = style in ("transparent", "oval")
        txt = WHITE if (hl and not transparent) else BLACK
        if not p.enabled and not hl:
            txt = GRAY
        if p.icon:
            fid, size, sty = 3, 9, set()  # icon buttons: Geneva 9, like HyperCard
            lay = self.simple_layout(p.name, fid, size, sty) if p.show_name else None
            th = lay.get_pixel_size()[1] if lay else 0
            total = 32 + 1 + th + 1 if lay else 34
            oy = y + h // 2 - total // 2 + 1
            if icon:
                tex = self.texture(icon, invert=hl)
                if tex:
                    s.append_scaled_texture(tex, Gsk.ScalingFilter.NEAREST,
                                            R(x + w // 2 - 16, oy, tex.get_width(), tex.get_height()))
            if lay:
                tw = lay.get_pixel_size()[0]
                nx = self.name_x(p, x, w, tw)
                ny = oy + 33
                if transparent or hl:
                    fill(s, nx - 2, ny, tw + 4, th, BLACK if hl else WHITE)
                self.draw_layout(s, lay, nx, ny, WHITE if hl else (GRAY if not p.enabled else BLACK))
            return
        if p.show_name and p.name:
            lay = self.simple_layout(p.name, p.text_font_id, p.text_size, p.text_style)
            tw, th = lay.get_pixel_size()
            bx, bw = (x + 4, w - 8) if style == "default" else (x, w)
            nx = self.name_x(p, bx, bw, tw)
            ny = y + (h - th) // 2
            self.draw_layout(s, lay, nx, ny, txt)
        if hl and transparent:
            self.invert(x, y, w, h, oval=(style == "oval"))

    def name_x(self, p, x, w, tw):
        if p.text_align == "left":
            return x + 9 if p.style not in ("transparent", "oval", "opaque") else x + 2
        if p.text_align == "right":
            return x + w - tw - (9 if p.style not in ("transparent", "oval", "opaque") else 2)
        return x + (w - tw) // 2

    def check_button(self, p, hl):
        s = self.s
        x, y, w, h = p.left, p.top, p.width, p.height
        bx, by = x + 3, y + h // 2 - 6
        col = BLACK if p.enabled else GRAY
        if p.style == "checkbox":
            fill(s, bx, by, 12, 12, WHITE)
            frame(s, bx, by, 12, 12, col)
            if hl:
                b = Gsk.PathBuilder.new()
                b.move_to(bx + 1.5, by + 1.5)
                b.line_to(bx + 10.5, by + 10.5)
                b.move_to(bx + 10.5, by + 1.5)
                b.line_to(bx + 1.5, by + 10.5)
                s.append_stroke(b.to_path(), Gsk.Stroke.new(1.2), col)
        else:
            s.append_fill(oval_path(bx, by, 12, 12), Gsk.FillRule.WINDING, WHITE)
            s.append_stroke(oval_path(bx + 0.5, by + 0.5, 11, 11), Gsk.Stroke.new(1), col)
            if hl:
                s.append_fill(oval_path(bx + 3, by + 3, 6, 6), Gsk.FillRule.WINDING, col)
        if p.show_name and p.name:
            lay = self.simple_layout(p.name, p.text_font_id, p.text_size, p.text_style)
            th = lay.get_pixel_size()[1]
            self.draw_layout(s, lay, x + 19, y + (h - th) // 2, col)

    def popup_items(self, p, layer, kind):
        c = self.content_for(p, layer, kind)
        if (not c or not c.text) and kind == "bg":
            c = layer.contents.get(p.id)
        return (c.text if c else "").split("\n") if c and c.text else []

    def popup_button(self, p, layer, kind):
        s = self.s
        x, y, w, h = p.left, p.top, p.width, p.height
        col = BLACK if p.enabled else GRAY
        tw = max(0, p.title_width)
        if p.show_name and tw:
            lay = self.simple_layout(p.name, p.text_font_id, p.text_size, p.text_style)
            th = lay.get_pixel_size()[1]
            self.draw_layout(s, lay, x + 4, y + (h - th) // 2, col)
        px, pw = x + tw, w - tw
        if pw <= 0:
            return
        fill(s, px, y, pw - 1, h - 1, WHITE)
        frame(s, px, y, pw - 1, h - 1, col)
        fill(s, px + 2, y + h - 1, pw - 2, 1, col)
        fill(s, px + pw - 1, y + 2, 1, h - 3, col)
        ax = px + pw - 18 if pw > 22 else px + pw // 2 - 6
        ay = y + (h - 6) // 2
        b = Gsk.PathBuilder.new()
        b.move_to(ax, ay)
        b.line_to(ax + 11, ay)
        b.line_to(ax + 5.5, ay + 6)
        b.close()
        s.append_fill(b.to_path(), Gsk.FillRule.WINDING, col)
        items = self.popup_items(p, layer, kind)
        sel = p.selected_line - 1 if p.selected_line > 0 else 0
        if 0 <= sel < len(items):
            lay = self.simple_layout(items[sel], p.text_font_id, p.text_size, p.text_style)
            lay.set_width(max(1, pw - 38) * Pango.SCALE)
            lay.set_ellipsize(Pango.EllipsizeMode.END)
            th = lay.get_pixel_size()[1]
            s.push_clip(R(px, y, pw - 1, h - 1))
            self.draw_layout(s, lay, px + 18, y + (h - 1 - th) // 2, col)
            s.pop()

    # -- fields --------------------------------------------------------------
    @staticmethod
    def field_rects(p):
        """(content rect, text rect) as (x, y, w, h)."""
        l, t, r, b = p.left + 1, p.top + 1, p.right - 1, p.bottom - 1
        if p.style == "shadow":
            r -= 2
            b -= 2
        elif p.style == "scrolling":
            r -= 16
        content = (l, t, r - l, b - t)
        tl = l + 3 + (5 if p.wide_margins else 0)
        tt = t + (4 if p.wide_margins else 0)
        tr = r - 3 - (3 if p.wide_margins else 0)
        return content, (tl, tt, tr - tl, b - tt)

    def field(self, p, layer, kind):
        s = self.s
        x, y, w, h = p.left, p.top, p.width, p.height
        style = p.style
        if style == "opaque":
            fill(s, x, y, w, h, WHITE)
        elif style in ("rectangle", "scrolling"):
            fill(s, x, y, w, h, WHITE)
            frame(s, x, y, w, h)
        elif style == "shadow":
            fill(s, x, y, w - 2, h - 2, WHITE)
            frame(s, x, y, w - 2, h - 2)
            fill(s, x + 3, y + h - 2, w - 3, 2)
            fill(s, x + w - 2, y + 3, 2, h - 5)
        (cx, cy, cw, ch), (tx, ty, tw, th) = self.field_rects(p)
        key = (kind, p.id)
        scroll = self.opts.get("scroll", {}).get(key, 0) if style == "scrolling" else 0
        c = self.content_for(p, layer, kind)
        s.push_clip(R(cx, cy, cw, ch))
        if p.show_lines:
            lh = max(4, p.text_height)
            n = 0
            yy = ty + lh - max(2, lh // 4) - scroll % lh
            while yy < cy + ch and n < 400:
                if yy >= cy:
                    xx = tx
                    while xx < tx + tw:
                        fill(s, xx, yy, 1, 1, GRAY)
                        xx += 2
                yy += lh
                n += 1
        if self.opts.get("skip_text") != key and c and c.text:
            lay = self.field_layout(p, c, tw)
            self.draw_layout(s, lay, tx, ty - scroll)
            found = self.opts.get("found")
            if found and found[0] == key:
                for rx, ry, rw, rh in self._range_rects(lay, c.text, found[1], found[2]):
                    self.invert(tx + rx, ty - scroll + ry, rw, rh, clip=(cx, cy, cw, ch))
            sel = p.selected_line
            if sel > 0 and p.auto_select:
                ok, rect_line = self._line_rect(lay, sel - 1)
                if ok:
                    self.invert(cx, ty - scroll + rect_line[0], cw, rect_line[1], clip=(cx, cy, cw, ch))
        s.pop()
        if style == "scrolling":
            self.scrollbar(p, layer, kind, scroll)

    @staticmethod
    def _range_rects(lay, text, start, end):
        """Pixel rects (x, y, w, h) covering characters [start, end)."""
        bs = len(text[:start].encode("utf-8"))
        be = len(text[:end].encode("utf-8"))
        out = []
        it = lay.get_iter()
        while True:
            line = it.get_line_readonly()
            ls, ll = line.start_index, line.length
            if ls < be and ls + ll > bs:
                _, log = it.get_line_extents()
                a0, a1 = max(bs, ls), min(be, ls + ll)
                x0 = lay.index_to_pos(a0).x
                x1 = lay.index_to_pos(a1).x if a1 < ls + ll else log.x + log.width
                if lay.index_to_pos(a1).y != lay.index_to_pos(a0).y and a1 < ls + ll:
                    x1 = log.x + log.width
                x0, x1 = sorted((x0 // Pango.SCALE, x1 // Pango.SCALE))
                out.append((x0, log.y // Pango.SCALE, max(1, x1 - x0), log.height // Pango.SCALE))
            if not it.next_line():
                break
        return out

    @staticmethod
    def _line_rect(lay, index):
        text = lay.get_text()
        lines = text.split("\n")
        if index >= len(lines):
            return False, None
        boff = len("\n".join(lines[:index]).encode("utf-8")) + (1 if index else 0)
        it = lay.get_iter()
        top = None
        bottom = None
        endb = boff + len(lines[index].encode("utf-8"))
        while True:
            li = it.get_line_readonly()
            if li.start_index >= boff and li.start_index <= max(boff, endb):
                _, log = it.get_line_extents()
                if top is None:
                    top = log.y // Pango.SCALE
                bottom = (log.y + log.height) // Pango.SCALE
            elif top is not None:
                break
            if not it.next_line():
                break
        if top is None:
            return False, None
        return True, (top, bottom - top)

    def text_height(self, p, layer, kind):
        c = self.content_for(p, layer, kind)
        if not c or not c.text:
            return 0
        _, (tx, ty, tw, th) = self.field_rects(p)
        return self.field_layout(p, c, tw).get_pixel_size()[1]

    def max_scroll(self, p, layer, kind):
        (cx, cy, cw, ch), (tx, ty, tw, th) = self.field_rects(p)
        return max(0, self.text_height(p, layer, kind) - th)

    def scrollbar(self, p, layer, kind, scroll):
        s = self.s
        sx = p.right - 17
        y, h = p.top, p.height
        fill(s, sx, y, 1, h)  # separator
        bx, bw = sx + 1, 15
        ms = self.max_scroll(p, layer, kind)
        # arrows
        for (ay, up) in ((y + 1, True), (y + h - 16, False)):
            fill(s, bx, ay, bw, 15, WHITE)
            b = Gsk.PathBuilder.new()
            cx = bx + bw / 2
            if up:
                b.move_to(cx, ay + 4)
                b.line_to(cx + 5, ay + 10)
                b.line_to(cx - 5, ay + 10)
            else:
                b.move_to(cx - 5, ay + 5)
                b.line_to(cx + 5, ay + 5)
                b.line_to(cx, ay + 11)
            b.close()
            s.append_fill(b.to_path(), Gsk.FillRule.WINDING, BLACK if ms else GRAY)
        fill(s, bx, y + 16, bw, 1)
        fill(s, bx, y + h - 17, bw, 1)
        ty, th = y + 17, h - 34
        if th <= 0:
            return
        if ms:
            # gray track with a white knob, like the classic Mac OS
            fill(s, bx, ty, bw, th, LIGHT)
            ky = ty + int((th - 16) * scroll / ms) if th > 16 else ty
            fill(s, bx, ky, bw, 16, WHITE)
            fill(s, bx, ky, bw, 1)
            fill(s, bx, ky + 15, bw, 1)
        else:
            fill(s, bx, ty, bw, th, WHITE)

    # -- authoring overlays --------------------------------------------------
    def overlays(self, card, bg):
        mode = self.opts.get("outlines")
        if not mode:
            return
        s = self.s
        sel = self.opts.get("selected")
        layers = [("bg", bg)] if self.opts.get("bg_only") else [("bg", bg), ("cd", card)]
        for kind, layer in layers:
            if not layer:
                continue
            if kind == "bg" and not self.opts.get("bg_only"):
                continue
            for p in layer.parts:
                if p.type != mode:
                    continue
                x, y, w, h = p.left, p.top, p.width, p.height
                if (kind, p.id) == sel:
                    frame(s, x - 1, y - 1, w + 2, h + 2, SEL, t=2)
                    for hx, hy in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
                        fill(s, hx - 3, hy - 3, 7, 7, SEL)
                else:
                    frame(s, x, y, w, h, OUTLINE)
