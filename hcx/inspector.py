"""Inspector sidebar (the port of HCX's authoring panel): stack, background,
card and selected button/field properties. Edits change the open stack in
memory and redraw the card."""

from gi.repository import Adw, GLib, Gtk, Pango

from .i18n import _
from .model import BUTTON_STYLES, FIELD_STYLES
from .render import MAC_FONT_IDS

ALIGNS = ["left", "center", "right"]


def _script_view(text):
    tv = Gtk.TextView()
    tv.set_editable(False)
    tv.set_monospace(True)
    tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    tv.set_top_margin(8)
    tv.set_bottom_margin(8)
    tv.set_left_margin(8)
    tv.set_right_margin(8)
    tv.get_buffer().set_text(text.strip() or _("(no script)"), -1)
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    sw.set_min_content_height(160)
    sw.set_max_content_height(420)
    sw.set_propagate_natural_height(True)
    sw.set_child(tv)
    sw.add_css_class("card")
    return sw


class Inspector(Gtk.Box):
    def __init__(self, on_change, on_play=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_change = on_change
        self.on_play = on_play
        self.view = None
        self.stack_pages = Gtk.Stack()
        self.stack_pages.set_vexpand(True)
        self.stack_pages.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        sw = Gtk.StackSwitcher(stack=self.stack_pages)
        sw.set_halign(Gtk.Align.CENTER)
        sw.set_margin_top(6)
        sw.set_margin_bottom(6)
        sw.set_margin_start(6)
        sw.set_margin_end(6)
        self.append(sw)
        self.append(self.stack_pages)
        self._pages = {}
        for name, title in (("object", _("Object")), ("card", _("Card")),
                            ("background", _("Background")), ("stack", _("Stack"))):
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.stack_pages.add_titled(holder, name, title)
            self._pages[name] = holder
        self._building = False

    def bind(self, view):
        self.view = view

    # -- helpers ---------------------------------------------------------------
    def _replace(self, name, page):
        holder = self._pages[name]
        c = holder.get_first_child()
        while c:
            n = c.get_next_sibling()
            holder.remove(c)
            c = n
        page.set_vexpand(True)
        holder.append(page)

    def _changed(self):
        if not self._building:
            self.on_change()

    def info(self, group, title, value):
        r = Adw.ActionRow(title=title)
        lab = Gtk.Label(label=str(value), selectable=True, xalign=1)
        lab.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        lab.add_css_class("dim-label")
        r.add_suffix(lab)
        group.add(r)

    def entry(self, group, title, obj, attr):
        r = Adw.EntryRow(title=title)
        r.set_text(getattr(obj, attr) or "")

        def changed(row):
            setattr(obj, attr, row.get_text())
            self._changed()
        r.connect("changed", changed)
        group.add(r)

    def switch(self, group, title, obj, attr, after=None):
        r = Adw.SwitchRow(title=title)
        r.set_active(bool(getattr(obj, attr)))

        def changed(row, _p):
            setattr(obj, attr, row.get_active())
            if after:
                after()
            self._changed()
        r.connect("notify::active", changed)
        group.add(r)

    def spin(self, group, title, getter, setter, lo, hi):
        r = Adw.SpinRow.new_with_range(lo, hi, 1)
        r.set_title(title)
        r.set_value(getter())

        def changed(row, _p):
            setter(int(row.get_value()))
            self._changed()
        r.connect("notify::value", changed)
        group.add(r)

    def combo(self, group, title, options, labels, obj, attr):
        r = Adw.ComboRow(title=title)
        r.set_model(Gtk.StringList.new(labels))
        cur = getattr(obj, attr)
        if cur in options:
            r.set_selected(options.index(cur))

        def changed(row, _p):
            i = row.get_selected()
            if 0 <= i < len(options):
                setattr(obj, attr, options[i])
                self._changed()
        r.connect("notify::selected", changed)
        group.add(r)

    def script_group(self, page, text):
        g = Adw.PreferencesGroup(title=_("Script"))
        g.add(_script_view(text))
        page.add(g)

    # -- pages -----------------------------------------------------------------------
    def refresh(self):
        v = self.view
        if not v or not v.stack or not v.card:
            return
        self._building = True
        try:
            self._replace("stack", self.stack_page(v.stack))
            bg = v.stack.bg_by_id(v.card.bg_id)
            self._replace("background", self.layer_page(bg, v.stack, False))
            self._replace("card", self.layer_page(v.card, v.stack, True))
            self._replace("object", self.object_page())
        finally:
            self._building = False

    def show_object(self):
        self.stack_pages.set_visible_child_name("object")

    def stack_page(self, st):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title=st.name)
        self.info(g, _("Cards"), len(st.cards))
        self.info(g, _("Backgrounds"), len(st.backgrounds))
        self.info(g, _("Card Size"), "%d × %d" % (st.width, st.height))
        self.info(g, _("Format"), "HyperCard %s" % ("1.x" if st.format <= 8 else "2.x"))
        if st.created_by:
            self.info(g, _("Created by"), "HyperCard " + st.created_by)
        if st.modified_by:
            self.info(g, _("Last modified by"), "HyperCard " + st.modified_by)
        self.info(g, _("Text encoding"), {"mac_roman": "Mac Roman", "shift_jis": "Shift-JIS"}.get(st.encoding, st.encoding))
        self.info(g, _("User level"), st.user_level)
        if st.cant_modify:
            self.info(g, _("Can't modify"), "✓")
        if st.private_access:
            self.info(g, _("Private access"), "✓")
        if st.source_path:
            self.info(g, _("Original file"), st.source_path)
        page.add(g)
        counts = {}
        for m in st.media:
            counts[m["type"]] = counts.get(m["type"], 0) + 1
        if counts:
            g2 = Adw.PreferencesGroup(title=_("Resources"))
            for t, lab in (("icon", _("Icons")), ("sound", _("Sounds")), ("picture", _("Pictures")),
                           ("external", _("Externals (XCMD/XFCN)"))):
                if counts.get(t):
                    self.info(g2, lab, counts[t])
            page.add(g2)
        sounds = [m for m in st.media if m["type"] == "sound"]
        if sounds and self.on_play:
            g3 = Adw.PreferencesGroup(title=_("Sounds"))
            for m in sounds:
                r = Adw.ActionRow(title=GLib.markup_escape_text(m["name"] or "#%d" % m["id"]),
                                  subtitle="ID %d" % m["id"])
                if (m.get("file") or "").endswith(".wav"):
                    b = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER,
                                   css_classes=["flat"], tooltip_text=_("Play"))
                    b.connect("clicked", lambda _b, name=m["name"] or str(m["id"]): self.on_play(name))
                    r.add_suffix(b)
                    r.set_activatable_widget(b)
                else:
                    r.set_subtitle("ID %d — %s" % (m["id"], _("compressed sound, not converted")))
                g3.add(r)
            page.add(g3)
        self.script_group(page, st.script)
        return page

    def layer_page(self, layer, st, is_card):
        page = Adw.PreferencesPage()
        if not layer:
            return page
        g = Adw.PreferencesGroup()
        self.entry(g, _("Name"), layer, "name")
        self.info(g, _("ID"), layer.id)
        if is_card:
            self.info(g, _("Number"), "%d / %d" % (st.cards.index(layer) + 1, len(st.cards)))
            self.switch(g, _("Marked"), layer, "marked")
        else:
            n = sum(1 for c in st.cards if c.bg_id == layer.id)
            self.info(g, _("Cards"), n)
        self.switch(g, _("Show picture"), layer, "show_pict")
        self.switch(g, _("Can't delete"), layer, "cant_delete")
        self.switch(g, _("Don't search"), layer, "dont_search")
        nb = sum(1 for p in layer.parts if p.type == "button")
        self.info(g, _("Parts"), "%d %s, %d %s" % (nb, _("Buttons").lower(), len(layer.parts) - nb,
                                                    _("Fields").lower()))
        page.add(g)
        self.script_group(page, layer.script)
        return page

    def object_page(self):
        v = self.view
        page = Adw.PreferencesPage()
        p, layer = v.part_of(v.selected)
        if not p:
            st = Adw.StatusPage(icon_name="find-location-symbolic",
                                description=_("Select a button or field with the Button or Field tool."))
            st.add_css_class("compact")
            g = Adw.PreferencesGroup()
            g.add(st)
            page.add(g)
            return page
        is_btn = p.type == "button"
        g = Adw.PreferencesGroup(title="%s — %s" % (_("Button") if is_btn else _("Field"),
                                                   _("Background") if v.selected[0] == "bg" else _("Card")))
        self.entry(g, _("Name"), p, "name")
        self.info(g, _("ID"), p.id)
        num = [q for q in layer.parts if q.type == p.type].index(p) + 1
        self.info(g, _("Number"), num)
        styles = BUTTON_STYLES if is_btn else FIELD_STYLES
        self.combo(g, _("Style"), styles, styles, p, "style")
        self.switch(g, _("Visible"), p, "visible")
        if is_btn:
            self.switch(g, _("Enabled"), p, "enabled")
            self.switch(g, _("Show name"), p, "show_name")
            self.switch(g, _("Auto highlight"), p, "auto_hilite")
            self.switch(g, _("Highlight"), p, "hilite")
            if v.selected[0] == "bg":
                self.switch(g, _("Shared highlight"), p, "shared_hilite")
            self.spin(g, _("Family"), lambda: p.family, lambda x: setattr(p, "family", x), 0, 15)
            self.spin(g, _("Icon"), lambda: p.icon, lambda x: setattr(p, "icon", x), -32768, 32767)
        else:
            self.switch(g, _("Lock text"), p, "lock_text")
            self.switch(g, _("Don't wrap"), p, "dont_wrap")
            if v.selected[0] == "bg":
                self.switch(g, _("Shared text"), p, "shared_text")
            self.switch(g, _("Fixed line height"), p, "fixed_line_height")
            self.switch(g, _("Show lines"), p, "show_lines")
            self.switch(g, _("Wide margins"), p, "wide_margins")
        page.add(g)

        g2 = Adw.PreferencesGroup(title=_("Position and Size"))

        def setr(attr_lo, attr_hi, keep_size):
            def f(val):
                if keep_size:
                    size = getattr(p, attr_hi) - getattr(p, attr_lo)
                    setattr(p, attr_lo, val)
                    setattr(p, attr_hi, val + size)
                else:
                    setattr(p, attr_hi, getattr(p, attr_lo) + max(1, val))
            return f
        self.spin(g2, _("Left"), lambda: p.left, setr("left", "right", True), -2000, 4000)
        self.spin(g2, _("Top"), lambda: p.top, setr("top", "bottom", True), -2000, 4000)
        self.spin(g2, _("Width"), lambda: p.width, setr("left", "right", False), 1, 4000)
        self.spin(g2, _("Height"), lambda: p.height, setr("top", "bottom", False), 1, 4000)
        page.add(g2)

        g3 = Adw.PreferencesGroup(title=_("Text"))
        st = v.stack
        ids = sorted(set(list(st.fonts.keys()) + [0, 3, 2, 4, 20, 21, 22, 16, p.text_font_id]))
        names = [st.fonts.get(i) or MAC_FONT_IDS.get(i, "#%d" % i) for i in ids]
        self.combo(g3, _("Font"), ids, names, p, "text_font_id")
        self.spin(g3, _("Size"), lambda: p.text_size, lambda x: setattr(p, "text_size", x), 1, 127)
        self.spin(g3, _("Line height"), lambda: p.text_height, lambda x: setattr(p, "text_height", x), 1, 255)
        self.combo(g3, _("Alignment"), ALIGNS, [_("Align left"), _("Center"), _("Align right")], p, "text_align")
        page.add(g3)
        self.script_group(page, p.script)
        return page
