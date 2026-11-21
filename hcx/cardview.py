"""The card widget: draws the current card with render.Renderer and handles
the browse tool (buttons, fields, popups, scrolling) and the button/field
tools (select, move, resize, create)."""

import math

from gi.repository import GLib, GObject, Graphene, Gsk, Gtk, Pango

from .model import Content, Part
from .render import Renderer, content_hilite

HANDLE = 5


class CardView(Gtk.Fixed):
    __gtype_name__ = "HCXCardView"
    __gsignals__ = {
        # HyperTalk message for the window's script runner: (message, target or None)
        "message": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "modified": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self.stack = None
        self.card = None
        self.renderer = None
        self.zoom = 1.0
        self.tool = "browse"          # browse | button | field
        self.bg_only = False
        self.selected = None          # (kind, part id)
        self.pressed = {}             # key -> temporary hilite
        self.busy = lambda: False     # the window says when a script is running
        self.pending_click = False    # for `the mouseClick`
        self.script_click = (0, 0)
        self.scroll = {}              # key -> scroll offset in px
        self.editing = None           # (kind, pid, textview)
        self._node = None
        self._drag = None
        self._trans = None            # (old node, effect, start time, duration)
        self.locked = None            # node shown while a script has locked the screen
        self.click = None             # (cx, cy, kind, part, layer) of the last click
        self.found = None             # (key, start, end) search hit to highlight
        self.set_focusable(True)
        self.set_can_focus(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)

        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect("pressed", self._on_pressed)
        click.connect("released", self._on_released)
        self.add_controller(click)
        self._click = click

        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

    # -- model ----------------------------------------------------------------
    def set_stack(self, stack):
        self.finish_editing()
        self.stack = stack
        self.renderer = Renderer(stack, self.get_pango_context())
        self.renderer.more_stacks = getattr(self, "more_stacks", None)
        self.renderer.color_state = getattr(self, "color_state", None)
        self.card = None
        self.scroll = {}
        self.selected = None
        self._update_size()

    def set_card(self, card, effect=None):
        self.finish_editing()
        if self.locked is not None:
            effect = None
        old = self._node
        if old is None and self.card is not None and self.renderer and effect:
            old = self.renderer.render(self.card, {"scroll": self.scroll, "bg_only": self.bg_only})
        self._start_transition(old, effect)
        self.card = card
        self.scroll = {}
        self.pressed = {}
        if self.selected and self.selected[0] == "cd":
            self.selected = None
            self.emit("selection-changed")
        self.refresh()

    def set_zoom(self, z):
        self.finish_editing()
        self.zoom = z
        self._update_size()
        self.refresh()

    def set_tool(self, tool):
        self.finish_editing()
        self.tool = tool
        self.selected = None
        self.emit("selection-changed")
        self.refresh()

    def set_bg_only(self, on):
        self.finish_editing()
        self.bg_only = on
        self.selected = None
        self.emit("selection-changed")
        self.refresh()

    def _update_size(self):
        if self.stack:
            self.set_size_request(int(self.stack.width * self.zoom), int(self.stack.height * self.zoom))

    def refresh(self):
        self._node = None
        self.queue_draw()

    def layer_of(self, kind):
        if kind == "cd":
            return self.card
        return self.stack.bg_by_id(self.card.bg_id) if self.card else None

    def part_of(self, key):
        if not key or not self.card:
            return None, None
        layer = self.layer_of(key[0])
        return (layer.part_by_id(key[1]) if layer else None), layer

    # -- drawing ---------------------------------------------------------------
    def lock(self):
        """Screen locked by a script: keep showing the current picture."""
        if self.locked is None and self.card is not None and self.renderer:
            if self._node is None:
                self._node = self.renderer.render(self.card, {"scroll": self.scroll, "bg_only": self.bg_only,
                                                              "hilite": self.pressed})
            self.locked = self._node

    def unlock(self, effect=None):
        old = self.locked
        self.locked = None
        self._node = None
        if old is not None and effect:
            self._start_transition(old, effect)
        self.queue_draw()

    def char_at(self, kind, p, layer, cx, cy):
        """0-based index of the character at card point (cx, cy) in a field."""
        c = self.renderer.content_for(p, layer, kind)
        if not c or not c.text:
            return None
        _, (tx, ty, tw, th) = self.renderer.field_rects(p)
        lay = self.renderer.field_layout(p, c, tw)
        x = (cx - tx) * Pango.SCALE
        y = (cy - ty + self.scroll.get((kind, p.id), 0)) * Pango.SCALE
        ok, idx, trailing = lay.xy_to_index(int(x), int(y))
        if not ok:
            return None
        return len(c.text.encode("utf-8")[:idx].decode("utf-8", "ignore"))

    def do_snapshot(self, snapshot):
        if not self.stack:
            return
        if self.locked is not None and not self._trans:
            snapshot.save()
            snapshot.scale(self.zoom, self.zoom)
            snapshot.append_node(self.locked)
            snapshot.restore()
            return
        if self._node is None:
            opts = {"hilite": self.pressed, "scroll": self.scroll, "bg_only": self.bg_only,
                    "found": self.found,
                    "outlines": self.tool if self.tool in ("button", "field") else None,
                    "selected": self.selected,
                    "skip_text": self.editing[:2] if self.editing else None}
            self._node = self.renderer.render(self.card, opts)
        snapshot.save()
        snapshot.scale(self.zoom, self.zoom)
        if self._trans:
            self._snapshot_transition(snapshot)
        elif self._node:
            snapshot.append_node(self._node)
        snapshot.restore()
        child = self.get_first_child()
        while child:
            self.snapshot_child(child, snapshot)
            child = child.get_next_sibling()

    # -- visual effects ----------------------------------------------------------
    DURATIONS = {"very fast": 0.15, "fast": 0.3, "normal": 0.5, "slow": 0.8, "very slow": 1.2}

    def _start_transition(self, old, effect):
        self._trans = None
        if not old or not effect or effect[0] in ("plain", "cut"):
            return
        dur = self.DURATIONS.get(effect[2], 0.5)
        self._trans = (old, effect, GLib.get_monotonic_time(), dur * 1e6)

        def tick(widget, clock):
            if not self._trans:
                return GLib.SOURCE_REMOVE
            if GLib.get_monotonic_time() - self._trans[2] >= self._trans[3]:
                self._trans = None
                self.queue_draw()
                return GLib.SOURCE_REMOVE
            self.queue_draw()
            return GLib.SOURCE_CONTINUE
        self.add_tick_callback(tick)

    def _snapshot_transition(self, s):
        old, (name, direction, _speed), t0, dur = self._trans
        new = self._node
        t = max(0.0, min(1.0, (GLib.get_monotonic_time() - t0) / dur))
        W, H = self.stack.width, self.stack.height

        def rect(x, y, w, h):
            return Graphene.Rect().init(x, y, max(0, w), max(0, h))

        def clipped(node, r):
            s.push_clip(r)
            if node:
                s.append_node(node)
            s.pop()

        def circle(node, radius):
            rr = Gsk.RoundedRect()
            r = Graphene.Rect().init(W / 2 - radius, H / 2 - radius, 2 * radius, 2 * radius)
            rr.init_from_rect(r, radius)
            s.push_clip(rect(0, 0, W, H))
            s.push_rounded_clip(rr)
            if node:
                s.append_node(node)
            s.pop()
            s.pop()

        if name == "wipe":
            s.append_node(old)
            d = direction or "left"
            r = {"left": rect(W * (1 - t), 0, W * t, H), "right": rect(0, 0, W * t, H),
                 "up": rect(0, H * (1 - t), W, H * t), "down": rect(0, 0, W, H * t)}.get(d, rect(0, 0, W * t, H))
            clipped(new, r)
        elif name in ("scroll", "push"):
            d = direction or "left"
            dx, dy = {"left": (-W, 0), "right": (W, 0), "up": (0, -H), "down": (0, H)}.get(d, (-W, 0))
            s.push_clip(rect(0, 0, W, H))
            for node, k in ((old, t), (new, t - 1)):
                s.save()
                s.translate(Graphene.Point().init(dx * k, dy * k))
                if node:
                    s.append_node(node)
                s.restore()
            s.pop()
        elif name == "iris":
            R = math.hypot(W, H) / 2
            if direction == "close":
                s.append_node(new) if new else None
                circle(old, R * (1 - t))
            else:
                s.append_node(old)
                circle(new, R * t)
        elif name in ("barn door", "zoom", "shrink", "stretch"):
            closing = direction in ("close", "in") or name == "shrink"
            k = 1 - t if closing else t
            if name == "barn door":
                r = rect(W / 2 * (1 - k), 0, W * k, H)
            else:
                r = rect(W / 2 * (1 - k), H / 2 * (1 - k), W * k, H * k)
            if closing:
                s.append_node(new) if new else None
                clipped(old, r)
            else:
                s.append_node(old)
                clipped(new, r)
        else:  # dissolve, venetian blinds, checkerboard…
            s.push_cross_fade(t)
            s.append_node(old)
            s.pop()
            if new:
                s.append_node(new)
            s.pop()

    # -- hit testing ------------------------------------------------------------
    def to_card(self, x, y):
        return x / self.zoom, y / self.zoom

    def hit(self, x, y, ptype=None, include_hidden=False, editable_only=False):
        """Topmost part under (x, y) in card coordinates -> (kind, part, layer).
        editable_only: only the layer being edited (card, or background in
        background mode), like HyperCard's button and field tools."""
        if not self.card:
            return None
        if self.bg_only:
            layers = [("bg", self.layer_of("bg"))]
        elif editable_only:
            layers = [("cd", self.card)]
        else:
            layers = [("cd", self.card), ("bg", self.layer_of("bg"))]
        for kind, layer in layers:
            if not layer:
                continue
            for p in reversed(layer.parts):
                if not p.visible and not include_hidden:
                    continue
                if ptype and p.type != ptype:
                    continue
                if p.left <= x < p.right and p.top <= y < p.bottom:
                    return kind, p, layer
        return None

    # -- browse tool ------------------------------------------------------------
    def _on_pressed(self, gesture, n_press, x, y):
        self.mouse_down = True
        if self.busy():
            # a script is running: it may poll the mouse, but the click starts nothing
            if self.card:
                cx, cy = self.to_card(x, y)
                self.script_click = (int(cx), int(cy))
            return
        self.grab_focus()
        if not self.card:
            return
        cx, cy = self.to_card(x, y)
        if self.tool != "browse":
            return self._tool_press(cx, cy, n_press)
        if self.editing:
            # click outside the editor ends editing
            self.finish_editing()
        h = self.hit(cx, cy)
        self.click = (int(cx), int(cy)) + (tuple(h) if h else (None, None, None))
        self._press = None
        if not h:
            self._press = (None, None, None)
            self.emit("message", "mouseDown", None)
            return
        kind, p, layer = h
        key = (kind, p.id)
        if p.type == "button":
            if not p.enabled:
                return
            if p.style == "popup":
                return self._popup(p, layer, kind)
            self._press = (key, p, layer)
            if p.auto_hilite and p.style not in ("checkbox", "radio"):
                self.pressed[key] = True
                self.refresh()
            self.emit("message", "mouseDown", ("part", kind, p, layer))
            return
        # field
        if p.style == "scrolling" and cx >= p.right - 17:
            return self._scrollbar_click(p, layer, kind, cy)
        if not p.lock_text:
            return self._begin_editing(kind, p, layer)
        if p.auto_select and p.dont_wrap:
            # list field: select clicked line
            line = self._line_at(p, layer, kind, cy)
            if line:
                p.selected_line = line
                self.refresh()
        self._press = (key, p, layer)
        self.emit("message", "mouseDown", ("part", kind, p, layer))

    def _on_released(self, gesture, n_press, x, y):
        self.mouse_down = False
        self.pending_click = True
        if self.busy():
            return
        if self.tool != "browse" or not getattr(self, "_press", None):
            return
        key, p, layer = self._press
        self._press = None
        if p is None:
            self.emit("message", "mouseUp", None)
            return
        cx, cy = self.to_card(x, y)
        inside = p.left <= cx < p.right and p.top <= cy < p.bottom
        self.pressed.pop(key, None)
        if inside and p.type == "button" and p.auto_hilite:
            self._toggle_hilite(key, p, layer)
        self.refresh()
        if inside:
            self.emit("message", "mouseUp", ("part", key[0], p, layer))

    def _set_hilite(self, key, p, value):
        if key[0] == "bg" and not p.shared_hilite:
            c = self.card.bg_contents.get(p.id)
            if c is None:
                c = self.card.bg_contents[p.id] = Content()
            c.hilite = value
            c.text = "1" if value else ""
        else:
            p.hilite = value

    def _get_hilite(self, key, p):
        if key[0] == "bg" and not p.shared_hilite:
            return content_hilite(self.card.bg_contents.get(p.id))
        return p.hilite

    def _toggle_hilite(self, key, p, layer):
        if p.style == "radio" or p.family:
            if p.family:
                for q in layer.parts:
                    if q.type == "button" and q.family == p.family and q is not p:
                        self._set_hilite((key[0], q.id), q, False)
            self._set_hilite(key, p, True)
        elif p.style == "checkbox":
            self._set_hilite(key, p, not self._get_hilite(key, p))

    def _popup(self, p, layer, kind):
        items = self.renderer.popup_items(p, layer, kind)
        if not items:
            return
        pop = getattr(self, "_hcx_popover", None)   # reused, see scriptui.popup_menu
        if pop is None:
            pop = Gtk.Popover()
            pop.set_parent(self)
            self._hcx_popover = pop
        pop.set_has_arrow(False)
        rect = Gdk_rect(int((p.left + p.title_width) * self.zoom), int(p.top * self.zoom),
                        int((p.width - p.title_width) * self.zoom), int(p.height * self.zoom))
        pop.set_pointing_to(rect)
        box = Gtk.ListBox()
        box.add_css_class("navigation-sidebar")
        for i, it in enumerate(items):
            row = Gtk.ListBoxRow()
            lab = Gtk.Label(label=it or " ", xalign=0)
            lab.set_margin_start(6)
            lab.set_margin_end(6)
            row.set_child(lab)
            box.append(row)
            if i == p.selected_line - 1:
                box.select_row(row)

        def activated(_b, row):
            p.selected_line = row.get_index() + 1
            pop.popdown()
            self.refresh()

        box.connect("row-activated", activated)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_propagate_natural_height(True)
        sw.set_max_content_height(400)
        sw.set_child(box)
        pop.set_child(sw)
        pop.popup()

    # -- fields -------------------------------------------------------------------
    def _line_at(self, p, layer, kind, cy):
        c = self.renderer.content_for(p, layer, kind)
        if not c or not c.text:
            return 0
        _, (tx, ty, tw, th) = self.renderer.field_rects(p)
        lay = self.renderer.field_layout(p, c, tw)
        y = (cy - ty + self.scroll.get((kind, p.id), 0)) * Pango.SCALE
        ok, idx, trailing = lay.xy_to_index(0, int(y))
        if not ok and y > lay.get_size()[1]:
            return 0
        text = c.text.encode("utf-8")[:idx].decode("utf-8", "ignore")
        return text.count("\n") + 1

    def _scroll_field(self, kind, p, layer, delta):
        key = (kind, p.id)
        ms = self.renderer.max_scroll(p, layer, kind)
        cur = self.scroll.get(key, 0)
        new = max(0, min(ms, cur + delta))
        if new != cur:
            self.scroll[key] = new
            self.refresh()
        return True

    def _scrollbar_click(self, p, layer, kind, cy):
        line = max(8, p.text_height)
        page = max(line, p.height - 2 * line)
        if cy < p.top + 17:
            d = -line
        elif cy >= p.bottom - 17:
            d = line
        else:
            ms = self.renderer.max_scroll(p, layer, kind)
            track = max(1, p.height - 34 - 16)
            knob = p.top + 17 + int(track * self.scroll.get((kind, p.id), 0) / ms) if ms else p.top
            d = -page if cy < knob else page
        self._scroll_field(kind, p, layer, d)

    def _on_scroll(self, ctl, dx, dy):
        if not self.card or self.editing:
            return False
        lx, ly = getattr(self, "_last_xy", (None, None))
        if lx is None:
            return False
        cx, cy = self.to_card(lx, ly)
        h = self.hit(cx, cy, "field")
        if h and h[1].style == "scrolling":
            kind, p, layer = h
            return self._scroll_field(kind, p, layer, dy * max(8, p.text_height))
        return False

    def content(self, kind, p, layer, create=False):
        """The text content shown in a field on the current card."""
        c = self.renderer.content_for(p, layer, kind)
        if c is None and create:
            c = Content()
            if kind == "cd" or p.shared_text:
                layer.contents[p.id] = c
            else:
                self.card.bg_contents[p.id] = c
        return c

    def _begin_editing(self, kind, p, layer):
        c = self.content(kind, p, layer, create=True)
        (cx, cy, cw, ch), _ = self.renderer.field_rects(p)
        tv = Gtk.TextView()
        tv.add_css_class("hcx-field-editor")
        tv.set_wrap_mode(Gtk.WrapMode.NONE if p.dont_wrap else Gtk.WrapMode.WORD_CHAR)
        tv.set_left_margin(int(3 * self.zoom))
        tv.set_right_margin(int(3 * self.zoom))
        fd = self.renderer.font_desc(p.text_font_id, p.text_size * self.zoom, p.text_style)
        tv.get_buffer().set_text(c.text, -1)
        css = Gtk.CssProvider()
        fam = fd.get_family()
        css.load_from_string(
            "textview.hcx-field-editor, textview.hcx-field-editor text { background: white; color: black;"
            " font-family: %s; font-size: %dpx; %s }" % (fam, max(1, int(p.text_size * self.zoom)),
                                                       "font-weight: bold;" if fd.get_weight() >= 700 else ""))
        tv.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(tv)
        sw.set_size_request(int(cw * self.zoom), int(ch * self.zoom))
        self.put(sw, int(cx * self.zoom), int(cy * self.zoom))
        self.editing = (kind, p.id, sw, tv, c)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._editor_key)
        tv.add_controller(key)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: GLib_idle(self.finish_editing))
        tv.add_controller(focus)
        self.refresh()
        tv.grab_focus()

    def _editor_key(self, ctl, keyval, keycode, state):
        from gi.repository import Gdk
        if keyval == Gdk.KEY_Escape:
            self.finish_editing()
            self.grab_focus()
            return True
        return False

    def finish_editing(self):
        if not self.editing:
            return False
        kind, pid, sw, tv, c = self.editing
        self.editing = None
        buf = tv.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if text != c.text:
            c.text = text
            c.runs = [(o, s) for o, s in c.runs if o < len(text)]
            self.emit("modified")
        self.remove(sw)
        self.refresh()
        return False

    # -- authoring tools -------------------------------------------------------------
    def _tool_press(self, cx, cy, n_press):
        sel_p, _ = self.part_of(self.selected)
        self._drag = None
        if sel_p:
            # resize handle?
            for corner, (hx, hy) in {"tl": (sel_p.left, sel_p.top), "tr": (sel_p.right, sel_p.top),
                                     "bl": (sel_p.left, sel_p.bottom), "br": (sel_p.right, sel_p.bottom)}.items():
                if abs(cx - hx) <= HANDLE and abs(cy - hy) <= HANDLE:
                    self._drag = ("resize", corner, sel_p, (sel_p.left, sel_p.top, sel_p.right, sel_p.bottom))
                    return
        h = self.hit(cx, cy, self.tool, include_hidden=True, editable_only=True)
        new = (h[0], h[1].id) if h else None
        if new != self.selected:
            self.selected = new
            self.emit("selection-changed")
            self.refresh()
        if h:
            p = h[1]
            self._drag = ("move", None, p, (p.left, p.top, p.right, p.bottom))

    def _on_drag_update(self, gesture, ox, oy):
        if self.tool == "browse" or not self._drag:
            return
        mode, corner, p, (l, t, r, b) = self._drag
        dx, dy = round(ox / self.zoom), round(oy / self.zoom)
        if mode == "move":
            p.left, p.top, p.right, p.bottom = l + dx, t + dy, r + dx, b + dy
        else:
            if "l" in corner:
                p.left = min(l + dx, r - 4)
            if "r" in corner:
                p.right = max(r + dx, l + 4)
            if "t" in corner:
                p.top = min(t + dy, b - 4)
            if "b" in corner:
                p.bottom = max(b + dy, t + 4)
        self.refresh()

    def _on_drag_end(self, gesture, ox, oy):
        if self._drag and (ox or oy):
            self.emit("modified")
            self.emit("selection-changed")
        self._drag = None

    def _on_motion(self, ctl, x, y):
        self._last_xy = (x, y)
        if not self.card:
            return
        cx, cy = self.to_card(x, y)
        name = "default"
        if self.tool == "browse":
            h = self.hit(cx, cy)
            if h and h[1].type == "field" and not h[1].lock_text and not (
                    h[1].style == "scrolling" and cx >= h[1].right - 17):
                name = "text"
            elif h and h[1].type == "button" and h[1].enabled and (h[1].script.strip() or h[1].auto_hilite
                                                                  or h[1].style == "popup"):
                name = "pointer"
        else:
            sel_p, _ = self.part_of(self.selected)
            if sel_p:
                for c, (hx, hy) in (("nwse-resize", (sel_p.left, sel_p.top)), ("nesw-resize", (sel_p.right, sel_p.top)),
                                    ("nesw-resize", (sel_p.left, sel_p.bottom)), ("nwse-resize", (sel_p.right, sel_p.bottom))):
                    if abs(cx - hx) <= HANDLE and abs(cy - hy) <= HANDLE:
                        name = c
                        break
            if name == "default" and self.hit(cx, cy, self.tool, include_hidden=True, editable_only=True):
                name = "move"
        self.set_cursor_from_name(name)

    def new_part(self, ptype):
        """Create a button or field in the middle of the card (or background)."""
        if not self.card:
            return None
        kind = "bg" if self.bg_only else "cd"
        layer = self.layer_of(kind)
        p = Part(ptype)
        p.id = layer.next_part_id()
        w, h = (100, 20) if ptype == "button" else (180, 85)
        x0 = (self.stack.width - w) // 2
        y0 = (self.stack.height - h) // 2
        p.left, p.top, p.right, p.bottom = x0, y0, x0 + w, y0 + h
        if ptype == "button":
            p.name = "New Button"
            p.style = "roundrect"
            p.auto_hilite = True
            p.text_font_id = 0
            p.text_size = 12
        else:
            p.style = "rectangle"
            p.text_font_id = 3
            p.text_size = 12
            p.text_height = 16
        layer.parts.append(p)
        if self.tool != ptype:
            self.tool = ptype
        self.selected = (kind, p.id)
        self.emit("selection-changed")
        self.emit("modified")
        self.refresh()
        return p

    def delete_selected(self):
        p, layer = self.part_of(self.selected)
        if not p:
            return False
        layer.parts.remove(p)
        layer.contents.pop(p.id, None)
        if self.selected[0] == "bg":
            for cd in self.stack.cards:
                if cd.bg_id == layer.id:
                    cd.bg_contents.pop(p.id, None)
        self.selected = None
        self.emit("selection-changed")
        self.emit("modified")
        self.refresh()
        return True

    def nudge(self, dx, dy):
        p, _ = self.part_of(self.selected)
        if not p:
            return False
        p.left += dx
        p.right += dx
        p.top += dy
        p.bottom += dy
        self.refresh()
        self.emit("selection-changed")
        return True


def Gdk_rect(x, y, w, h):
    from gi.repository import Gdk
    r = Gdk.Rectangle()
    r.x, r.y, r.width, r.height = x, y, max(1, w), max(1, h)
    return r


def GLib_idle(fn):
    from gi.repository import GLib

    def run():
        fn()
        return False
    GLib.idle_add(run)
