"""GTK parts of HyperTalk: modal dialogs (answer, ask, list selection,
pop-up menus) and the floating script windows (palettes, picture windows,
text windows, movies), which float over the card like HyperCard's."""

from gi.repository import Adw, Gdk, GLib, Graphene, Gtk, Pango

from .i18n import _

CSS = """
row.hcx-sep, row.hcx-sep:hover { background: none; min-height: 0; padding: 3px 0; }
row.hcx-sep separator { min-height: 1px; background: alpha(currentColor, 0.25); }
.hcx-palette { background: white; border: 1px solid black; box-shadow: 2px 2px 0 rgba(0,0,0,0.6); }
.hcx-palette-title { min-height: 11px; padding: 0 4px; border-bottom: 1px solid black;
  background-image: repeating-linear-gradient(to bottom, white 0px, white 1px, black 1px, black 2px);
  background-size: 100% 10px; background-position: 0 1px; background-repeat: no-repeat; }
.hcx-palette-title label { background: white; color: black; font-family: "Chicago", "ChicagoFLF", sans-serif;
  font-size: 10px; font-weight: bold; padding: 0 4px; }
.hcx-palette-close { min-width: 9px; min-height: 9px; padding: 0; margin: 1px 0; border: 1px solid black;
  border-radius: 0; background: white; }
.hcx-textwin, .hcx-textwin text { background: white; color: black; }
"""


def run_modal(present, connect):
    """Present a dialog and wait for its answer without blocking GTK."""
    loop = GLib.MainLoop()
    box = []

    def done(value):
        if not box:
            box.append(value)
        if loop.is_running():
            loop.quit()
    connect(done)
    present()
    if not box:
        loop.run()
    return box[0] if box else None


def answer(win, text, buttons):
    dlg = Adw.AlertDialog(body=str(text))
    buttons = [b for b in buttons[:3] if b is not None] or ["OK"]
    for k, b in enumerate(buttons):
        dlg.add_response("r%d" % k, str(b) or "OK")
    dlg.set_default_response("r%d" % (len(buttons) - 1))
    dlg.set_close_response("r%d" % (len(buttons) - 1))

    def connect(done):
        dlg.connect("response", lambda d, r: done(buttons[int(r[1:])] if r.startswith("r") else buttons[-1]))
    return run_modal(lambda: dlg.present(win), connect)


def ask(win, prompt, default="", password=False):
    dlg = Adw.AlertDialog(body=str(prompt))
    entry = Gtk.PasswordEntry(show_peek_icon=True) if password else Gtk.Entry()
    entry.set_text(str(default))
    entry.set_activates_default(True)
    dlg.set_extra_child(entry)
    dlg.add_response("cancel", _("Cancel"))
    dlg.add_response("ok", "OK")
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.set_close_response("cancel")

    def connect(done):
        dlg.connect("response", lambda d, r: done(entry.get_text() if r == "ok" else None))
    return run_modal(lambda: (dlg.present(win), entry.grab_focus()), connect)


def list_select(win, prompt, lines, multiple=False):
    dlg = Adw.AlertDialog(body=str(prompt))
    lb = Gtk.ListBox(css_classes=["boxed-list"])
    lb.set_selection_mode(Gtk.SelectionMode.MULTIPLE if multiple else Gtk.SelectionMode.SINGLE)
    for ln in lines:
        lb.append(Gtk.Label(label=ln, xalign=0, margin_start=8, margin_end=8, margin_top=4, margin_bottom=4))
    sw = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=320,
                            hscrollbar_policy=Gtk.PolicyType.NEVER)
    sw.set_child(lb)
    dlg.set_extra_child(sw)
    dlg.add_response("cancel", _("Cancel"))
    dlg.add_response("ok", "OK")
    dlg.set_default_response("ok")
    dlg.set_close_response("cancel")

    def connect(done):
        dlg.connect("response", lambda d, r: done(
            [lines[row.get_index()] for row in lb.get_selected_rows()] if r == "ok" else None))
    return run_modal(lambda: dlg.present(win), connect)


def popup_menu(view, items, h, v):
    """Pop-up menu at card point (h, v). Returns (line, subitem) 1-based or None."""
    pop = Gtk.Popover(has_arrow=False)
    pop.set_parent(view)
    z = getattr(view, "zoom", 1.0)
    r = Gdk.Rectangle()
    r.x, r.y, r.width, r.height = int(h * z), int(v * z), 1, 1
    pop.set_pointing_to(r)
    pop.set_position(Gtk.PositionType.BOTTOM)
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
    rows = []
    for k, it in enumerate(items):
        if it["sep"]:
            sep = Gtk.Separator()
            row = Gtk.ListBoxRow(child=sep, activatable=False, selectable=False, can_target=False, can_focus=False, css_classes=["hcx-sep"])
            lb.append(row)
            continue
        subs = it["sub"] or [None]
        for j, sub in enumerate(subs):
            text = it["text"] if sub is None else ("%s  ›  %s" % (it["text"], sub) if j == 0 or True else sub)
            lab = Gtk.Label(label=("✓ " if it["checked"] else "") + text, xalign=0)
            if it["title"]:
                lab.add_css_class("heading")
            row = Gtk.ListBoxRow(child=lab, activatable=it["enabled"] and not it["title"])
            row._pick = (k + 1, j + 1)
            lb.append(row)
            rows.append(row)
    sw = Gtk.ScrolledWindow(propagate_natural_height=True, propagate_natural_width=True,
                            max_content_height=420, hscrollbar_policy=Gtk.PolicyType.NEVER)
    sw.set_child(lb)
    pop.set_child(sw)
    result = []

    def connect(done):
        def activated(_lb, row):
            result.append(row._pick)
            pop.popdown()
        lb.connect("row-activated", activated)
        pop.connect("closed", lambda *_: done(result[0] if result else None))
    try:
        return run_modal(pop.popup, connect)
    finally:
        GLib.idle_add(pop.unparent)


# -- floating script windows ------------------------------------------------------------------

class ScriptWindow(Gtk.Box):
    """One HyperCard external window shown as a palette over the card."""

    def __init__(self, host, w):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START,
                         valign=Gtk.Align.START, css_classes=["hcx-palette"])
        self.host = host
        self.w = w
        title = Gtk.Box(css_classes=["hcx-palette-title"])
        close = Gtk.Button(css_classes=["hcx-palette-close"], valign=Gtk.Align.CENTER,
                           tooltip_text=_("Close"))
        close.connect("clicked", lambda b: host.window_close(self.w["name"]))
        title.append(close)
        self.title_label = Gtk.Label(hexpand=True, halign=Gtk.Align.CENTER)
        title.append(self.title_label)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        title.add_controller(drag)
        self.append(title)
        style = str(w.get("style", "")).lower()
        if w["kind"] == "picture" and style in ("rect", "plain", "shadow", "dialog", "roundrect"):
            title.set_visible(False)
        self.title_bar = title
        self.body = None
        self.kind = w["kind"]
        if self.kind == "picture":
            self._build_picture()
        elif self.kind == "movie":
            vid = Gtk.Video(autoplay=True)
            vid.set_filename(w.get("file"))
            self.body = vid
            self.append(vid)
        else:
            self._build_text()
        self.update()

    def _build_picture(self):
        self.pic = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.FILL)
        self.pic.set_halign(Gtk.Align.START)
        self.pic.set_valign(Gtk.Align.START)
        png = self.w.get("png")
        self.size = (1, 1)
        if png:
            tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
            self.size = (tex.get_width(), tex.get_height())
            self.pic.set_paintable(tex)
        self.vp = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                     vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.vp.set_child(self.pic)
        click = Gtk.GestureClick()
        click.connect("pressed", self._picture_pressed)
        click.connect("released", self._picture_released)
        click.connect("stopped", self._picture_released)
        self.pic.add_controller(click)
        self.body = self.vp
        self.append(self.vp)

    def _build_text(self):
        self.tv = Gtk.TextView(editable=False, cursor_visible=False, css_classes=["hcx-textwin"],
                               left_margin=6, right_margin=6, top_margin=4, bottom_margin=4)
        self.sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.sw.set_child(self.tv)
        click = Gtk.GestureClick()
        click.connect("released", self._text_clicked)
        self.tv.add_controller(click)
        self.body = self.sw
        self.append(self.sw)
        self._css = Gtk.CssProvider()
        self.tv.get_style_context().add_provider(self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._text = None

    def zoom(self):
        return getattr(self.host.view, "zoom", 1.0)

    def update(self):
        w = self.w
        self.title_label.set_label(w["name"])
        l, t, r, b = w["rect"]
        z = self.zoom()
        width, height = max(20, int((r - l) * z)), max(12, int((b - t) * z))
        if self.kind == "picture":
            pw, ph = self.size
            self.pic.set_size_request(int(pw * z), int(ph * z))
            self.vp.set_size_request(width, height)
            sc = str(w.get("scroll", "0,0")).split(",")
            try:
                sx, sy = int(float(sc[0])), int(float(sc[1])) if len(sc) > 1 else 0
            except ValueError:
                sx = sy = 0
            GLib.idle_add(self._apply_scroll, sx * z, sy * z)
        elif self.kind == "text":
            self.sw.set_size_request(width, height)
            text = str(w.get("text", ""))
            if text != self._text:
                self._text = text
                self.tv.get_buffer().set_text(text, -1)
            wrap = str(w.get("neverwrap", w.get("dontwrap", "false"))).lower() == "true"
            self.tv.set_wrap_mode(Gtk.WrapMode.NONE if wrap else Gtk.WrapMode.WORD_CHAR)
            from .render import linux_family
            fam = linux_family(str(w.get("textfont", "Geneva")))
            try:
                size = int(float(w.get("textsize", 12) or 12))
            except ValueError:
                size = 12
            self._css.load_from_string("textview { font-family: \"%s\"; font-size: %dpx; }" %
                                       (fam, max(6, int(size * z))))
        elif self.body is not None:
            self.body.set_size_request(width, height)
        self.set_visible(bool(w.get("visible")))
        self.host.place_script_window(self)

    def _apply_scroll(self, x, y):
        self.vp.get_hadjustment().set_value(x)
        self.vp.get_vadjustment().set_value(y)
        return False

    def _picture_pressed(self, g, n, x, y):
        z = self.zoom()
        card = None
        view = getattr(self.host, "view", None)
        if view is not None:
            ok, pt = g.get_widget().compute_point(view, Graphene.Point().init(x, y))
            if ok:
                card = (int(pt.x / z), int(pt.y / z))
        self.host._mouse_down = True
        # Run the script after this event returns, so that releasing the
        # button is seen while it runs ("if the mouse is down" = click-and-hold).
        GLib.idle_add(lambda: (self.host.picture_click(self.w["name"], int(x / z), int(y / z), card), False)[1])

    def _picture_released(self, *a):
        self.host._mouse_down = False

    def _text_clicked(self, g, n, x, y):
        bx, by = self.tv.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, it = self.tv.get_iter_at_location(bx, by)
        if not ok:
            return
        line = it.get_line() + 1
        char = it.get_offset() + 1
        GLib.idle_add(lambda: (self.host.window_click(self.w["name"], char, line), False)[1])

    def _drag_begin(self, g, x, y):
        self._start = self.w["rect"]

    def _drag_update(self, g, dx, dy):
        z = self.zoom()
        l, t, r, b = self._start
        ddx, ddy = int(dx / z), int(dy / z)
        self.w["rect"] = (l + ddx, t + ddy, r + ddx, b + ddy)
        self.host.place_script_window(self)


def install_css():
    css = Gtk.CssProvider()
    css.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def font_family(name):
    return Pango.FontDescription.from_string(name).get_family()
